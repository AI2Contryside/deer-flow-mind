"""Rotate, gzip, and ship tenant_profile usage logs to OSS.

Pipeline (called by M3 trigger after a successful summarize, but works
standalone for tests):

  1. ``log.rotate_log(tenant_id)`` swaps the live jsonl for a timestamped
     archive sibling and creates a fresh empty live file.
  2. We gzip the rotated file in place, compute SHA-256, take the first 8
     hex chars as the content-addressed suffix, and rename to
     ``usage_log-<stamp>-<sha8>.jsonl.gz``.
  3. Best-effort upload to OSS via the project's ``Storage`` Protocol
     (bucket: ``trademind-chat-session``; key per ``keys.tenant_profile_usage_log_key``).
  4. On failure: enqueue in ``meta.pending_oss_uploads`` for the next run.
     On success: drop any matching pending entry.
  5. Local copy is kept either way (decision 6).

Also exposes ``retry_pending_uploads`` — call this before archiving the
current rotation so older failures get reattempted under the same call.

Best-effort throughout: every failure path returns False / None and only
writes a warning log (decision 1). The caller MUST be able to handle "we
didn't ship anything" without breaking the user-facing flow.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.agents.tenant_profile import log as log_module
from src.agents.tenant_profile import meta as meta_module
from src.agents.tenant_profile.meta import Meta, PendingUpload
from src.storage import Storage, get_default, tenant_profile_usage_log_key

logger = logging.getLogger(__name__)

# Per §7 archive.max_retry_attempts. Beyond this we stop trying — the file
# stays on disk and the operator gets to decide what to do next.
DEFAULT_MAX_RETRY_ATTEMPTS = 3


@dataclass(frozen=True)
class ArchiveResult:
    """What ``archive_log`` produced for the most recent rotation."""

    rotated: bool
    local_path: Path | None
    bucket: str | None
    key: str | None
    uploaded: bool
    pending: bool  # ``True`` if the file is sitting in pending_oss_uploads


def archive_log(
    tenant_id: str,
    *,
    storage: Storage | None = None,
    max_retry_attempts: int = DEFAULT_MAX_RETRY_ATTEMPTS,
) -> ArchiveResult:
    """Rotate, compress, upload — best effort.

    Returns an ``ArchiveResult`` describing what landed where. ``storage``
    is injected for tests; production callers leave it ``None`` and we pull
    the project default lazily (so unconfigured OSS doesn't crash callers).
    """
    # First, give pending uploads a shot. Doing this before the new rotation
    # means transient OSS outages drain naturally; doing it after would let
    # the queue grow without bound.
    retry_pending_uploads(tenant_id, storage=storage, max_retry_attempts=max_retry_attempts)

    rotated = log_module.rotate_log(tenant_id)
    if rotated is None:
        return ArchiveResult(rotated=False, local_path=None, bucket=None, key=None, uploaded=False, pending=False)

    try:
        compressed_path, sha8, size_bytes = _compress_and_hash(rotated)
    except OSError as exc:
        logger.warning("tenant_profile: failed to compress rotated log %s: %s", rotated, exc)
        return ArchiveResult(rotated=True, local_path=rotated, bucket=None, key=None, uploaded=False, pending=False)

    bucket, key = _build_oss_target(tenant_id, sha8)
    uploaded = _try_upload(
        tenant_id=tenant_id,
        local_path=compressed_path,
        bucket=bucket,
        key=key,
        sha8=sha8,
        size_bytes=size_bytes,
        storage=storage,
        max_retry_attempts=max_retry_attempts,
    )

    return ArchiveResult(
        rotated=True,
        local_path=compressed_path,
        bucket=bucket,
        key=key,
        uploaded=uploaded,
        pending=not uploaded,
    )


def retry_pending_uploads(
    tenant_id: str,
    *,
    storage: Storage | None = None,
    max_retry_attempts: int = DEFAULT_MAX_RETRY_ATTEMPTS,
) -> int:
    """Re-attempt every entry in ``meta.pending_oss_uploads``.

    Returns the number of successful uploads in this pass. Entries that have
    already exhausted ``max_retry_attempts`` are left untouched (and counted
    in neither success nor failure) — the operator can flush them manually
    once the underlying issue (bad credentials, deleted bucket, …) is fixed.
    """
    state = meta_module.read_meta(tenant_id)
    if not state.pending_oss_uploads:
        return 0

    successes = 0
    new_pending: list[PendingUpload] = []
    for entry in state.pending_oss_uploads:
        if entry.attempts >= max_retry_attempts:
            new_pending.append(entry)
            continue

        local = Path(entry.local_path)
        if not local.exists():
            # File got cleaned up out from under us. Drop the entry; nothing
            # we can do with a missing local copy.
            logger.warning(
                "tenant_profile: dropping pending upload for tenant %r — local file %s missing",
                tenant_id,
                entry.local_path,
            )
            continue

        ok, err = _do_upload(local, entry.bucket, entry.key, storage=storage)
        if ok:
            successes += 1
            continue
        new_pending.append(_bump(entry, err))

    # Always write back when we processed at least one entry — even when
    # length didn't change we bumped attempt counters and want them durable.
    meta_module.mutate(tenant_id, lambda m: _replace_pending(m, new_pending))
    return successes


# ── Internals ──────────────────────────────────────────────────────────────


def _compress_and_hash(rotated: Path) -> tuple[Path, str, int]:
    """Gzip ``rotated`` in place, return (compressed_path, sha8, size_bytes).

    The gzipped file lives next to the original under a name that includes
    the content sha8 — so retries land on the same OSS key. The original
    uncompressed jsonl is removed once the gz is successfully written.
    """
    gz_tmp = rotated.with_suffix(rotated.suffix + ".gz.tmp")
    sha = hashlib.sha256()
    with open(rotated, "rb") as src, gzip.open(gz_tmp, "wb") as dst:
        for chunk in iter(lambda: src.read(65536), b""):
            sha.update(chunk)
            dst.write(chunk)
    sha8 = sha.hexdigest()[:8]

    # Final filename: usage_log-<stamp>-<rand>.jsonl → usage_log-<stamp>-<sha8>.jsonl.gz
    # We replace the random rotation suffix with the content-addressed sha8
    # so two attempts at the same content collide on the same archive name.
    stem = rotated.stem  # usage_log-<stamp>-<rand>
    if stem.startswith("usage_log-"):
        head, _, _ = stem.rpartition("-")
        final_stem = f"{head}-{sha8}"
    else:
        final_stem = f"{stem}-{sha8}"
    final = rotated.with_name(f"{final_stem}.jsonl.gz")
    gz_tmp.replace(final)
    rotated.unlink(missing_ok=True)
    size_bytes = final.stat().st_size
    return final, sha8, size_bytes


def _build_oss_target(tenant_id: str, sha8: str) -> tuple[str, str]:
    date = datetime.now(UTC).strftime("%Y%m%d")
    key = tenant_profile_usage_log_key(tenant_id, date, sha8)
    bucket = _resolve_chat_bucket()
    return bucket, key


def _resolve_chat_bucket() -> str:
    """Return the chat bucket name. Falls back to the documented default
    when OSS isn't configured (so we have a stable string to record in
    pending_oss_uploads even if upload itself can't proceed).
    """
    try:
        cfg = get_default().config
        return cfg.chat_bucket
    except Exception:
        return "trademind-chat-session"


def _try_upload(
    *,
    tenant_id: str,
    local_path: Path,
    bucket: str,
    key: str,
    sha8: str,
    size_bytes: int,
    storage: Storage | None,
    max_retry_attempts: int,
) -> bool:
    ok, err = _do_upload(local_path, bucket, key, storage=storage)
    if ok:
        return True

    # Stash for retry next round.
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    pending = PendingUpload(
        local_path=str(local_path),
        bucket=bucket,
        key=key,
        sha8=sha8,
        size_bytes=size_bytes,
        attempts=1,
        last_error=err,
        first_attempt_at=now,
        last_attempt_at=now,
    )
    meta_module.mutate(tenant_id, lambda m: _enqueue_pending(m, pending))
    if pending.attempts >= max_retry_attempts:
        logger.warning(
            "tenant_profile: upload to %s/%s exhausted retries for tenant %r; keeping local copy at %s",
            bucket,
            key,
            tenant_id,
            local_path,
        )
    return False


def _do_upload(local_path: Path, bucket: str, key: str, *, storage: Storage | None) -> tuple[bool, str | None]:
    """Single upload attempt. Returns (ok, error_message).

    Acquires the storage lazily so an unconfigured project (no oss block)
    just records "not configured" and we move on.
    """
    try:
        client = storage if storage is not None else get_default()
    except Exception as exc:
        return False, f"storage unavailable: {exc}"

    try:
        with open(local_path, "rb") as fh:
            client.put_object(bucket, key, fh, content_type="application/gzip")
        return True, None
    except FileNotFoundError as exc:
        return False, f"local file gone: {exc}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _bump(entry: PendingUpload, err: str | None) -> PendingUpload:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return PendingUpload(
        local_path=entry.local_path,
        bucket=entry.bucket,
        key=entry.key,
        sha8=entry.sha8,
        size_bytes=entry.size_bytes,
        attempts=entry.attempts + 1,
        last_error=err,
        first_attempt_at=entry.first_attempt_at,
        last_attempt_at=now,
    )


def _enqueue_pending(meta: Meta, entry: PendingUpload) -> Meta:
    # Replace any pre-existing entry with the same key so retries don't
    # double-record the same file.
    meta.pending_oss_uploads = [p for p in meta.pending_oss_uploads if p.key != entry.key]
    meta.pending_oss_uploads.append(entry)
    return meta


def _replace_pending(meta: Meta, new_list: list[PendingUpload]) -> Meta:
    meta.pending_oss_uploads = list(new_list)
    return meta


# ── Test helpers ───────────────────────────────────────────────────────────


def _delete_local_archive_for_tests(local_path: Path) -> None:
    """Used by tests that want to simulate a vanished local file."""
    if local_path.exists():
        local_path.unlink()
    parent = local_path.parent
    if parent.exists() and not any(parent.iterdir()):
        shutil.rmtree(parent)

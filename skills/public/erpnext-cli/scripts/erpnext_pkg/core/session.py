"""Persistent session state for the CLI.

A session holds everything the REPL and one-shot commands need between
invocations:

  - **Connection**: site URL + credentials (API token or user/pass)
  - **Context defaults**: current company, warehouse, customer, supplier,
    price list, currency — so agents don't have to re-pass them on every
    command
  - **Breadcrumbs**: the last N business-flow steps (for `history` /
    `replay` and debugging)

The file is JSON, saved under ``~/.cli-anything-erpnext/session.json`` by
default. Writes use an atomic rename pattern so a crash mid-write can't
corrupt the file.

Credentials at rest
-------------------
Passwords/API secrets are stored **only if the user explicitly opts in**
via ``--save-credentials``. Otherwise the session file stores the URL,
api_key, username — but NOT the secret/password — and every run must read
the secret from env or prompt.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .client import FrappeClient
from .errors import AuthError


DEFAULT_SESSION_DIR = Path(os.environ.get(
    "CLI_ANYTHING_ERPNEXT_HOME",
    str(Path.home() / ".cli-anything-erpnext"),
))
SESSION_FILE = DEFAULT_SESSION_DIR / "session.json"
HISTORY_MAX = 50


@dataclass
class Context:
    """Default values the CLI fills in when a command omits them.

    Every ERPNext transaction requires a Company (and usually a Warehouse
    and Currency). Rather than force agents to pass them on every call,
    the session remembers the last-used values.
    """

    company: str | None = None
    default_warehouse: str | None = None
    default_customer: str | None = None
    default_supplier: str | None = None
    default_price_list: str | None = None
    default_currency: str | None = None
    default_cost_center: str | None = None


@dataclass
class Session:
    """Persisted CLI state. One per user."""

    url: str | None = None
    api_key: str | None = None
    api_secret: str | None = None  # only persisted if save_credentials=True
    username: str | None = None
    password: str | None = None    # only persisted if save_credentials=True
    save_credentials: bool = False
    verify_ssl: bool = True
    context: Context = field(default_factory=Context)
    history: list[dict] = field(default_factory=list)
    dirty: bool = False

    # ── Persistence ───────────────────────────────────────────────────

    @classmethod
    def load(cls, path: Path = SESSION_FILE) -> "Session":
        """Load session from disk. Returns a fresh Session if file is missing."""
        if not path.is_file():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        ctx_raw = raw.pop("context", {}) or {}
        raw.pop("dirty", None)
        return cls(context=Context(**ctx_raw), **raw)

    def save(self, path: Path = SESSION_FILE) -> Path:
        """Atomically save session to disk.

        Uses write-to-temp + rename so a crash can't leave a partial file.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data.pop("dirty", None)
        if not self.save_credentials:
            data["api_secret"] = None
            data["password"] = None
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=".session.", suffix=".json.tmp", dir=path.parent,
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        self.dirty = False
        return path

    # ── Connection ────────────────────────────────────────────────────

    def client(self, tenant_id: str | None = None) -> FrappeClient:
        """Build a live ``FrappeClient`` from this session's credentials.

        Env vars override session values so CI and agents can inject
        creds without touching the session file. ``tenant_id`` follows the
        same rule: explicit argument wins, then ``ERPNEXT_TENANT_ID`` env
        var. Tenant is deliberately not stored in the on-disk session file —
        it is a per-invocation scope, not a per-user setting.
        """
        url = os.environ.get("ERPNEXT_URL") or self.url
        if not url:
            raise AuthError(
                "No ERPNext URL configured. Run: "
                "cli-anything-erpnext session login --url https://... "
                "--api-key ... --api-secret ..."
            )
        api_key = os.environ.get("ERPNEXT_API_KEY") or self.api_key
        api_secret = os.environ.get("ERPNEXT_API_SECRET") or self.api_secret
        username = os.environ.get("ERPNEXT_USERNAME") or self.username
        password = os.environ.get("ERPNEXT_PASSWORD") or self.password
        tenant = tenant_id or os.environ.get("ERPNEXT_TENANT_ID") or None
        verify_ssl = os.environ.get("ERPNEXT_VERIFY_SSL", "1") != "0" and self.verify_ssl
        c = FrappeClient(
            url,
            api_key=api_key, api_secret=api_secret,
            username=username, password=password,
            tenant_id=tenant,
            verify_ssl=verify_ssl,
        )
        if not (api_key and api_secret) and username and password:
            c.login()
        return c

    # ── Mutators (immutable-style: return a new Session) ──────────────

    def with_context(self, **updates) -> "Session":
        """Return a new Session with updated context fields.

        Follows the global immutability rule — never mutates self.
        """
        new_ctx = Context(**{**asdict(self.context), **updates})
        cloned = Session(
            url=self.url,
            api_key=self.api_key,
            api_secret=self.api_secret,
            username=self.username,
            password=self.password,
            save_credentials=self.save_credentials,
            verify_ssl=self.verify_ssl,
            context=new_ctx,
            history=list(self.history),
            dirty=True,
        )
        return cloned

    def log(self, event: str, payload: dict | None = None) -> "Session":
        """Append a history entry. Returns a new Session."""
        import datetime as _dt
        entry = {
            "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "event": event,
            "payload": payload or {},
        }
        new_history = (self.history + [entry])[-HISTORY_MAX:]
        cloned = Session(
            url=self.url,
            api_key=self.api_key,
            api_secret=self.api_secret,
            username=self.username,
            password=self.password,
            save_credentials=self.save_credentials,
            verify_ssl=self.verify_ssl,
            context=Context(**asdict(self.context)),
            history=new_history,
            dirty=True,
        )
        return cloned

    def redacted(self) -> dict:
        """Safe dict for printing — no secrets.

        When credentials come from the environment (ERPNEXT_URL /
        ERPNEXT_API_KEY / ERPNEXT_API_SECRET / ERPNEXT_USERNAME), reflect them
        here so `session status` matches what the actual API-call path sees.
        Without this, an agent running `session status` would see null fields
        and wrongly conclude the user hasn't authenticated — even though every
        subsequent command would succeed via the env-var fallback in
        `effective_*()` / `_env_session_from_runtime()`.
        """
        d = asdict(self)
        env_url = os.environ.get("ERPNEXT_URL")
        env_api_key = os.environ.get("ERPNEXT_API_KEY")
        env_api_secret = os.environ.get("ERPNEXT_API_SECRET")
        env_username = os.environ.get("ERPNEXT_USERNAME")
        if not d.get("url") and env_url:
            d["url"] = env_url
        if not d.get("api_key") and env_api_key:
            d["api_key"] = env_api_key
        if not d.get("api_secret") and env_api_secret:
            d["api_secret"] = env_api_secret
        if not d.get("username") and env_username:
            d["username"] = env_username
        if d.get("api_secret"):
            d["api_secret"] = "***"
        if d.get("password"):
            d["password"] = "***"
        d.pop("dirty", None)
        return d

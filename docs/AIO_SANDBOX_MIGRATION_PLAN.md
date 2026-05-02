# AIO Sandbox Migration Plan

> Status: **Drafted, not started.** This file captures the analysis from the
> 2026-04-30 design pass so it can be picked up later without re-deriving it.
> See "Progress" at the bottom for what has and hasn't been delivered.

## 1. Goal

Switch `deer-flow-mind` from `LocalSandboxProvider` (default today) to
`AioSandboxProvider` so agent-executed bash / file ops run inside a
container-isolated environment instead of directly on the gateway/langgraph
host process.

## 2. What's already in the repo

The implementation is mostly already there — this migration is **enabling +
hardening**, not building from scratch.

- `src/community/aio_sandbox/aio_sandbox.py` — `AioSandbox` (HTTP client over `agent_sandbox`)
- `src/community/aio_sandbox/aio_sandbox_provider.py` — full provider with in-process cache, warm pool, idle checker, replicas cap, cross-process file lock, deterministic `sandbox_id`
- `src/community/aio_sandbox/local_backend.py` — Docker / Apple Container local backend
- `src/community/aio_sandbox/remote_backend.py` — k8s / provisioner remote backend
- `docker/provisioner/` — FastAPI provisioner service (k3s Pod-per-sandbox)
- `docker/docker-compose.yaml` — DooD wiring already present (`DEER_FLOW_HOST_BASE_DIR`, `DEER_FLOW_HOST_SKILLS_PATH`, `DEER_FLOW_SANDBOX_HOST=host.docker.internal`, `/var/run/docker.sock` mount)
- `agent-sandbox>=0.0.19` already in `pyproject.toml`
- `scripts/docker.sh::detect_sandbox_mode` auto-selects local / aio / provisioner from `config.yaml`
- Regression tests: `tests/test_docker_sandbox_mode_detection.py`, `tests/test_provisioner_kubeconfig.py`

`config.yaml` ships with `sandbox.use: src.sandbox.local:LocalSandboxProvider`
as the default; the AIO config blocks are commented in place as Options 2 and 3.

## 3. Implementation gaps (AIO vs Local) — must fix before flipping the default

| # | Gap | Risk | Fix |
|---|-----|------|-----|
| 3.1 | **Credential leakage** — `LocalSandbox.execute_command` strips `_FORBIDDEN_INHERITED_ENV` (ERPNEXT_URL/API_KEY/API_SECRET/TENANT_ID) — the thread `5093394f-…` security fix. `AioSandboxProvider._resolve_env_vars` accepts `environment.KEY: $HOST_VAR` shape; if ops adds `ERPNEXT_API_KEY: $ERPNEXT_API_KEY` to `config.yaml`, every sandbox container gets the credential pre-loaded and `echo $ERPNEXT_API_KEY` returns it. | **High** (security) | Add ERPNEXT_* deny-list inside `_resolve_env_vars` (or schema-level reject); copy `tests/test_local_sandbox_credential_isolation.py` to an AIO version using a mocked `agent_sandbox` client. |
| 3.2 | **Error swallowing** — `AioSandbox.read_file` returns `f"Error: {e}"` instead of raising. Tool-layer `except FileNotFoundError` / `except PermissionError` never trigger; LLM sees generic "Error: …" instead of the formatted "Error: File not found: <path>", which can change retry behavior. | Medium (behavior drift) | Raise real `FileNotFoundError` / `PermissionError`; let `tools.read_file_tool` format. |
| 3.3 | **`list_dir` output shape differs** — Local renders tree (matches `ls_tool` docstring "tree format, max 2 levels"); AIO uses `find -maxdepth … \| head -500`, flat absolute paths. | Medium (prompt drift) | Render tree inside `AioSandbox.list_dir`, or push tree rendering down into `ls_tool`. |
| 3.4 | **Non-atomic append** — `AioSandbox.write_file(append=True)` does `read_file` → concat → `write_file`. Two RPCs are not atomic; large files re-transfer their entire body each append. | Low–Medium | Check whether `agent_sandbox` 0.0.19 exposes `append`/`offset`; if not, fall back to `shell.exec_command "printf '%s' … >> file"`. |
| 3.5 | **Binary write encoding** — AIO assumes `agent_sandbox.file.write_file(content, encoding="base64")` works. Untested. | Low | Smoke test in the build (already added: `python3 -c "import openpyxl, …"` in the sandbox Dockerfile) or with a real container in CI. |
| 3.6 | **`tools.is_local_sandbox()` semantics** — actually means "should the tool layer translate `/mnt/user-data` paths," but the name says "is local sandbox." Works correctly today but invites future bugs. | Low | Rename to `tool_layer_should_translate_paths()`, or document. |

## 4. Deployment / runtime concerns

### 4.1 Devbox (DooD)

- gateway/langgraph run as containers; they `docker run` a *third* container via the host daemon (Docker socket already mounted)
- Mount sources must be **host absolute paths** — `DEER_FLOW_HOST_BASE_DIR` / `DEER_FLOW_HOST_SKILLS_PATH` are exported by `scripts/deploy.sh`; the relative-path fallbacks in `docker-compose.yaml` would be rejected by the host daemon
- `extra_hosts: host-gateway` is required for sandbox → langgraph callbacks via `host.docker.internal` (Linux Docker 20.10+; devbox runs 29.3.1, fine)
- Each sandbox occupies one host port (defaults from 8080 up; `replicas=3` default). Confirm port range is free on devbox.

### 4.2 Sandbox containers are **not** managed by docker-compose

`AioSandboxProvider` starts/stops them at runtime via the host daemon. Implications:
- `docker compose restart` won't recycle sandbox containers — they're owned by the provider
- After bumping the sandbox image tag, dangling old-tag containers must be cleaned up: `docker ps -a --filter name=deer-flow-sandbox- -q | xargs -r docker rm -f`
- Adding the cleanup hook to `scripts/deploy/deploy-deerflow.sh` is part of this migration's deploy work

### 4.3 Multi-pod / horizontal scaling

The cross-process file lock in `AioSandboxProvider._discover_or_create_with_lock`
uses local fs (`paths.thread_dir(thread_id)/{sandbox_id}.lock`). If
gateway/langgraph ever scales to multiple pods, **must switch to
`provisioner_url` + `RemoteSandboxBackend`** (k3s Pod-per-sandbox is already
implemented). Single-pod for now; document the transition criterion.

## 5. Sandbox image strategy

**Decision: thin overlay on `agent-infra/all-in-one-sandbox`.** Drafted under
`deer-flow-mind/docker/sandbox/` (Dockerfile + requirements.lock + README).

Rationale and what's intentionally out:
- **Not** rebuilt from scratch — would mean reimplementing the `agent_sandbox`
  HTTP server (`/v1/sandbox/shell/exec`, `/v1/file/read|write`, …)
- **Not** baking docling — already runs in the gateway container, emits
  `<file>.docling.json` sidecars; sandbox agents read sidecars not originals
- **Not** baking playwright / heavy ML wheels
- **Not** baking credentials — per-call ERPNEXT_* / OSS / etc. flow via
  `tools._extract_erpnext_env` and `AioSandbox` shell prefix

**What it does add:**
- byted internal proxy on every network RUN (mirrors `backend/Dockerfile`)
- system tools: `jq git unzip ffmpeg poppler-utils libreoffice-{writer,calc,impress}`
- CJK + emoji + DejaVu fonts (chart / ppt / image skills render Chinese)
- Python file-processing stack baked in (Excel / Word / PDF / PPT / images / dataframe / charts) — see `requirements.lock` and `[dependency-groups.sandbox-fallback]` in `backend/pyproject.toml`

**Distribution path: TBD.** Two options laid out in the README; depends on
whether a private registry is reachable from devbox.

## 6. Testing

Existing sandbox tests are mostly Local-specific. To add:

- `test_aio_sandbox_credential_isolation.py` — mock `agent_sandbox` client, assert `_resolve_env_vars` does not inject ERPNEXT_*
- `test_aio_sandbox_provider_lifecycle.py` — `acquire / release / destroy / shutdown`, warm pool reclaim, replicas eviction, idle cleanup, file-lock path. **No coverage today.**
- `test_aio_sandbox_error_propagation.py` — `read_file` failures raise typed exceptions (gates 3.2)
- `@pytest.mark.aio_integration` — end-to-end with a real container; CI on a runner with docker socket; local `make test` skips by default

## 7. Open questions to resolve before flipping the default

1. Can devbox `docker pull enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest`? (volcengine source, internal network reachability is uncertain)
2. What's actually inside the upstream image (Python version? Node version? pre-installed packages?) — tells us how thin the overlay can be
3. Is there an internal Docker registry reachable from devbox to push/pull? Determines image distribution path A vs B (see `docker/sandbox/README.md`)
4. Resource budget for a sandbox container (CPU / memory / disk) and the `replicas` cap given expected concurrent active threads on devbox

## 8. Effort estimate

| Module | Effort |
|---|---|
| §3.1–3.4 (impl + unit tests) | 1–2 person-days |
| §4.1 (DooD verification + deploy doc) | 0.5 |
| §4.2 (cleanup hook in deploy-deerflow.sh) | 0.5 |
| §5 (overlay image + lockfile + build CI) | 1–2 |
| Devbox dry-run + image distribution rollout | 0.5–1 |
| Monitoring + dangling-container cleanup | 0.5 |
| §6 (lifecycle tests) | 1 |
| Canary + behavior diff vs Local | 1–2 |
| **Total** | **6–10 person-days** |

## 9. Recommended landing order

1. **Land §3.1–3.4 fixes + unit tests first** (no config change, pure Python PR; reverts trivially)
2. Local macOS smoke test with `config.yaml` flipped to AIO
3. Build the overlay image, dry pull / build on devbox to answer §7 questions
4. Push or `docker save` to devbox; `--force-rebuild` deploy-deerflow with new `sandbox.use`
5. Canary: route a subset of threads (by env flag or tenant_id) to AIO; diff against Local
6. Flip default for everyone; **keep** `LocalSandboxProvider` as fallback (don't delete `src/sandbox/local/`)
7. Multi-pod scaling: switch to `provisioner_url` when needed

## 10. Progress (as of 2026-04-30)

Delivered as drafts, not yet wired up:

- [x] `deer-flow-mind/docker/sandbox/Dockerfile` — overlay image draft
- [x] `deer-flow-mind/docker/sandbox/requirements.lock` — Python file-processing pins
- [x] `deer-flow-mind/docker/sandbox/README.md` — build + distribution + verify steps
- [x] `[dependency-groups.sandbox-fallback]` in `backend/pyproject.toml` — same package set declared as a removable group, so LocalSandbox-mode `bash` tool can `import openpyxl/docx/pypdf/…` without the agent re-`pip install`-ing on every cold start. Bracketed by `# === SANDBOX FALLBACK DEPS …` markers for grep-based removal once AIO is the only mode.
- [x] This plan file

Not yet done (deferred to next push):

- [ ] §3 fixes (credential deny-list, error propagation, list_dir tree, append atomicity)
- [ ] §6 new tests
- [ ] Image build CI / publish path
- [ ] Devbox dry-run answers to §7
- [ ] `config.yaml` flip
- [ ] Compose `sandbox-warmup` profile + deploy-script cleanup hook
- [ ] CLAUDE.md update (default-sandbox callout, deploy-script note about `--force-rebuild` on first switch)

## 11. Files touched in the draft phase

```
deer-flow-mind/
├── docker/sandbox/Dockerfile
├── docker/sandbox/requirements.lock
├── docker/sandbox/README.md
├── docs/AIO_SANDBOX_MIGRATION_PLAN.md     # this file
└── backend/pyproject.toml                  # +[dependency-groups.sandbox-fallback]
```

To revert the draft phase entirely: `rm -rf deer-flow-mind/docker/sandbox/
deer-flow-mind/docs/AIO_SANDBOX_MIGRATION_PLAN.md` and remove the marked block
in `backend/pyproject.toml`.

# Copilot Onboarding Instructions for DeerFlow

Use this file as the default operating guide for this repository. Follow it first, and only search the codebase when this file is incomplete or incorrect.

## 1) Repository Summary

DeerFlow is a backend "super agent harness".

- Backend: Python 3.12, LangGraph + FastAPI gateway, sandbox/tool system, memory, MCP integration.
- Local dev entrypoint: root `Makefile` starts backend + nginx on `http://localhost:2026`.
- Docker dev entrypoint: `make docker-*` (mode-aware provisioner startup from `config.yaml`).

Current repo footprint is medium (backend service, docker stack, skills library, docs).

## 2) Runtime and Toolchain Requirements

Validated in this repo on macOS:

- Python `>=3.12` (CI uses `3.12`)
- `uv` (validated with `0.7.20`)
- `nginx` (required for `make dev` unified local endpoint)

Always run from repo root unless a command explicitly says otherwise.

## 3) Build/Test/Lint/Run - Verified Command Sequences

These were executed and validated in this repository.

### A. Bootstrap and install

1. Check prerequisites:

```bash
make check
```

Observed: passes when required tools are installed.

2. Install dependencies:

```bash
make install
```

### B. Backend CI-equivalent validation

Run from `backend/`:

```bash
make lint
make test
```

Validated results:

- `make lint`: pass (`ruff check .`)
- `make test`: pass

CI parity:

- `.github/workflows/backend-unit-tests.yml` runs on pull requests.
- CI executes `uv sync --group dev`, then `make lint`, then `make test` in `backend/`.

### C. Run locally (all services)

From root:

```bash
make dev
```

Behavior:

- Stops existing local services first.
- Starts LangGraph (`2024`), Gateway (`8001`), nginx (`2026`).
- Unified API endpoint: `http://localhost:2026`.
- Logs: `logs/langgraph.log`, `logs/gateway.log`, `logs/nginx.log`.

Stop services:

```bash
make stop
```

If tool sessions/timeouts interrupt `make dev`, run `make stop` again to ensure cleanup.

### D. Config bootstrap

From root:

```bash
make config
```

Important behavior:

- This intentionally aborts if `config.yaml` (or `config.yml`/`configure.yml`) already exists.
- Use `make config` only for first-time setup in a clean clone.

## 4) Command Order That Minimizes Failures

Use this exact order for local code changes:

1. `make check`
2. `make install`
3. Backend checks: `cd backend && make lint && make test`

Always run backend lint/tests before opening PRs because that is what CI enforces.

## 5) Project Layout and Architecture (High-Value Paths)

Root-level orchestration and config:

- `Makefile` - main local/dev/docker command entrypoints
- `config.example.yaml` - primary app config template
- `config.yaml` - local active config (gitignored)
- `docker/docker-compose-dev.yaml` - Docker dev topology
- `.github/workflows/backend-unit-tests.yml` - PR validation workflow

Backend core:

- `backend/src/agents/` - lead agent, middleware chain, memory
- `backend/src/gateway/` - FastAPI gateway API
- `backend/src/sandbox/` - sandbox provider + tool wrappers
- `backend/src/subagents/` - subagent registry/execution
- `backend/src/mcp/` - MCP integration
- `backend/langgraph.json` - graph entrypoint (`src.agents:make_lead_agent`)
- `backend/pyproject.toml` - Python deps and `requires-python`
- `backend/ruff.toml` - lint/format policy
- `backend/tests/` - backend unit and integration-like tests

Skills and assets:

- `skills/public/` - built-in skill packs loaded by agent runtime

## 6) Pre-Checkin / Validation Expectations

Before submitting changes, run at minimum:

- Backend: `cd backend && make lint && make test`

If touching orchestration/config (`Makefile`, `docker/*`, `config*.yaml`), also run `make dev` and verify the services start.

## 7) Non-Obvious Dependencies and Gotchas

- `make config` is non-idempotent by design when config already exists.
- `make dev` includes process cleanup and can emit shutdown logs/noise if interrupted; this is expected.

## 8) Root Inventory (quick reference)

Important root entries:

- `.github/`
- `backend/`
- `docker/`
- `skills/`
- `scripts/`
- `docs/`
- `README.md`
- `CONTRIBUTING.md`
- `Makefile`
- `config.example.yaml`
- `extensions_config.example.json`

## 9) Instruction Priority

Trust this onboarding guide first.

Only do broad repo searches (`grep/find/code search`) when:

- you need file-level implementation details not listed here,
- a command here fails and you need updated replacement behavior,
- or CI/workflow definitions have changed since this file was written.

# erpnext-cli (DeerFlow native skill)

A business-process CLI for ERPNext, embedded as a self-contained DeerFlow
skill under `skills/public/erpnext-cli/`. Ported from the upstream
standalone package `cli-anything-erpnext` (see
`trademind-harness/cli_anything/erpnext/`).

## Agent quick reference

See [SKILL.md](./SKILL.md) for the canonical agent contract (JSON
envelope, typed errors, command catalog). Below is a human-oriented
summary.

## Invocation

Inside the DeerFlow sandbox, the skill is mounted at
`/mnt/skills/public/erpnext-cli/`. Call it via the launcher:

```bash
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json <group> <command> [options]
```

Outside the sandbox (for local development / debugging):

```bash
python deer-flow-mind/skills/public/erpnext-cli/scripts/erpnext.py --help
```

## Authentication

Token auth is preferred. Either cache once to the session file, or inject
via env on every call:

```bash
# Option A — persistent session (written to ~/.cli-anything-erpnext/session.json, mode 0600)
python .../erpnext.py session login \
    --url https://erp.example.com \
    --api-key K --api-secret S --save-credentials

# Option B — env vars (never touch disk, ideal for CI/agents)
export ERPNEXT_URL=https://erp.example.com
export ERPNEXT_API_KEY=K
export ERPNEXT_API_SECRET=S
python .../erpnext.py --json session status
```

Env vars **override** the session file, so a cached session can be
overridden per-run for multi-tenant usage.

## Example: end-to-end order-to-cash

```bash
python .../erpnext.py --json selling order-to-cash \
    --customer "Alice Ltd" \
    --item "WIDGET-001:10:99.50" \
    --mode-of-payment "Cash"
```

This single call:

1. Inserts and submits a Sales Order
2. Chains via `erpnext.selling.doctype.sales_order.sales_order.make_delivery_note`
3. Chains via `erpnext.stock.doctype.delivery_note.delivery_note.make_sales_invoice`
4. Chains via `erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry`
5. Returns the names of all four submitted documents

## Why a "skill" and not just a CLI?

The upstream `cli-anything-erpnext` package ships as a
`pip install`-able console script. That requires a clean Python
environment inside the sandbox. By embedding as a DeerFlow skill:

- **Zero install**: the sources live under `skills/public/`, mounted at
  `/mnt/skills/public/erpnext-cli/` in the sandbox.
- **Self-contained**: the launcher (`scripts/erpnext.py`) puts the
  sibling `erpnext_pkg/` on `sys.path` — no namespace-package plumbing.
- **Discoverable**: DeerFlow's `load_skills()` registers it in the agent
  system prompt automatically (toggle via `extensions_config.json`).

## Architecture

```
erpnext-cli/
├── SKILL.md              # agent contract (frontmatter + catalog)
├── README.md             # this file
├── scripts/
│   ├── erpnext.py        # thin launcher — mutates sys.path, calls cli.main
│   └── erpnext_pkg/
│       ├── cli.py        # Click root group (formerly erpnext_cli.py upstream)
│       ├── core/         # FrappeClient (REST + RPC), Session, typed errors
│       ├── domains/      # business-process logic (pure, no Click)
│       ├── cli_groups/   # Click wrappers per domain
│       └── utils/        # REPL theming
└── references/
    └── ERPNEXT.md        # catalog of ERPNext make_* chain methods
```

### Package layering (invariant)

- `cli_groups/*` handle **Click parsing + session defaults + JSON envelope
  emission**. They import from `domains/`, never from `core/` directly.
- `domains/*` handle **business orchestration** — chaining `make_*` calls
  via `FrappeClient`. They never touch Click or stdout.
- `core/*` handle **transport + state + errors**. Nothing above imports
  back into `core/` outside of its public surface (`FrappeClient`,
  `Session`, error classes).

Keep this split when extending.

## Error contract

Agents parse `error.error` (the class name) to decide what to do:

| Class | Meaning | Agent action |
|-------|---------|--------------|
| `AuthError` | Session expired / bad token | Re-inject creds or `session login` |
| `NotFoundError` | DocType / record doesn't exist | Check input names |
| `ValidationError` | Frappe rejected the payload | Fix fields, retry |
| `PermissionError_` | Missing role | Use a different key |
| `WorkflowError` | Precondition failed | Read the message, adjust inputs |
| `ServerError` | Frappe 5xx | Backoff + retry |
| `ERPNextError` | Unclassified | Surface to user |

## Syncing from upstream

This skill is a **ported copy** of
`trademind-harness/cli_anything/erpnext/`. When the upstream package
changes, re-sync by:

1. `cp -R <harness>/cli_anything/erpnext/{core,domains,cli_groups,utils}/. scripts/erpnext_pkg/<same>/`
2. `cp <harness>/cli_anything/erpnext/__init__.py scripts/erpnext_pkg/__init__.py`
3. `cp <harness>/cli_anything/erpnext/erpnext_cli.py scripts/erpnext_pkg/cli.py`
4. Re-run the smoke test: `python scripts/erpnext.py --version`

No source file needs editing — the package uses **relative imports only**
(`from . import …`, `from ..core.client import …`), so the rename from
`cli_anything.erpnext` → `erpnext_pkg` is a pure directory move.

## Tests

Tests are **not** ported in this skill. The upstream test suite lives at
`trademind-harness/cli_anything/erpnext/tests/` and uses an in-memory
`MockFrappe` simulator. Run them from the upstream repo:

```bash
cd trademind-harness
pip install -e ".[test]"
pytest cli_anything/erpnext/tests/ -v --tb=short
```

When the upstream tests pass, re-sync this skill via the steps above.

## License

Apache-2.0 — inherited from the upstream `cli-anything-erpnext` package.

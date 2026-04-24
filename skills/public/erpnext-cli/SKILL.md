---
name: erpnext-cli
description: Use this skill to drive a running ERPNext / Frappe site end-to-end — quote-to-cash, procure-to-pay, material transfer, journal entries, BOM → work order → finish, lead → customer. Every domain command wraps ERPNext's native `make_*` chain methods, returns a typed JSON envelope (`{ok, data, error}`), and submits documents by default (matching what the GUI's "Create → X" button produces). Authenticate once via `session login` or env vars (`ERPNEXT_URL`, `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET`), then issue commands from nine groups: selling, buying, stock, accounts, manufacturing, crm, hr, doc, session.
license: Apache-2.0
---

# erpnext-cli

A business-process CLI for ERPNext, embedded as a self-contained DeerFlow
skill. Every command maps to a **business workflow** — the same actions a
human accountant / sales rep / warehouse operator would take in the
ERPNext web UI, collapsed into one invocation. Under the hood, it chains
ERPNext's whitelisted `erpnext.<module>.doctype.<dt>.<dt>.make_<target>`
methods instead of reimplementing DocType copy-forward logic.

## Invocation

The skill is mounted inside the DeerFlow sandbox at
`/mnt/skills/public/erpnext-cli/`. Call it via the launcher script:

```bash
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json <group> <command> [options]
```

> **Always pass `--json`.** Agents parse stdout as a typed envelope:
> `{"ok": true, "data": ...}` on success,
> `{"ok": false, "error": {"error": "<ClassName>", "message": "..."}}` on failure.

### First-time setup

Either (A) log in once and cache to `~/.cli-anything-erpnext/session.json`:

```bash
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json session login \
    --url https://erp.example.com \
    --api-key K --api-secret S --save-credentials

python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json session set-context \
    --company "ACME Ltd" --warehouse "Stores - ACME" --currency USD
```

Or (B) inject credentials via environment variables on each call — preferred
for CI / agent runners so secrets never hit disk:

| Env var | Purpose |
|---------|---------|
| `ERPNEXT_URL` | Base URL of the ERPNext site |
| `ERPNEXT_API_KEY` / `ERPNEXT_API_SECRET` | Token auth (preferred) |
| `ERPNEXT_USERNAME` / `ERPNEXT_PASSWORD` | Fallback user/pass auth |
| `ERPNEXT_VERIFY_SSL` | Set to `0` to skip TLS verification |

Env values **override** the session file at runtime.

## Command groups

| Group | Purpose | Key workflows |
|-------|---------|---------------|
| `session` | Auth, context defaults, history | `login`, `set-context`, `status`, `history` |
| `selling` | Quote → cash | `order-to-cash`, `quote-to-cash`, `onboard-customer`, `deliver`, `invoice-from-delivery`, `collect-payment`, `dashboard` |
| `buying` | Procure → pay | `procure-to-pay`, `request-to-pay`, `onboard-supplier`, `receive`, `bill-from-receipt`, `pay`, `dashboard` |
| `stock` | Warehouse movements | `transfer`, `issue`, `receipt`, `reconcile`, `levels`, `warehouse` |
| `accounts` | AR/AP, JEs, reconciliation | `receive-payment`, `pay`, `journal`, `reconcile`, `ar`, `ap`, `snapshot` |
| `manufacturing` | BOM → finished goods | `bom`, `work-order`, `issue-materials`, `finish`, `make-from-bom` |
| `crm` | Lead → customer | `lead`, `lead-to-opportunity`, `opportunity-to-quote`, `lead-to-customer`, `lead-to-quotation` |
| `hr` | Employees, leave, attendance | `employee`, `leave`, `attendance`, `onboard` |
| `doc` | Raw DocType CRUD (escape hatch) | `get`, `list`, `insert`, `update`, `submit`, `cancel`, `delete`, `call` |

### Item specification (three equivalent forms)

```bash
--item "ITEM-001:10"              # code:qty
--item "ITEM-001:10:99.50"        # code:qty:rate
--items-json '[{"item_code":"X","qty":10,"rate":99.5}]'
--items-file /mnt/user-data/uploads/items.json
```

## Examples

### 1. Full order-to-cash in one call

```bash
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json selling order-to-cash \
    --customer "Alice Ltd" \
    --item "WIDGET-001:10:99.50" \
    --item "BOLT-M8:50:0.25" \
    --mode-of-payment "Cash"
```

Output:

```json
{
  "ok": true,
  "data": {
    "workflow": "order_to_cash",
    "customer": "Alice Ltd",
    "sales_order": "SAL-ORD-2026-00012",
    "delivery_note": "MAT-DN-2026-00008",
    "sales_invoice": "ACC-SINV-2026-00015",
    "payment_entry": "ACC-PAY-2026-00007",
    "grand_total": 1008.0,
    "paid_amount": 1008.0
  }
}
```

### 2. Procure-to-pay

```bash
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json buying procure-to-pay \
    --supplier "PartsCo Inc" \
    --item "PART-A:20:15" \
    --item "PART-B:10:25" \
    --schedule-date 2026-05-10
```

### 3. Stock transfer

```bash
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json stock transfer \
    --item "WIDGET-001:50" \
    --source "Stores - ACME" \
    --target "Shop Floor - ACME"
```

### 4. Partial payment against an invoice

```bash
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json selling collect-payment \
    ACC-SINV-2026-0042 \
    --mode-of-payment "Bank" --paid-amount 500 \
    --reference-no "TX123456" --reference-date 2026-04-21
```

### 5. Balanced journal entry

```bash
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json accounts journal \
    --accounts-json '[
      {"account": "Cash - ACME", "debit_in_account_currency": 1000},
      {"account": "Revenue - ACME", "credit_in_account_currency": 1000}
    ]' --company "ACME Ltd"
```

### 6. BOM → work order → finished goods

```bash
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json manufacturing make-from-bom \
    --bom "BOM-PROD-A-001" --qty 10 \
    --fg-warehouse "Finished Goods - ACME" \
    --wip-warehouse "Work In Progress - ACME"
```

### 7. Raw DocType escape hatch

```bash
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py doc list "Payment Terms Template" --limit 5
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py doc get "Company" "ACME Ltd"
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py doc call frappe.auth.get_logged_user
```

## Agent contract

1. **Always pass `--json`.** The CLI returns `{"ok": true, "data": ...}` on
   success and `{"ok": false, "error": {...}}` on failure. Parse stdout.
2. **Exit code 0 = success.** Non-zero = failure; the `error` payload's
   `error` field names the typed exception class:
   `AuthError`, `NotFoundError`, `ValidationError`, `PermissionError_`,
   `WorkflowError`, `ServerError`, or the catch-all `ERPNextError`.
3. **Prefer workflow commands over `doc` CRUD.** If a domain command
   covers your intent (`selling order-to-cash`, `buying procure-to-pay`,
   etc.), use it — it chains the right DocTypes in the right order with
   the right `make_*` helpers. Fall through to `doc` only for DocTypes
   without workflow coverage.
4. **Use session context.** Once `session set-context --company ...` is
   run, every command that takes `--company` defaults to that value.
   Same for `--warehouse` / `--currency` / `--customer` / `--supplier`.
5. **`--no-submit`** keeps the created document as a draft. Default is to
   submit (matching the GUI's "Create → X" button).
6. **Inspect before mutating.** Read-side commands for reconnaissance:
   - `selling list-open --customer X` — open SOs for a customer
   - `selling dashboard <customer>` — outstanding + open orders
   - `buying list-open --supplier Y`
   - `accounts ar` / `accounts ap` / `accounts snapshot --company Z`
   - `stock levels --item Z --warehouse W`

## Error taxonomy

| `error.error` | Meaning | Agent action |
|---------------|---------|--------------|
| `AuthError` | Session expired / bad token | Re-run `session login` or re-inject env creds |
| `NotFoundError` | DocType / record doesn't exist | Check input names |
| `ValidationError` | Frappe rejected the payload | Fix fields, retry |
| `PermissionError_` | User lacks the required role | Use a different API key |
| `WorkflowError` | Precondition failed (e.g., JE unbalanced) | Read the message and fix inputs |
| `ServerError` | Frappe 5xx | Backoff + retry |
| `ERPNextError` | Unclassified | Surface to the user |

## Skill layout

```
erpnext-cli/
├── SKILL.md                    # this file
├── README.md                   # human-oriented quick reference
├── scripts/
│   ├── erpnext.py              # launcher — entry point for agents
│   └── erpnext_pkg/            # self-contained package (relative imports)
│       ├── cli.py              # Click root group
│       ├── core/               # FrappeClient, Session, typed errors
│       ├── domains/            # business-process logic (pure functions)
│       ├── cli_groups/         # Click wrappers per domain
│       └── utils/              # REPL skin
└── references/
    └── ERPNEXT.md              # full catalog of ERPNext `make_*` chain methods
```

## Dependencies

- Python **3.10+**
- `click`, `requests`, `prompt_toolkit` — usually already present in the
  DeerFlow sandbox. If missing, install inside the sandbox:

  ```bash
  pip install --quiet click requests prompt_toolkit
  ```

## Output modes

- **Human-readable** (default): indented JSON + REPL-style tables.
- **Machine-readable** (`--json` or `CLI_ANYTHING_JSON=1`): the typed
  envelope above. **Use this mode from agents.**

## More

- Per-group help: `python /mnt/skills/public/erpnext-cli/scripts/erpnext.py <group> --help`
- Full `make_*` catalog: `references/ERPNEXT.md`

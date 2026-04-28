
---
name: erpnext-cli
description: Use this skill to drive a running ERPNext / Frappe site end-to-end — quote-to-cash, procure-to-pay, material transfer, journal entries, BOM → work order → finish, lead → customer. Every domain command wraps ERPNext's native `make_*` chain methods, returns a typed JSON envelope (`{ok, data, error, next_actions}`), and submits documents by default (matching what the GUI's "Create → X" button produces). The CLI auto-authenticates from harness-injected credentials and persists session cookies — invoke domain commands directly, no upfront `session status` probe needed. Ten command groups: bootstrap, session, selling, buying, stock, accounts, manufacturing, crm, hr, doc.
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

> **Always pass** **`--json`.** Agents parse stdout as a typed envelope:
> `{"ok": true, "data": ...}` on success,
> `{"ok": false, "error": {"error": "<ClassName>", "message": "..."}}` on failure.

### Authentication (harness-managed, auto-applied)

**Inside the DeerFlow harness, credentials are pre-provisioned per tenant
and injected only when this CLI launcher runs.** Agents do not see the
URL, API key, or API secret — and must not look for them.

**You do not need to verify auth before every command.** The CLI:

- Picks up harness-injected credentials automatically on every
  invocation (token mode → stateless `Authorization: token …` header;
  username/password mode → cached session cookies in
  `~/.cli-anything-erpnext/cookies.json`).
- Transparently re-logs-in once if the cached session has expired
  (single retry on 401/403, then it surfaces the error).
- Returns a typed `AuthError` envelope **only** when the harness genuinely
  failed to inject credentials for this tenant — i.e. the user is not
  provisioned.

**The right flow is: invoke the domain command directly.** If the
response is `{"ok": false, "error": {"error": "AuthError", ...}}`,
**surface that to the user and stop** — do not call `session login`, do
not prompt for an API key, do not retry. `session status` is still
available for debugging, but it is no longer a required preflight step.

**Forbidden inside the harness:**

- `env`, `printenv`, `echo $ERPNEXT_*` — env-var enumeration is blocked
  by the agent prompt and would return empty regardless (the harness
  injects credentials only into this CLI launcher, never into plain
  bash).
- `session login --api-key … --api-secret …` — TradeMind never surfaces
  raw AK/SK to the agent or user; an `AuthError` means escalate, not
  collect credentials.
- `curl` / `requests` / `python -c "...requests..."` against any
  back-office URL — only this CLI may talk to ERPNext.

> **Standalone-CLI mode** (running this CLI outside the DeerFlow harness,
> e.g. from a developer shell) supports `session login` and the
> `ERPNEXT_*` env vars. After one `session login` the cookie jar is
> reused on subsequent invocations. Those paths are **not** for agent use
> and are intentionally unreachable from the in-harness sandbox.

## Command groups

| Group           | Purpose                         | Key workflows                                                                                                            |
| --------------- | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `session`       | Auth, context defaults, history | `login`, `set-context`, `status`, `history`                                                                              |
| `bootstrap`     | Tenant readiness probe          | `status`                                                                                                                 |
| `selling`       | Quote → cash                    | `order-to-cash`, `quote-to-cash`, `onboard-customer`, `deliver`, `invoice-from-delivery`, `collect-payment`, `dashboard` |
| `buying`        | Procure → pay                   | `procure-to-pay`, `request-to-pay`, `onboard-supplier`, `receive`, `bill-from-receipt`, `pay`, `dashboard`               |
| `stock`         | Warehouse movements             | `transfer`, `issue`, `receipt`, `reconcile`, `levels`, `warehouse`                                                       |
| `accounts`      | AR/AP, JEs, reconciliation      | `receive-payment`, `pay`, `journal`, `reconcile`, `ar`, `ap`, `snapshot`                                                 |
| `manufacturing` | BOM → finished goods            | `bom`, `work-order`, `issue-materials`, `finish`, `make-from-bom`                                                        |
| `crm`           | Lead → customer                 | `lead`, `lead-to-opportunity`, `opportunity-to-quote`, `lead-to-customer`, `lead-to-quotation`                           |
| `hr`            | Employees, leave, attendance    | `employee`, `leave`, `attendance`, `onboard`                                                                             |
| `doc`           | Raw DocType CRUD (escape hatch) | `get`, `list`, `insert`, `update`, `submit`, `cancel`, `delete`, `call`                                                  |

## Pre-flight: `bootstrap status` is mandatory before any chain command

A fresh ERPNext tenant has **zero master data** — no Company, no Warehouse,
no Item, no Supplier, no Customer. Running `selling order-to-cash` /
`buying procure-to-pay` / `stock stock-in` against an empty tenant fails
half-way and leaves orphan records. Always probe first:

```bash
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json bootstrap status
```

The single payload tells you in one shot:

```json
{
  "ok": true,
  "data": {
    "has_company": false,
    "company_count": 0,
    "default_warehouse": null,
    "warehouse_count": 0,
    "item_group_count": 0, "item_count": 0,
    "supplier_count": 0, "customer_count": 0,
    "ready_for_purchase": false,
    "ready_for_sales":    false,
    "ready_for_stock_in": false,
    "missing": [
      {"doctype": "Company",   "reason": "no record found", "blocks": ["all chains"]},
      {"doctype": "Warehouse", "reason": "no record found", "blocks": ["stock-in", "delivery", "purchase-receipt"]}
    ],
    "probe_errors": {}
  }
}
```

Decision rules:

- `ready_for_<chain>: true` → call the chain.
- `ready_for_<chain>: false` and `missing[*].blocks` includes that chain →
  set up the missing master first (or escalate to the user — never proceed
  blindly).
- `probe_errors` non-empty → upstream is unhealthy; surface the error to
  the user and stop. Do **not** retry the same chain.

## Per-DocType required fields (master setup quick reference)

When `bootstrap status` says a master is missing, these are the minimal
payloads to insert via `doc insert --doctype <X> --data '...'`. Anything
not listed is optional with sensible Frappe defaults.

| DocType            | Required fields                                                                          | Example minimal payload                                                                          |
| ------------------ | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `Company`          | `company_name`, `abbr`, `default_currency`, `country`                                    | `{"company_name":"BIEL","abbr":"BIEL","default_currency":"CNY","country":"Hong Kong"}`           |
| `Warehouse`        | `warehouse_name`, `company`                                                              | `{"warehouse_name":"主仓库","company":"BIEL"}`                                                      |
| `Item Group`       | `item_group_name`, `parent_item_group`                                                   | `{"item_group_name":"围巾","parent_item_group":"All Item Groups"}`                                 |
| `Item`             | `item_code`, `item_name`, `item_group`, `stock_uom`                                      | `{"item_code":"SCARF-001","item_name":"围巾 Style A","item_group":"围巾","stock_uom":"Pcs"}`         |
| `Supplier`         | `supplier_name`, `supplier_group`, `supplier_type`                                       | `{"supplier_name":"NOXIA","supplier_group":"All Supplier Groups","supplier_type":"Company"}`     |
| `Customer`         | `customer_name`, `customer_group`, `customer_type`                                       | `{"customer_name":"Alice Ltd","customer_group":"All Customer Groups","customer_type":"Company"}` |
| `UOM`              | `uom_name`                                                                               | `{"uom_name":"Pcs"}` (usually exists already)                                                    |
| `Currency`         | `currency_name`                                                                          | Use existing `CNY` / `USD` / `HKD`; `RMB` is **not** a Frappe code, use `CNY`.                   |
| `Purchase Order`   | `supplier`, `company`, `schedule_date`, `items: [{item_code, qty, rate, schedule_date}]` | Prefer `buying procure-to-pay` over raw insert.                                                  |
| `Purchase Receipt` | `supplier`, `company`, `items: [{item_code, qty, rate, warehouse, schedule_date}]`       | Prefer `buying receive --po <PO-name>` chain.                                                    |
| `Sales Order`      | `customer`, `company`, `delivery_date`, `items: [{item_code, qty, rate, delivery_date}]` | Prefer `selling order-to-cash` over raw insert.                                                  |
| `Sales Invoice`    | `customer`, `company`, `items: [{item_code, qty, rate}]`                                 | Prefer `selling invoice-from-delivery`.                                                          |
| `Stock Entry`      | `stock_entry_type`, `company`, `items: [{item_code, qty, t_warehouse}]`                  | Prefer `stock receipt` / `stock issue` / `stock transfer`.                                       |

Common gotchas pinned from real failures:

- **Currency code** — Use `CNY` (not `RMB`), `USD`, `HKD`. Probe via
  `doc list Currency --filter "name=CNY"` if uncertain.
- **Country name** — Use the full English name as Frappe stores it
  (`Hong Kong`, not `HK`). Probe via `doc list Country --limit 5`.
- **Parent groups** — `Item Group` / `Customer Group` / `Supplier Group`
  must reference an existing parent (`All Item Groups`, etc.); otherwise
  Frappe returns `LinkValidationError`.
- **`docstatus`** — Submitted docs are immutable. To "edit" a submitted
  Sales Order, cancel + amend, do not try to update fields directly.

### Item specification (three equivalent forms)

```bash
--item "ITEM-001:10"              # code:qty
--item "ITEM-001:10:99.50"        # code:qty:rate
--items-json '[{"item_code":"X","qty":10,"rate":99.5}]'
--items-file /mnt/user-data/uploads/items.json
```

The CLI accepts the human-friendly field name **`rate`** in every command
group. For `selling` / `buying` it goes through to the Sales Order /
Purchase Order child row as-is. For `stock` (`receipt` / `issue` /
`transfer`), the CLI promotes `rate` → `basic_rate` and sets
`set_basic_rate_manually: 1` automatically — Stock Entry Detail's
writable per-item price is `basic_rate`, not `rate` (which is a
computed column).

### Stock Entry rate / valuation gotchas

ERPNext's accounting layer enforces a non-zero valuation rate for every
item that hits a Stock Entry. There are three independent rate concepts
on a Stock Entry Detail row, and confusing them costs round-trips:

| Field | Meaning | Who sets it |
|---|---|---|
| `basic_rate` | Per-item cost on this row, in company currency. **Writable.** | You (via `--items-json … "rate": 99.5` or `"basic_rate": 99.5`). |
| `set_basic_rate_manually` | If `1`, ERPNext keeps your `basic_rate`; if `0`, it overwrites with Item master's `valuation_rate`. | CLI sets to `1` automatically when you pass `rate`. |
| `valuation_rate` (on Item master) | Default cost ERPNext falls back to when the row has no manual rate. | `doc update Item <code> --data '{"valuation_rate": 11.5}'` before the receipt. |

Failure modes you'll hit if these aren't set:

- **"要为此 Stock Entry 生成会计凭证，请先在物料主数据中维护成本价"** /
  *"Please maintain valuation rate in item master before submitting"*
  — both `basic_rate` on the row and `valuation_rate` on the Item are
  zero. Fix: pass `rate` (or `basic_rate`) on every row, **or** seed
  `valuation_rate` on the Item master first.
- **Submitted draft has all rates back to 0** — you passed `basic_rate`
  but no `set_basic_rate_manually`, and ERPNext overwrote with the
  Item's zero `valuation_rate`. The CLI handles this for you when you
  go through `stock receipt` / `stock issue` / `stock transfer`; raw
  `doc insert "Stock Entry"` does not.
- **Genuinely zero-cost items** (samples, write-offs) — set
  `allow_zero_valuation_rate: 1` on the row (or on the Item master) to
  bypass the accounting check. Don't reach for this casually; it
  silently zeroes inventory cost.

Recommended bulk-receipt pattern (avoids 4-5 round-trips of trial and
error):

```bash
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json stock receipt \
    --items-json '[
        {"item_code":"WIDGET-001","qty":1400,"rate":11.5},
        {"item_code":"BOLT-M8","qty":50,"rate":0.25},
        {"item_code":"FREEBIE-007","qty":10,"rate":0,"allow_zero_valuation_rate":1}
    ]' \
    --target "Stores - ACME" --company "ACME"
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

> `--json` is mandatory in agent contexts; the examples below model what the
> agent should emit verbatim.

```bash
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json doc list "Payment Terms Template" --limit 5
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json doc get "Company" "ACME Ltd"
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json doc call frappe.auth.get_logged_user
```

DOCTYPE is a **positional** argument on `doc list` / `doc get` (not
`--doctype`). Use `--filter` (repeatable) for WHERE clauses, `--field`
for projection, `--limit` / `--start` for pagination. `--data` is for
`doc insert` / `doc update` only — never on `doc list`.

## Agent contract

1. **Always pass** **`--json`.** The CLI returns `{"ok": true, "data": ...}` on
   success and `{"ok": false, "error": {...}}` on failure. Parse stdout.
2. **Exit code 0 = success.** Non-zero = failure; the `error` payload's
   `error` field names the typed exception class:
   `AuthError`, `NotFoundError`, `ValidationError`, `PermissionError_`,
   `WorkflowError`, `ServerError`, or the catch-all `ERPNextError`.
3. **Prefer workflow commands over** **`doc`** **CRUD.** If a domain command
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

Every error envelope carries a `next_actions: [{action, reason}]` list.
**Treat it as authoritative** — pick the first feasible action and
follow its `reason`. Do not improvise a different recovery, and do not
re-run the same command. The table below is a fallback summary in case
`next_actions` is empty.

| `error.error`      | Meaning                                   | Agent action                                                                                                                  |
| ------------------ | ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `AuthError`        | Harness did not inject credentials, or transparent re-login failed | Surface to the user and stop. **Never** call `session login` or prompt for an API key inside the harness. The CLI already retried once before surfacing this. |
| `NotFoundError`    | DocType / record doesn't exist            | `doc list <DocType>` to confirm spelling; if the tenant is empty, run `bootstrap status` first.                               |
| `ValidationError`  | Frappe rejected the payload               | Read `next_actions` for the missing field name; ask the user for that value rather than inventing one.                        |
| `PermissionError_` | User lacks the required role              | Surface to the user — the agent cannot escalate its own role.                                                                 |
| `WorkflowError`    | Precondition failed (e.g., JE unbalanced) | Read the message and fix inputs; consider `doc get` on the target record to confirm its current state before retrying.        |
| `ServerError`      | Frappe 5xx (incl. `BrokenPipeError`)      | **Stop retrying.** Surface to the user. The fail-fast guard will abort the run after 3 consecutive 5xx anyway.                |
| `ERPNextError`     | Unclassified                              | Surface to the user.                                                                                                          |

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
│       │   ├── client.py       # low-level Frappe REST client
│       │   ├── session.py      # session file + context defaults
│       │   └── errors.py       # typed error classes + next_actions
│       ├── domains/            # business-process logic (pure functions)
│       ├── cli_groups/         # Click wrappers per domain
│       │   ├── bootstrap_group.py   # tenant readiness probe
│       │   ├── session_group.py     # session status / set-context / login*
│       │   ├── doc_group.py         # raw CRUD escape hatch
│       │   ├── selling_group.py     # quote → cash chains
│       │   ├── buying_group.py      # procure → pay chains
│       │   ├── stock_group.py       # warehouse movements
│       │   ├── accounts_group.py    # AR/AP, JE, reconciliation
│       │   ├── manufacturing_group.py
│       │   ├── crm_group.py
│       │   ├── hr_group.py
│       │   └── repl_group.py        # standalone-CLI REPL (not for agents)
│       └── utils/              # REPL skin
└── references/
    └── ERPNEXT.md              # full catalog of ERPNext `make_*` chain methods
```

> `*` `session login` is wired for standalone-CLI use only; in the
> harness the `AuthError` recovery path is to escalate to the user, not
> to log in.

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


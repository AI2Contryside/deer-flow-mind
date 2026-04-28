---
name: erpnext-cli
description: Use this skill to drive a running ERPNext / Frappe site end-to-end — quote-to-cash, procure-to-pay, material transfer, journal entries, BOM → work order → finish, lead → customer. Every domain command wraps ERPNext's native `make_*` chain methods, returns a typed JSON envelope (`{ok, data, error, next_actions}`), and submits documents by default. The CLI auto-authenticates from harness-injected credentials. **Always call `help <group> <command>` before invoking an unfamiliar command — it returns full parameter spec, output schema, working examples, and common errors with recoveries.** Eleven command groups: bootstrap, session, selling, buying, stock, accounts, manufacturing, crm, hr, doc, help.
---

# erpnext-cli

A business-process CLI for ERPNext, embedded as a self-contained DeerFlow
skill. Every domain command maps to a **business workflow** — the same
actions a human accountant / sales rep / warehouse operator would take
in the ERPNext web UI, collapsed into one invocation. Under the hood it
chains ERPNext's whitelisted `erpnext.<module>.doctype.<dt>.<dt>.make_<target>`
methods instead of reimplementing DocType copy-forward logic.

## Mandatory rule: **call `help <group> <command>` first**

Parameter names, required-vs-optional, default values, output JSON
shape, working examples, and common errors live in the **`help`** command
itself — not in this file. Before invoking any command you haven't run
recently, fetch its full spec:

```bash
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json help
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json help selling
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json help selling order-to-cash
```

Three call shapes:

| Call shape | Returns |
|---|---|
| `help` (no args) | All groups with one-line summary, business process, and key chains. |
| `help <group>` | Every command in that group with one-line summary. |
| `help <group> <command>` | **Full per-command spec**: arguments, options (type, required, default, choices), enriched parameter notes, output schema, ready-to-run examples, and common errors with concrete fixes. |

Always pass `--json`. The help envelope is structured for agent
parsing: `{ok: true, data: {kind: "command_help", arguments: [...], options: [...], output_schema: {...}, examples: [...], common_errors: [...], related: [...]}}`.

> If `help <group> <command>` returns `kind: "unknown_command"`, read
> the `available_commands` list — you almost certainly used a wrong
> name. Do **not** improvise — re-fetch the correct help record first.

## Invocation

The skill is mounted inside the DeerFlow sandbox at
`/mnt/skills/public/erpnext-cli/`. Call it via the launcher script:

```bash
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json <group> <command> [options]
```

> **Always pass** **`--json`.** Agents parse stdout as a typed envelope:
> `{"ok": true, "data": ...}` on success,
> `{"ok": false, "error": {"error": "<ClassName>", "message": "..."}}` on failure.

## Authentication (harness-managed, auto-applied)

Inside the DeerFlow harness, credentials are pre-provisioned per tenant
and injected only when this CLI launcher runs. **Agents do not see the
URL, API key, or API secret — and must not look for them.** Invoke
domain commands directly; the CLI authenticates transparently.

If a command returns `{"ok": false, "error": {"error": "AuthError", ...}}`,
**surface to the user and stop.** Inside the harness:

- **Forbidden:** `session login --api-key …`, `env` / `printenv` /
  `echo $ERPNEXT_*` (env-var enumeration is blocked and would return
  empty anyway), and any direct `curl` / `requests` against ERPNext.
- The CLI already retried once on 401/403 before surfacing. Re-running
  won't help.

Standalone-CLI (developer shell, outside the harness) supports
`session login` and `ERPNEXT_*` env vars — those paths are intentionally
unreachable from the in-harness sandbox.

## Pre-flight: `bootstrap status` is mandatory before any chain

A fresh ERPNext tenant has zero master data. Running a chain against an
empty tenant fails half-way and leaves orphan records. **Always probe first:**

```bash
python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json bootstrap status
```

The single payload tells you whether `ready_for_sales` /
`ready_for_purchase` / `ready_for_stock_in` are true, and lists missing
masters with the chains they block. See
`help bootstrap status` for the full output schema.

Decision rules:
- `ready_for_<chain>: true` → call the chain.
- `ready_for_<chain>: false` → set up the missing master(s) via
  `doc insert` first (or escalate to the user).
- `probe_errors` non-empty → upstream is unhealthy. Stop. Don't retry.

## Command groups → business processes

| Group | Business process | When to pick it |
|---|---|---|
| `help` | Meta | Run this **first** for any command you don't have memorized. |
| `bootstrap` | Pre-flight | Probe tenant readiness before any chain. |
| `session` | Setup / debugging | Set context defaults, inspect status. (`login` is forbidden in-harness.) |
| `selling` | Quote → Cash | Customer-facing sales: quotation, sales order, delivery, sales invoice, payment receipt. |
| `buying` | Procure → Pay | Supplier-facing procurement: material request, RFQ, PO, receipt, supplier bill, supplier payment. |
| `stock` | Inventory | Warehouse-only movements (no SO/PO context): transfer, issue, receipt, reconciliation, level queries. |
| `accounts` | Accounting | AR/AP positions, journal entries, payment reconciliation. |
| `manufacturing` | Make-to-stock / Make-to-order | BOM, work order, raw material issuance, finished goods. |
| `crm` | Lead → Customer | Lead capture, opportunity conversion, lead → customer / quote. |
| `hr` | HR / People | Employee onboarding, leave, attendance. |
| `doc` | Generic / escape hatch | Raw DocType CRUD for anything without a domain wrapper (Company / Warehouse / Item Group / etc.). |

Within each group, run `help <group>` to see every command and pick the
right one. Run `help <group> <command>` for the full spec.

> **Heuristic:** if a domain command exists for the intent, prefer it
> over `doc insert`. Domain commands chain the right `make_*` builders
> in the right order and copy forward fields that raw `doc insert`
> doesn't. Use `doc insert` only for masters (Company, Warehouse, Item,
> Item Group, Customer, Supplier, etc.) and for DocTypes the CLI doesn't
> wrap.

## Per-DocType required fields (master setup quick reference)

When `bootstrap status` reports a missing master, these are the minimal
payloads to insert via `doc insert --doctype <X> --data '...'`. Common
gotchas pinned from real failures.

| DocType | Required fields | Example minimal payload |
|---|---|---|
| `Company` | `company_name`, `abbr`, `default_currency`, `country` | `{"company_name":"BIEL","abbr":"BIEL","default_currency":"CNY","country":"Hong Kong"}` |
| `Warehouse` | `warehouse_name`, `company` | `{"warehouse_name":"Stores","company":"BIEL"}` |
| `Item Group` | `item_group_name`, `parent_item_group` | `{"item_group_name":"Scarves","parent_item_group":"All Item Groups"}` |
| `Item` | `item_code`, `item_name`, `item_group`, `stock_uom` | `{"item_code":"SCARF-001","item_name":"Scarf A","item_group":"Scarves","stock_uom":"Pcs"}` |
| `Supplier` | `supplier_name`, `supplier_group`, `supplier_type` | `{"supplier_name":"NOXIA","supplier_group":"All Supplier Groups","supplier_type":"Company"}` |
| `Customer` | `customer_name`, `customer_group`, `customer_type` | `{"customer_name":"Alice Ltd","customer_group":"All Customer Groups","customer_type":"Company"}` |
| `UOM` | `uom_name` | `{"uom_name":"Pcs"}` (`Pcs`, `Nos`, etc. usually exist already) |
| `Currency` | `currency_name` | Use existing `CNY` / `USD` / `HKD`; `RMB` is **not** a Frappe code, use `CNY`. |

Common gotchas:
- **Currency code** — `CNY` (not `RMB`), `USD`, `HKD`. Probe via `doc list Currency --filter "name=CNY"`.
- **Country name** — Full English name (`Hong Kong`, not `HK`). Probe via `doc list Country --limit 5`.
- **Parent groups** — `Item Group` / `Customer Group` / `Supplier Group` must reference an existing parent (`All Item Groups`, etc.); otherwise Frappe returns `LinkValidationError`.
- **`docstatus`** — Submitted docs are immutable. To "edit" a submitted Sales Order, cancel + amend, do not try to update fields directly.
- **Stock Entry valuation** — When pushing a Stock Entry (receipt / issue / transfer), every row needs either a manual `rate` (CLI promotes to `basic_rate` + `set_basic_rate_manually=1`) **or** a non-zero `valuation_rate` on the Item master, otherwise submission fails with *"Please maintain valuation rate in item master"*. Use `doc update Item <code> --data '{"valuation_rate": <n>}'` to seed the master, or just pass `rate` per row.

For full per-command guidance — including the complete output schema and
common-error recoveries — call `help <group> <command>`.

## Agent contract

1. **Always pass `--json`.** Parse stdout as `{ok, data}` or `{ok, error}`.
2. **Exit code 0 = success.** Non-zero = failure; the `error.error`
   field names the typed exception class.
3. **Call `help <group> <command>` first** for any unfamiliar command.
4. **`bootstrap status` before any selling / buying / stock / manufacturing chain.**
5. **Prefer domain workflows over `doc` CRUD.** If a domain command
   covers your intent, use it.
6. **Use session context.** Once `session set-context --company ...`
   is run, every command's `--company` defaults to that value.
7. **`--no-submit` keeps the document as a draft.** Default is to
   submit (matching the GUI's "Create → X" button).
8. **On `AuthError` inside the harness, escalate. Do not retry login.**

## Error taxonomy

Every error envelope carries a `next_actions: [{action, reason}]` list.
Treat it as authoritative. The table below is a fallback summary.

| `error.error` | Meaning | Agent action |
|---|---|---|
| `AuthError` | Harness did not inject credentials, or transparent re-login failed. | Surface to the user and stop. **Never** call `session login` inside the harness. |
| `NotFoundError` | DocType / record doesn't exist. | `doc list <DocType>` to confirm spelling; if the tenant is empty, run `bootstrap status` first. |
| `ValidationError` | Frappe rejected the payload. | Read `next_actions` for the missing field; ask the user rather than inventing one. Call `help <group> <command>` to confirm input shape. |
| `PermissionError_` | User lacks the required role. | Surface to the user. |
| `WorkflowError` | Precondition failed (JE unbalanced, source not submitted, etc.). | Read the message; consider `doc get` on the source to confirm state before retrying. |
| `ServerError` | Frappe 5xx. | **Stop retrying.** Surface to the user. The fail-fast guard aborts after 3 consecutive 5xx anyway. |
| `ERPNextError` | Unclassified. | Surface to the user. |

For per-command error tables (with concrete fixes), call
`help <group> <command>`.

## Skill layout

```
erpnext-cli/
├── SKILL.md                    # this file (process map + help-first directive)
├── README.md                   # human-oriented quick reference
├── scripts/
│   ├── erpnext.py              # launcher — entry point for agents
│   └── erpnext_pkg/            # self-contained package
│       ├── cli.py              # Click root group
│       ├── core/               # FrappeClient, Session, typed errors
│       ├── domains/            # business-process logic (pure functions)
│       └── cli_groups/         # Click wrappers per domain
│           ├── help_group.py   # → `help` command (LLM-friendly)
│           ├── _help_data.py   # curated per-command metadata
│           ├── _help_introspect.py  # auto-extract Click params
│           └── …                # bootstrap / session / selling / buying / …
└── references/
    └── ERPNEXT.md              # full catalog of ERPNext make_* chain methods
```

## Output modes

- **Human-readable** (default): indented JSON + REPL-style tables.
- **Machine-readable** (`--json` or `CLI_ANYTHING_JSON=1`): the typed
  envelope above. **Use this mode from agents.**

## More

- Per-group / per-command help: `help <group> <command>` (this is the
  primary reference — it stays in sync with the code automatically).
- Group-level Click help: `python /mnt/skills/public/erpnext-cli/scripts/erpnext.py <group> --help`.
- Full `make_*` catalog: `references/ERPNEXT.md`.

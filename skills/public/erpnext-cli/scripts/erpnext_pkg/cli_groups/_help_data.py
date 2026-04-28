"""Hand-curated help metadata for every CLI command.

The ``help`` command merges this metadata with auto-introspected Click
parameter records (``_help_introspect``) to produce a single LLM-friendly
JSON envelope per command.

What lives here vs. in Click decorators
---------------------------------------
- **In Click decorators** (``@click.option(..., help=...)``): one-line
  flag descriptions. Used for the human ``--help`` screen.
- **In this module**: business-process context, preconditions, enriched
  per-parameter notes, output schema, full working examples, common
  errors with recoveries, and pointers to related commands.

Why hand-curated
----------------
Click only knows the *shape* of a parameter (name, type, required). It
does not know that ``--customer`` is the value of the Customer master's
``customer_name`` field, that empty tenants will fail this chain until
``bootstrap status`` says ready, or that a typical ``NotFoundError`` on
an order-to-cash run almost always means the customer name is misspelt.
That's the curated layer.

When you add or change a CLI command
------------------------------------
Update the matching ``HELP_DATA[(group, cmd)]`` record. If the parameter
list changes, you do **not** need to edit ``param_notes`` — Click
introspection picks up the new params automatically; ``param_notes``
just enriches descriptions for the params worth annotating.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ─────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class HelpRecord:
    """Curated help metadata for one CLI command."""

    summary: str
    """One-line summary (≤80 chars) — what the command does."""

    process: str
    """Business process this command belongs to (e.g. 'Quote → Cash')."""

    when_to_use: str
    """Two- to four-sentence guidance for the LLM: when to pick THIS
    command instead of an adjacent one. The most useful field for
    avoiding wrong-tool errors."""

    preconditions: tuple[str, ...] = ()
    """Things that must be true before invoking. Listed in priority order."""

    param_notes: dict[str, str] = field(default_factory=dict)
    """``flag-or-arg-name → enriched description``. Keyed by the *primary*
    flag name (e.g. ``--customer``) or the *positional* arg name
    upper-cased (``CUSTOMER``). Click introspection still drives
    type/required/default — this only enriches the description.
    """

    output: dict[str, str] = field(default_factory=dict)
    """``json-field → description`` for the ``data`` block on success."""

    examples: tuple[dict[str, Any], ...] = ()
    """Each example: ``{title, command, expect}``. ``command`` is the full
    bash invocation as a single string. ``expect`` is a short prose
    description of what the LLM should see come back."""

    common_errors: tuple[dict[str, str], ...] = ()
    """Each error: ``{error, cause, fix}``. ``error`` matches the
    ``error.error`` field in the JSON envelope (e.g. ``NotFoundError``)."""

    related: tuple[str, ...] = ()
    """Related command paths to consider next, e.g. ``selling dashboard``."""


@dataclass(frozen=True)
class GroupRecord:
    """Curated help metadata for one command group."""

    summary: str
    """One-line summary of the group's purpose."""

    process: str
    """Business process the group covers (e.g. 'Quote → Cash')."""

    when_to_use: str
    """Two-line hint about when an LLM should reach for this group."""

    key_chains: tuple[str, ...] = ()
    """Multi-step chain commands this group exposes (the headline ones)."""


# ─────────────────────────────────────────────────────────────────────
# Reusable error fragments — keeps records DRY
# ─────────────────────────────────────────────────────────────────────

ERR_AUTH = {
    "error": "AuthError",
    "cause": "Harness did not inject ERPNext credentials, or the cached "
             "session expired and silent re-login failed.",
    "fix": "Surface to the user and stop. Inside the DeerFlow harness, "
           "do NOT call `session login` and do NOT prompt for keys — "
           "the CLI already retried once before surfacing this.",
}

ERR_TENANT_MISSING = {
    "error": "AuthError",
    "cause": "X-Tenant-ID is required on every call but was not "
             "supplied (no --tenant flag and ERPNEXT_TENANT_ID env var "
             "is empty).",
    "fix": "Pass --tenant <id> on the root command, or export "
           "ERPNEXT_TENANT_ID before invoking. Inside the harness this "
           "is normally injected automatically — surface to the user.",
}

ERR_NEEDS_BOOTSTRAP = {
    "error": "NotFoundError",
    "cause": "The tenant has no master data (Company / Warehouse / "
             "Item / Customer / Supplier all empty).",
    "fix": "Run `bootstrap status` first. If `ready_for_<chain>` is "
           "false, set up the missing master(s) via `doc insert` "
           "before running the chain.",
}

ERR_CUSTOMER_NOT_FOUND = {
    "error": "NotFoundError",
    "cause": "Customer master with that exact name does not exist.",
    "fix": "Verify spelling: `doc list Customer --filter "
           "\"customer_name=<X>\" --limit 5`. To create + order in one "
           "step, use `selling onboard-customer`.",
}

ERR_SUPPLIER_NOT_FOUND = {
    "error": "NotFoundError",
    "cause": "Supplier master with that exact name does not exist.",
    "fix": "Verify spelling: `doc list Supplier --filter "
           "\"supplier_name=<X>\" --limit 5`. To create + order in one "
           "step, use `buying onboard-supplier`.",
}

ERR_ITEM_NOT_FOUND = {
    "error": "NotFoundError",
    "cause": "One of the item codes does not exist in the Item master.",
    "fix": "Verify with `doc list Item --filter \"item_code=<X>\"`. If "
           "you intend to auto-create, use the ``--ensure-items`` flag "
           "on the onboard commands, or `doc insert --doctype Item ...`.",
}

ERR_ITEM_NO_VALUATION = {
    "error": "ValidationError",
    "cause": "Submitting a Stock Entry against an Item with "
             "`valuation_rate=0` and no manual `basic_rate` on the row.",
    "fix": "Pass `rate` on every --item or items-json row (CLI promotes "
           "it to `basic_rate` + `set_basic_rate_manually=1`), OR set "
           "`valuation_rate` on the Item master first via "
           "`doc update Item <code> --data '{\"valuation_rate\":<n>}'`.",
}

ERR_SUBMITTED_IMMUTABLE = {
    "error": "ValidationError",
    "cause": "Trying to update fields on a submitted (`docstatus=1`) "
             "document. ERPNext blocks direct edits after submission.",
    "fix": "Cancel + amend (`doc cancel <DT> <name>`) before changing, "
           "or post a Journal Entry to adjust the financial impact.",
}

ERR_VALIDATION_GENERIC = {
    "error": "ValidationError",
    "cause": "Frappe rejected the payload. The message names the "
             "specific field (missing, malformed, or referencing a "
             "non-existent linked record).",
    "fix": "Read `error.message` and `error.next_actions[0].reason`. "
           "Fix the named field and retry. Do NOT improvise alternative "
           "values silently — ask the user when in doubt.",
}

ERR_PERMISSION = {
    "error": "PermissionError_",
    "cause": "The authenticated user lacks the role needed for this "
             "DocType / submit action.",
    "fix": "Surface to the user — the agent cannot escalate its own "
           "role. Use a different API key or get the role granted in "
           "the ERPNext UI.",
}

ERR_SERVER_5XX = {
    "error": "ServerError",
    "cause": "Frappe returned a 5xx (overload, deadlock, broken pipe, "
             "etc.).",
    "fix": "STOP retrying. Surface to the user. The fail-fast guard "
           "aborts after 3 consecutive 5xx anyway.",
}


# ─────────────────────────────────────────────────────────────────────
# Group-level metadata
# ─────────────────────────────────────────────────────────────────────

GROUP_DATA: dict[str, GroupRecord] = {
    "session": GroupRecord(
        summary="Connection state, default context, and event history.",
        process="Setup / debugging",
        when_to_use=(
            "Use `session status` to debug 'is the CLI authenticated?' "
            "questions. Use `session set-context` to bake a default "
            "company/warehouse so subsequent commands don't need them. "
            "Inside the DeerFlow harness, NEVER call `session login` — "
            "credentials are injected automatically."
        ),
        key_chains=("set-context", "status", "history"),
    ),
    "bootstrap": GroupRecord(
        summary="One-shot tenant readiness probe.",
        process="Pre-flight",
        when_to_use=(
            "Always run `bootstrap status` once at the start of any "
            "session that intends to call selling/buying/stock/"
            "manufacturing chains. The single payload tells you which "
            "chains are ready and what masters are missing if not."
        ),
        key_chains=("status",),
    ),
    "selling": GroupRecord(
        summary="Quote → Cash workflows (customer-facing sales).",
        process="Quote → Cash",
        when_to_use=(
            "Pick a `selling` command when the user describes selling "
            "to a customer: quotations, sales orders, deliveries, "
            "sales invoices, payment collection. The headline chain "
            "is `order-to-cash`."
        ),
        key_chains=("order-to-cash", "quote-to-cash", "onboard-customer"),
    ),
    "buying": GroupRecord(
        summary="Procure → Pay workflows (supplier-facing procurement).",
        process="Procure → Pay",
        when_to_use=(
            "Pick a `buying` command when the user describes buying "
            "from a supplier: material requests, RFQs, purchase orders, "
            "purchase receipts, supplier bills, supplier payments. The "
            "headline chain is `procure-to-pay`."
        ),
        key_chains=("procure-to-pay", "onboard-supplier"),
    ),
    "stock": GroupRecord(
        summary="Warehouse movements and stock-level queries.",
        process="Inventory",
        when_to_use=(
            "Pick a `stock` command for movements *outside* a Purchase "
            "Receipt / Delivery Note: receipts (e.g. opening balance), "
            "issues (write-offs, samples), inter-warehouse transfers, "
            "physical-count reconciliations, and on-hand level queries."
        ),
        key_chains=("transfer", "issue", "receipt", "reconcile", "levels"),
    ),
    "accounts": GroupRecord(
        summary="AR/AP, payments, journal entries, reconciliation.",
        process="Accounting",
        when_to_use=(
            "Pick an `accounts` command for AR/AP positions, balanced "
            "journal entries, and explicit payment recording. For "
            "payments tied to a specific invoice, prefer "
            "`selling collect-payment` / `buying pay` — they fetch "
            "the invoice context for you."
        ),
        key_chains=("ar", "ap", "snapshot", "journal", "reconcile"),
    ),
    "manufacturing": GroupRecord(
        summary="BOM → Work Order → Finished Goods.",
        process="Make-to-stock / Make-to-order",
        when_to_use=(
            "Pick a `manufacturing` command for BOM creation, work "
            "orders, raw-material issuance, and finished-goods "
            "reporting. The end-to-end chain is `make-from-bom`."
        ),
        key_chains=("bom", "work-order", "make-from-bom"),
    ),
    "crm": GroupRecord(
        summary="Lead → Opportunity → Customer / Quotation.",
        process="Lead → Customer",
        when_to_use=(
            "Pick a `crm` command when the user describes pipeline-"
            "stage work: capturing leads, converting to opportunities/"
            "customers, drafting quotes off a lead. Once you have a "
            "Customer, switch to the `selling` group for cash flow."
        ),
        key_chains=("lead", "lead-to-quotation", "lead-to-customer"),
    ),
    "hr": GroupRecord(
        summary="Employees, leave, attendance.",
        process="HR / People",
        when_to_use=(
            "Pick an `hr` command for employee onboarding, leave "
            "applications, and daily attendance. Payroll is not yet "
            "wrapped — fall through to `doc` for Salary Structure / "
            "Salary Slip."
        ),
        key_chains=("employee", "onboard", "leave", "attendance"),
    ),
    "doc": GroupRecord(
        summary="Raw DocType CRUD (escape hatch).",
        process="Generic",
        when_to_use=(
            "Use `doc` ONLY when no domain command covers the intent: "
            "listing/inspecting any DocType, creating master records "
            "(Company, Warehouse, Item Group, etc.), calling arbitrary "
            "whitelisted Frappe methods. For Sales Order / Purchase "
            "Order / Stock Entry / Sales Invoice, prefer the domain "
            "chains — they fill in the right copy-forward fields."
        ),
        key_chains=("get", "list", "insert", "update", "submit", "call"),
    ),
}


# ─────────────────────────────────────────────────────────────────────
# Per-command help — section by section to keep this file scannable.
# Keys are (group, command) tuples.
# ─────────────────────────────────────────────────────────────────────

HELP_DATA: dict[tuple[str, str], HelpRecord] = {}


# ── session ──────────────────────────────────────────────────────────

HELP_DATA[("session", "status")] = HelpRecord(
    summary="Show the current session (URL, context defaults, secrets redacted).",
    process="Setup / debugging",
    when_to_use=(
        "Run as a DEBUG step when an `AuthError` surfaces from a domain "
        "command and you want to confirm what URL/keys the CLI sees. "
        "It is NOT a required preflight — domain commands authenticate "
        "transparently."
    ),
    output={
        "url": "ERPNext site URL the CLI will hit.",
        "api_key": "Public part of the API key (secret is redacted).",
        "username": "Username if password mode is in use.",
        "context": "Object of default company / warehouse / customer / "
                   "supplier / currency / etc.",
        "history": "Recent workflow events (most recent last).",
    },
    examples=(
        {
            "title": "Read current session",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json session status",
            "expect": "JSON envelope with url, context defaults, redacted secrets.",
        },
    ),
    common_errors=(ERR_AUTH,),
    related=("session set-context", "session ping", "bootstrap status"),
)

HELP_DATA[("session", "ping")] = HelpRecord(
    summary="Verify the session can reach ERPNext (calls /api/method/frappe.auth.get_logged_user).",
    process="Setup / debugging",
    when_to_use=(
        "Use as a quick liveness probe when domain commands hang or "
        "return server errors. Confirms creds + URL + tenant + network "
        "are all good."
    ),
    output={"ok": "Always true on success.", "user": "Logged-in username."},
    examples=(
        {
            "title": "Liveness check",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json session ping",
            "expect": "{ok: true, data: {user: 'Administrator'}}",
        },
    ),
    common_errors=(ERR_AUTH, ERR_TENANT_MISSING, ERR_SERVER_5XX),
    related=("session status", "bootstrap status"),
)

HELP_DATA[("session", "set-context")] = HelpRecord(
    summary="Persist default company / warehouse / customer / etc. for future commands.",
    process="Setup",
    when_to_use=(
        "Set once per tenant if multiple commands will run with the "
        "same Company / Warehouse. Domain commands use these as "
        "fallbacks when their `--company` / `--warehouse` flag is "
        "omitted."
    ),
    param_notes={
        "--company": "Company name as it appears in `doc list Company` (NOT the abbr).",
        "--warehouse": "Full warehouse name with company suffix, e.g. 'Stores - ACME'.",
        "--customer": "Customer master name to default for selling commands.",
        "--supplier": "Supplier master name to default for buying commands.",
        "--currency": "ISO code (CNY/USD/HKD); 'RMB' is NOT valid in Frappe.",
    },
    output={"context": "Updated context defaults dict (after merging --updates)."},
    examples=(
        {
            "title": "Bake company + warehouse defaults",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json session set-context --company \"BIEL\" "
                       "--warehouse \"Stores - BIEL\"",
            "expect": "Echo of the updated context.",
        },
    ),
    common_errors=({"error": "UsageError",
                    "cause": "Called with no --company/--warehouse/...",
                    "fix": "Pass at least one default flag."},),
    related=("session status",),
)

HELP_DATA[("session", "history")] = HelpRecord(
    summary="Show recent workflow events recorded by chain commands.",
    process="Debugging",
    when_to_use=(
        "Use to audit what chains the CLI has run in this tenant "
        "recently — useful when reproducing an issue or tracing what "
        "the agent already created."
    ),
    output={
        "(array)": "Each entry: {at: ISO8601 UTC, event: workflow name, "
                   "payload: small dict of created doc names}.",
    },
    examples=(
        {
            "title": "Last 10 events",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json session history --limit 10",
            "expect": "Array of events, newest last.",
        },
    ),
)

HELP_DATA[("session", "login")] = HelpRecord(
    summary="(STANDALONE-CLI ONLY) Authenticate and persist a session.",
    process="Setup",
    when_to_use=(
        "ONLY for developer / standalone-CLI use. Inside the DeerFlow "
        "harness this is FORBIDDEN — credentials are injected by the "
        "harness, not collected from the agent. Surfacing an `AuthError` "
        "should never trigger a `session login` retry inside the harness."
    ),
    common_errors=(ERR_AUTH,),
    related=("session status",),
)

HELP_DATA[("session", "logout")] = HelpRecord(
    summary="(STANDALONE-CLI ONLY) Clear stored credentials, keep URL + context.",
    process="Setup",
    when_to_use="Standalone-CLI debugging only.",
)

HELP_DATA[("session", "clear")] = HelpRecord(
    summary="(STANDALONE-CLI ONLY) Delete the entire session file.",
    process="Setup",
    when_to_use="Standalone-CLI debugging only. Forbidden in harness.",
)


# ── bootstrap ────────────────────────────────────────────────────────

HELP_DATA[("bootstrap", "status")] = HelpRecord(
    summary="Probe master-data readiness for selling / buying / stock / manufacturing chains.",
    process="Pre-flight",
    when_to_use=(
        "Run ONCE at the start of any session that intends to call a "
        "domain chain. The payload tells you, in one shot, which "
        "chains are ready (`ready_for_sales` / `ready_for_purchase` / "
        "`ready_for_stock_in`) and what masters need to be created "
        "first if not. Never charge into a chain on an empty tenant."
    ),
    preconditions=("Valid tenant (X-Tenant-ID resolved).",),
    output={
        "has_company": "Bool — Company master has at least one record.",
        "company_count": "Int — capped probe (we only need to know >0).",
        "default_warehouse": "Str|null — value from session context, NOT a probe.",
        "warehouse_count": "Int — Warehouse records present.",
        "item_group_count": "Int — Item Group records present.",
        "item_count": "Int — Item records present.",
        "supplier_count": "Int — Supplier records present.",
        "customer_count": "Int — Customer records present.",
        "ready_for_purchase": "Bool — Company AND Warehouse AND Supplier AND Item.",
        "ready_for_sales": "Bool — Company AND Warehouse AND Customer AND Item.",
        "ready_for_stock_in": "Bool — Company AND Warehouse AND Item.",
        "missing": "Array — each missing master with `blocks` listing affected chains.",
        "probe_errors": "Object — non-empty means upstream Frappe is unhealthy. "
                         "STOP and surface, do not retry chains.",
    },
    examples=(
        {
            "title": "Pre-flight before order-to-cash",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json bootstrap status",
            "expect": "JSON with ready_for_sales true/false and missing[] details.",
        },
    ),
    common_errors=(ERR_AUTH, ERR_TENANT_MISSING, ERR_SERVER_5XX),
    related=("doc insert", "session set-context"),
)


# ── selling ──────────────────────────────────────────────────────────

_ITEM_SPEC_NOTE = (
    "Repeatable. Format: `CODE:QTY` or `CODE:QTY:RATE` (rate in company "
    "currency). At least one of --item / --items-json / --items-file "
    "must produce ≥1 row. The CLI promotes `rate` → `basic_rate` for "
    "stock commands automatically."
)
_ITEMS_JSON_NOTE = (
    "Inline JSON array of item rows: "
    "`[{\"item_code\":\"X\",\"qty\":10,\"rate\":99.5}, ...]`. "
    "Mutually combinable with --item; rows are concatenated."
)
_ITEMS_FILE_NOTE = (
    "Path to a JSON file with the same shape as --items-json. Useful "
    "when you've staged a file under /mnt/user-data/uploads/."
)
_NO_SUBMIT_NOTE = (
    "Keep the document as a draft (`docstatus=0`). Default behaviour "
    "is SUBMIT (matches the GUI's 'Create → X' button). An unsubmitted "
    "Sales/Purchase Order can't be delivered/received or invoiced — "
    "only set this if you intend a multi-step approval."
)
_COMPANY_NOTE = (
    "Company name as in `doc list Company` (NOT the abbreviation). "
    "Falls back to `session set-context --company` when omitted."
)


HELP_DATA[("selling", "quote")] = HelpRecord(
    summary="Create a Quotation for a customer.",
    process="Quote → Cash (step 1: Quotation)",
    when_to_use=(
        "Use when a customer wants pricing but has NOT yet committed "
        "to an order. For confirmed orders skip straight to "
        "`selling order` or `selling order-to-cash`."
    ),
    preconditions=(
        "`bootstrap status` shows Company + Customer + Item present.",
        "Customer master exists (or use `selling onboard-customer`).",
    ),
    param_notes={
        "--customer": "Customer master name (the value of `customer_name`).",
        "--item": _ITEM_SPEC_NOTE,
        "--items-json": _ITEMS_JSON_NOTE,
        "--items-file": _ITEMS_FILE_NOTE,
        "--company": _COMPANY_NOTE,
        "--currency": "ISO code (CNY/USD/HKD). 'RMB' is invalid in Frappe.",
        "--valid-till": "ISO date (YYYY-MM-DD). Defaults to ERPNext's quotation validity.",
        "--submit": "Submit the quotation. Default is to leave as DRAFT for review.",
    },
    output={
        "name": "Created Quotation name, e.g. 'SAL-QTN-2026-00007'.",
        "customer": "Echoed customer name.",
        "items": "Array of submitted item rows (with computed amounts).",
        "grand_total": "Quotation grand total in company currency.",
        "docstatus": "0 (draft) unless --submit was passed.",
    },
    examples=(
        {
            "title": "Single-item draft quotation",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json selling quote --customer \"Alice Ltd\" "
                       "--item \"WIDGET-001:10:99.50\"",
            "expect": "Created Quotation name + line-items + grand_total.",
        },
    ),
    common_errors=(
        ERR_CUSTOMER_NOT_FOUND, ERR_ITEM_NOT_FOUND, ERR_VALIDATION_GENERIC,
        ERR_NEEDS_BOOTSTRAP, ERR_AUTH,
    ),
    related=("selling quote-to-order", "selling order", "selling quote-to-cash"),
)

HELP_DATA[("selling", "order")] = HelpRecord(
    summary="Create a Sales Order (submits by default).",
    process="Quote → Cash (step 2: Sales Order)",
    when_to_use=(
        "Use when the customer has confirmed the order. The default "
        "is SUBMIT — a draft SO can't be delivered or invoiced. Pass "
        "--no-submit ONLY if a multi-step approval is required."
    ),
    preconditions=(
        "Customer master exists.",
        "Item master exists for every code passed.",
    ),
    param_notes={
        "--customer": "Customer master name.",
        "--item": _ITEM_SPEC_NOTE,
        "--items-json": _ITEMS_JSON_NOTE,
        "--items-file": _ITEMS_FILE_NOTE,
        "--company": _COMPANY_NOTE,
        "--delivery-date": "ISO date (YYYY-MM-DD). Required by ERPNext on item rows; "
                           "if omitted, ERPNext computes from today + lead time.",
        "--currency": "ISO code (CNY/USD/HKD). 'RMB' is invalid.",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={
        "name": "Created Sales Order name, e.g. 'SAL-ORD-2026-00012'.",
        "customer": "Echoed customer name.",
        "items": "Array of order item rows.",
        "grand_total": "Order grand total in company currency.",
        "docstatus": "1 (submitted) by default; 0 if --no-submit.",
    },
    examples=(
        {
            "title": "Submit a SO with two items",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json selling order --customer \"Alice Ltd\" "
                       "--item \"WIDGET-001:10:99.50\" --item \"BOLT-M8:50:0.25\"",
            "expect": "Sales Order name + grand_total. docstatus=1.",
        },
    ),
    common_errors=(
        ERR_CUSTOMER_NOT_FOUND, ERR_ITEM_NOT_FOUND, ERR_VALIDATION_GENERIC,
        ERR_NEEDS_BOOTSTRAP, ERR_AUTH,
    ),
    related=("selling deliver", "selling invoice-from-order", "selling order-to-cash"),
)

HELP_DATA[("selling", "quote-to-order")] = HelpRecord(
    summary="Promote an existing Quotation to a Sales Order.",
    process="Quote → Cash (Quotation → Sales Order step)",
    when_to_use=(
        "Use when a previously-created Quotation is now confirmed. "
        "Wraps ERPNext's `make_sales_order` — same as the GUI button."
    ),
    param_notes={
        "QUOTATION": "Quotation document name (positional), e.g. 'SAL-QTN-2026-00007'.",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={
        "name": "Created Sales Order name.",
        "items": "Item rows copied from the quotation.",
        "grand_total": "Echoed.",
    },
    examples=(
        {
            "title": "Convert a quotation",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json selling quote-to-order SAL-QTN-2026-00007",
            "expect": "Sales Order name with the same items as the quotation.",
        },
    ),
    common_errors=(
        {"error": "NotFoundError",
         "cause": "Quotation name doesn't exist.",
         "fix": "Verify with `doc list Quotation --filter \"name=<X>\"`."},
        ERR_VALIDATION_GENERIC, ERR_AUTH,
    ),
    related=("selling deliver", "selling order-to-cash", "selling quote-to-cash"),
)

HELP_DATA[("selling", "deliver")] = HelpRecord(
    summary="Create a Delivery Note from a submitted Sales Order.",
    process="Quote → Cash (Sales Order → Delivery Note)",
    when_to_use=(
        "Use after a Sales Order is submitted, when stock is ready to "
        "ship. Wraps ERPNext's `make_delivery_note`."
    ),
    preconditions=("Sales Order exists and is submitted.",),
    param_notes={
        "SALES_ORDER": "Sales Order name (positional), e.g. 'SAL-ORD-2026-00012'.",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={
        "name": "Created Delivery Note name, e.g. 'MAT-DN-2026-00008'.",
        "items": "Item rows copied from the SO.",
        "docstatus": "1 (submitted) by default.",
    },
    examples=(
        {
            "title": "Ship a confirmed SO",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json selling deliver SAL-ORD-2026-00012",
            "expect": "Delivery Note name + line items.",
        },
    ),
    common_errors=(
        {"error": "NotFoundError",
         "cause": "Sales Order name doesn't exist.",
         "fix": "Confirm with `doc get \"Sales Order\" <name>`."},
        ERR_VALIDATION_GENERIC, ERR_AUTH,
    ),
    related=("selling invoice-from-delivery", "selling order-to-cash"),
)

HELP_DATA[("selling", "invoice-from-order")] = HelpRecord(
    summary="Create a Sales Invoice directly from a Sales Order (no Delivery Note).",
    process="Quote → Cash (alt path: Sales Order → Sales Invoice)",
    when_to_use=(
        "Use for service orders or pre-shipping invoices where no "
        "physical delivery is involved. For physical goods, prefer "
        "`selling deliver` then `selling invoice-from-delivery` so "
        "stock ledger and AR ledger stay aligned."
    ),
    param_notes={
        "SALES_ORDER": "Sales Order name (positional).",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={
        "name": "Created Sales Invoice name, e.g. 'ACC-SINV-2026-00015'.",
        "grand_total": "Invoice grand total.",
        "outstanding_amount": "Amount still owed (typically equal to grand_total at submit).",
    },
    examples=(
        {
            "title": "Bill a service-only SO",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json selling invoice-from-order SAL-ORD-2026-00012",
            "expect": "Sales Invoice name + outstanding_amount.",
        },
    ),
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("selling collect-payment", "selling deliver"),
)

HELP_DATA[("selling", "invoice-from-delivery")] = HelpRecord(
    summary="Create a Sales Invoice from a submitted Delivery Note.",
    process="Quote → Cash (Delivery Note → Sales Invoice)",
    when_to_use=(
        "Use after a Delivery Note has been submitted; pulls the "
        "exact items + qtys from the DN. Most common path for "
        "physical-goods sales."
    ),
    param_notes={
        "DELIVERY_NOTE": "Delivery Note name (positional), e.g. 'MAT-DN-2026-00008'.",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={
        "name": "Created Sales Invoice name.",
        "grand_total": "Invoice grand total.",
        "outstanding_amount": "Amount still owed.",
    },
    examples=(
        {
            "title": "Bill a delivered shipment",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json selling invoice-from-delivery MAT-DN-2026-00008",
            "expect": "Sales Invoice name with grand_total = DN value.",
        },
    ),
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("selling collect-payment", "selling order-to-cash"),
)

HELP_DATA[("selling", "collect-payment")] = HelpRecord(
    summary="Create + submit a Payment Entry against a Sales Invoice.",
    process="Quote → Cash (Sales Invoice → Payment Entry)",
    when_to_use=(
        "Use to record cash/bank receipts that settle a specific "
        "Sales Invoice. For full-amount immediate payment, the "
        "invoice's `outstanding_amount` is used as the default. For "
        "partial payments, pass --paid-amount explicitly."
    ),
    param_notes={
        "SALES_INVOICE": "Sales Invoice name (positional), e.g. 'ACC-SINV-2026-00015'.",
        "--mode-of-payment": "Mode of Payment master name, e.g. 'Cash', 'Bank', 'Credit Card'. "
                              "Must exist in `doc list 'Mode of Payment'`.",
        "--paid-amount": "Amount paid (company currency). Defaults to the invoice's "
                          "`outstanding_amount` for full settlement.",
        "--reference-no": "Bank/cheque reference number for non-cash modes.",
        "--reference-date": "ISO date when the payment was received (for bank reconciliation).",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={
        "name": "Created Payment Entry name, e.g. 'ACC-PAY-2026-00007'.",
        "paid_amount": "Echoed paid amount.",
        "references": "Array of {reference_doctype, reference_name, allocated_amount}.",
    },
    examples=(
        {
            "title": "Full payment by Cash",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json selling collect-payment ACC-SINV-2026-00015 "
                       "--mode-of-payment \"Cash\"",
            "expect": "Payment Entry name; paid_amount = invoice outstanding.",
        },
        {
            "title": "Partial bank payment with reference",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json selling collect-payment ACC-SINV-2026-0042 "
                       "--mode-of-payment \"Bank\" --paid-amount 500 "
                       "--reference-no \"TX123456\" --reference-date 2026-04-21",
            "expect": "Payment Entry with paid_amount=500 and reference fields populated.",
        },
    ),
    common_errors=(
        {"error": "NotFoundError",
         "cause": "Sales Invoice name doesn't exist or is unsubmitted.",
         "fix": "`doc get \"Sales Invoice\" <name>` to confirm; submit it first if needed."},
        {"error": "ValidationError",
         "cause": "`Mode of Payment` doesn't exist in this tenant.",
         "fix": "List with `doc list \"Mode of Payment\" --limit 10` and pass an existing one."},
        ERR_VALIDATION_GENERIC, ERR_AUTH,
    ),
    related=("accounts ar", "selling dashboard"),
)

HELP_DATA[("selling", "order-to-cash")] = HelpRecord(
    summary="End-to-end: Sales Order → Delivery Note → Sales Invoice → Payment Entry, all submitted.",
    process="Quote → Cash (full chain, no Quotation step)",
    when_to_use=(
        "Use when the customer has CONFIRMED the order AND is paying "
        "immediately (cash sale, prepaid sale, invoice-on-delivery "
        "scenario). Submits four documents in one call. If you only "
        "want to ship now and bill later, call the smaller building "
        "blocks (`selling order` then `selling deliver`)."
    ),
    preconditions=(
        "`bootstrap status` shows `ready_for_sales: true`.",
        "Customer master exists (or use `selling onboard-customer`).",
        "Every item code in --item exists in the Item master.",
    ),
    param_notes={
        "--customer": "Customer master name.",
        "--item": _ITEM_SPEC_NOTE,
        "--items-json": _ITEMS_JSON_NOTE,
        "--items-file": _ITEMS_FILE_NOTE,
        "--company": _COMPANY_NOTE,
        "--delivery-date": "ISO date (YYYY-MM-DD). Defaults to today + lead time per item.",
        "--mode-of-payment": "Mode of Payment master name; must exist (e.g. 'Cash', 'Bank').",
    },
    output={
        "workflow": "Constant 'order_to_cash'.",
        "customer": "Echoed customer name.",
        "sales_order": "Created SO name.",
        "delivery_note": "Created DN name.",
        "sales_invoice": "Created SI name.",
        "payment_entry": "Created PE name.",
        "grand_total": "SI grand total in company currency.",
        "paid_amount": "PE paid_amount (typically == grand_total for full payment).",
    },
    examples=(
        {
            "title": "Two-item cash sale, full chain",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json selling order-to-cash --customer \"Alice Ltd\" "
                       "--item \"WIDGET-001:10:99.50\" --item \"BOLT-M8:50:0.25\" "
                       "--mode-of-payment \"Cash\"",
            "expect": "Names of all four created docs + grand_total + paid_amount.",
        },
    ),
    common_errors=(
        ERR_CUSTOMER_NOT_FOUND, ERR_ITEM_NOT_FOUND, ERR_NEEDS_BOOTSTRAP,
        ERR_VALIDATION_GENERIC, ERR_AUTH,
    ),
    related=(
        "selling onboard-customer", "selling quote-to-cash",
        "selling dashboard", "accounts ar",
    ),
)

HELP_DATA[("selling", "quote-to-cash")] = HelpRecord(
    summary="End-to-end: Quotation → Sales Order → Delivery → Invoice → Payment.",
    process="Quote → Cash (full chain WITH Quotation)",
    when_to_use=(
        "Use when the workflow needs a paper-trail Quotation as well "
        "as the downstream submitted documents. Five documents in one "
        "call. For confirmed orders that skip the quote step, use "
        "`selling order-to-cash` instead."
    ),
    param_notes={
        "--customer": "Customer master name.",
        "--item": _ITEM_SPEC_NOTE,
        "--items-json": _ITEMS_JSON_NOTE,
        "--items-file": _ITEMS_FILE_NOTE,
        "--company": _COMPANY_NOTE,
        "--valid-till": "ISO date for quotation expiry. Defaults to ERPNext's setting.",
        "--delivery-date": "ISO date for SO/DN.",
        "--mode-of-payment": "Mode of Payment master name.",
    },
    output={
        "workflow": "Constant 'quote_to_cash'.",
        "quotation": "Created Quotation name.",
        "sales_order": "Created SO name.",
        "delivery_note": "Created DN name.",
        "sales_invoice": "Created SI name.",
        "payment_entry": "Created PE name.",
    },
    examples=(
        {
            "title": "Quote-to-cash with valid-till",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json selling quote-to-cash --customer \"Alice Ltd\" "
                       "--item \"WIDGET-001:10:99.50\" --valid-till 2026-05-30 "
                       "--mode-of-payment \"Bank\"",
            "expect": "Five doc names returned in one envelope.",
        },
    ),
    common_errors=(ERR_CUSTOMER_NOT_FOUND, ERR_ITEM_NOT_FOUND, ERR_AUTH),
    related=("selling order-to-cash", "selling quote"),
)

HELP_DATA[("selling", "onboard-customer")] = HelpRecord(
    summary="Create Customer (if missing) + Sales Order in one step. Idempotent.",
    process="Quote → Cash (zero-to-SO)",
    when_to_use=(
        "Use when you've been told a customer name that may or may "
        "not exist yet AND you want to immediately raise an SO. "
        "Idempotent: re-running with the same customer-name does NOT "
        "create duplicates. Pair with --ensure-items if items might "
        "also be missing."
    ),
    param_notes={
        "--customer": "Customer master name. Created if absent (autoname from this).",
        "--item": _ITEM_SPEC_NOTE,
        "--company": _COMPANY_NOTE,
        "--customer-group": "Customer Group parent. Default 'All Customer Groups' "
                              "is safe in vanilla ERPNext.",
        "--territory": "Territory parent. Default 'All Territories'.",
        "--ensure-items": "If set, also creates any Item that doesn't exist (with "
                            "minimal defaults) before raising the SO.",
    },
    output={
        "workflow": "Constant 'onboard_customer_with_order'.",
        "customer": "Echoed customer name.",
        "sales_order": "Created SO name.",
        "grand_total": "SO grand total.",
    },
    examples=(
        {
            "title": "New customer + SO, auto-create items",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json selling onboard-customer --customer \"NewCo\" "
                       "--item \"GIZMO-001:5:120\" --ensure-items",
            "expect": "Customer + SO created. Item GIZMO-001 created if absent.",
        },
    ),
    common_errors=(
        {"error": "ValidationError",
         "cause": "Customer Group / Territory parent doesn't exist.",
         "fix": "Stick to defaults 'All Customer Groups' / 'All Territories', or "
                "verify with `doc list 'Customer Group'`."},
        ERR_AUTH,
    ),
    related=("selling order-to-cash", "selling order"),
)

HELP_DATA[("selling", "dashboard")] = HelpRecord(
    summary="Customer dashboard: open Sales Orders + outstanding invoices.",
    process="Reconnaissance",
    when_to_use=(
        "Use as a single read-only probe before mutating anything for "
        "a customer. Tells you what's open, what's overdue, and total "
        "outstanding."
    ),
    param_notes={
        "CUSTOMER": "Customer master name (positional).",
    },
    output={
        "customer": "Echoed.",
        "open_sales_orders_count": "Count of submitted, not-fully-billed-or-delivered SOs.",
        "open_sales_orders": "First 20 records (name, dates, status, totals).",
        "outstanding_invoices_count": "Count of submitted SIs with outstanding > 0.",
        "outstanding_invoices": "First 20 records.",
        "total_outstanding": "Sum of all outstanding_amount values.",
    },
    examples=(
        {
            "title": "Inspect a customer before billing",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json selling dashboard \"Alice Ltd\"",
            "expect": "Counts + first-20 lists + total_outstanding.",
        },
    ),
    common_errors=(ERR_CUSTOMER_NOT_FOUND, ERR_AUTH),
    related=("selling list-open", "accounts ar"),
)

HELP_DATA[("selling", "list-open")] = HelpRecord(
    summary="List submitted Sales Orders that are not yet fully delivered/billed/closed.",
    process="Reconnaissance",
    when_to_use=(
        "Use to enumerate work-in-progress orders for a customer or "
        "company. Filter via --customer / --company; --limit to "
        "control page size."
    ),
    param_notes={
        "--customer": "Optional customer filter.",
        "--company": "Optional company filter; defaults to session context.",
        "--limit": "Page size. Default 20.",
    },
    output={
        "(array)": "Each row: name, customer, transaction_date, delivery_date, "
                   "status, per_delivered, per_billed, grand_total, currency.",
    },
    examples=(
        {
            "title": "All open SOs for a customer",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json selling list-open --customer \"Alice Ltd\"",
            "expect": "Array of open Sales Orders.",
        },
    ),
    common_errors=(ERR_AUTH,),
    related=("selling dashboard", "selling deliver", "selling invoice-from-order"),
)


# ── buying ───────────────────────────────────────────────────────────

HELP_DATA[("buying", "material-request")] = HelpRecord(
    summary="Create a Material Request (internal request to procure / transfer / issue).",
    process="Procure → Pay (step 0: planning)",
    when_to_use=(
        "Use to capture an internal need for material BEFORE deciding "
        "to RFQ or PO. Use --purpose Purchase to feed an RFQ/PO chain; "
        "use Material Transfer / Material Issue / Manufacture for "
        "internal flows."
    ),
    param_notes={
        "--item": _ITEM_SPEC_NOTE,
        "--items-json": _ITEMS_JSON_NOTE,
        "--items-file": _ITEMS_FILE_NOTE,
        "--purpose": "Material Request Type. 'Purchase' is the default and feeds "
                      "RFQ/PO. The other choices (Material Transfer, Material Issue, "
                      "Manufacture, Customer Provided) are internal-flow MR types.",
        "--company": _COMPANY_NOTE,
        "--schedule-date": "ISO date when the materials are needed.",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={
        "name": "Created MR name, e.g. 'MAT-MR-2026-00003'.",
        "items": "Item rows.",
        "docstatus": "1 by default; 0 if --no-submit.",
    },
    examples=(
        {
            "title": "Standard purchase MR",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json buying material-request --item \"PART-A:50:0\" "
                       "--schedule-date 2026-05-15",
            "expect": "MR name + items.",
        },
    ),
    common_errors=(ERR_ITEM_NOT_FOUND, ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("buying rfq-from-mr", "buying po-from-mr"),
)

HELP_DATA[("buying", "order")] = HelpRecord(
    summary="Create a Purchase Order (submits by default).",
    process="Procure → Pay (step 1: PO)",
    when_to_use=(
        "Use when supplier + items + price are agreed and you want a "
        "submitted PO immediately. For sourcing flows, prefer "
        "`buying material-request` first."
    ),
    param_notes={
        "--supplier": "Supplier master name.",
        "--item": _ITEM_SPEC_NOTE,
        "--items-json": _ITEMS_JSON_NOTE,
        "--items-file": _ITEMS_FILE_NOTE,
        "--company": _COMPANY_NOTE,
        "--schedule-date": "ISO date by which the supplier should deliver.",
        "--currency": "ISO code; falls back to session default.",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={
        "name": "Created PO name, e.g. 'PUR-ORD-2026-00010'.",
        "supplier": "Echoed.",
        "grand_total": "PO total in company currency.",
    },
    examples=(
        {
            "title": "Two-line PO",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json buying order --supplier \"PartsCo Inc\" "
                       "--item \"PART-A:20:15\" --item \"PART-B:10:25\" "
                       "--schedule-date 2026-05-10",
            "expect": "PO name + grand_total.",
        },
    ),
    common_errors=(
        ERR_SUPPLIER_NOT_FOUND, ERR_ITEM_NOT_FOUND, ERR_VALIDATION_GENERIC, ERR_AUTH,
    ),
    related=("buying receive", "buying procure-to-pay", "buying bill-from-po"),
)

HELP_DATA[("buying", "supplier-quote")] = HelpRecord(
    summary="Create a Supplier Quotation (received-from-supplier price record).",
    process="Procure → Pay (sourcing record)",
    when_to_use=(
        "Use to record what a supplier has quoted you. A submitted "
        "Supplier Quotation can be promoted to a PO via `buying po-from-sq`."
    ),
    param_notes={
        "--supplier": "Supplier master name.",
        "--item": _ITEM_SPEC_NOTE,
        "--items-json": _ITEMS_JSON_NOTE,
        "--items-file": _ITEMS_FILE_NOTE,
        "--company": _COMPANY_NOTE,
        "--valid-till": "ISO date the quote is valid until.",
        "--submit": "Submit the quotation (default is to leave as DRAFT).",
    },
    output={"name": "Created Supplier Quotation name."},
    examples=(
        {
            "title": "Record a supplier's quote",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json buying supplier-quote --supplier \"PartsCo Inc\" "
                       "--item \"PART-A:1:14.50\" --valid-till 2026-06-01",
            "expect": "Supplier Quotation name.",
        },
    ),
    common_errors=(ERR_SUPPLIER_NOT_FOUND, ERR_ITEM_NOT_FOUND, ERR_AUTH),
    related=("buying po-from-sq", "buying order"),
)

HELP_DATA[("buying", "rfq-from-mr")] = HelpRecord(
    summary="Create a Request For Quotation from a submitted Material Request.",
    process="Procure → Pay (sourcing)",
    when_to_use=(
        "Use to solicit prices from one or more suppliers based on a "
        "Material Request. Wraps ERPNext's `make_request_for_quotation`."
    ),
    param_notes={
        "MATERIAL_REQUEST": "Material Request name (positional).",
        "--supplier": "Supplier name to request from. Repeatable for multi-supplier RFQ.",
    },
    output={"name": "Created RFQ name.", "suppliers": "Suppliers attached."},
    examples=(
        {
            "title": "RFQ to two suppliers",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json buying rfq-from-mr MAT-MR-2026-00003 "
                       "--supplier \"PartsCo Inc\" --supplier \"AltCo Ltd\"",
            "expect": "RFQ name with both suppliers attached.",
        },
    ),
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("buying po-from-mr", "buying order"),
)

HELP_DATA[("buying", "po-from-mr")] = HelpRecord(
    summary="Create a Purchase Order from a submitted Material Request.",
    process="Procure → Pay (MR → PO)",
    when_to_use=(
        "Use after the MR is approved and a supplier has been chosen. "
        "ERPNext's `make_purchase_order` copies forward MR items."
    ),
    param_notes={
        "MATERIAL_REQUEST": "MR name (positional).",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={"name": "Created PO name."},
    examples=(
        {
            "title": "Promote MR to PO",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json buying po-from-mr MAT-MR-2026-00003",
            "expect": "PO name with MR items copied forward.",
        },
    ),
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("buying receive",),
)

HELP_DATA[("buying", "po-from-sq")] = HelpRecord(
    summary="Promote a Supplier Quotation to a Purchase Order.",
    process="Procure → Pay (SQ → PO)",
    when_to_use="Use after the supplier's quote is accepted.",
    param_notes={
        "SUPPLIER_QUOTATION": "SQ name (positional).",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={"name": "Created PO name."},
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("buying receive", "buying procure-to-pay"),
)

HELP_DATA[("buying", "receive")] = HelpRecord(
    summary="Create a Purchase Receipt from a submitted Purchase Order.",
    process="Procure → Pay (PO → Receipt)",
    when_to_use=(
        "Use when goods physically arrive against a PO. Submitting "
        "the Purchase Receipt updates stock ledger immediately."
    ),
    param_notes={
        "PURCHASE_ORDER": "PO name (positional), e.g. 'PUR-ORD-2026-00010'.",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={"name": "Created PR name, e.g. 'MAT-PRE-2026-00006'."},
    examples=(
        {
            "title": "Receive a PO",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json buying receive PUR-ORD-2026-00010",
            "expect": "Purchase Receipt name; stock balances updated.",
        },
    ),
    common_errors=(
        {"error": "ValidationError",
         "cause": "Item has zero `valuation_rate` and no manual `basic_rate`.",
         "fix": ERR_ITEM_NO_VALUATION["fix"]},
        ERR_VALIDATION_GENERIC, ERR_AUTH,
    ),
    related=("buying bill-from-receipt",),
)

HELP_DATA[("buying", "bill-from-po")] = HelpRecord(
    summary="Create a Purchase Invoice from a submitted Purchase Order.",
    process="Procure → Pay (PO → Bill, no receipt)",
    when_to_use=(
        "Use for service POs or pre-paid bills. For physical goods, "
        "prefer `buying receive` then `buying bill-from-receipt`."
    ),
    param_notes={
        "PURCHASE_ORDER": "PO name (positional).",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={"name": "Created Purchase Invoice name."},
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("buying pay", "buying bill-from-receipt"),
)

HELP_DATA[("buying", "bill-from-receipt")] = HelpRecord(
    summary="Create a Purchase Invoice from a submitted Purchase Receipt.",
    process="Procure → Pay (Receipt → Bill)",
    when_to_use="Use after the goods are received. Standard physical-goods path.",
    param_notes={
        "PURCHASE_RECEIPT": "PR name (positional).",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={"name": "Created Purchase Invoice name."},
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("buying pay",),
)

HELP_DATA[("buying", "pay")] = HelpRecord(
    summary="Create + submit a Payment Entry against a Purchase Invoice.",
    process="Procure → Pay (Invoice → Payment)",
    when_to_use="Use to pay a supplier bill.",
    param_notes={
        "PURCHASE_INVOICE": "Purchase Invoice name (positional).",
        "--mode-of-payment": "Mode of Payment master name.",
        "--paid-amount": "Amount paid; defaults to invoice's outstanding_amount.",
        "--reference-no": "Bank/cheque reference for non-cash modes.",
        "--reference-date": "ISO date the payment was made.",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={
        "name": "Created Payment Entry name.",
        "paid_amount": "Echoed amount.",
    },
    examples=(
        {
            "title": "Settle a supplier bill in full",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json buying pay ACC-PINV-2026-0050 --mode-of-payment \"Bank\"",
            "expect": "Payment Entry name; paid_amount = invoice outstanding.",
        },
    ),
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("accounts ap", "accounts pay"),
)

HELP_DATA[("buying", "procure-to-pay")] = HelpRecord(
    summary="End-to-end: PO → Purchase Receipt → Purchase Invoice → Payment Entry.",
    process="Procure → Pay (full chain)",
    when_to_use=(
        "Use when supplier + items + price are agreed AND payment is "
        "happening immediately. Submits four documents in one call. "
        "For multi-step approval flows, call building blocks "
        "individually."
    ),
    preconditions=(
        "`bootstrap status` shows `ready_for_purchase: true`.",
        "Supplier master exists (or use `buying onboard-supplier`).",
    ),
    param_notes={
        "--supplier": "Supplier master name.",
        "--item": _ITEM_SPEC_NOTE,
        "--items-json": _ITEMS_JSON_NOTE,
        "--items-file": _ITEMS_FILE_NOTE,
        "--company": _COMPANY_NOTE,
        "--schedule-date": "ISO date for PO/PR.",
        "--mode-of-payment": "Mode of Payment master name.",
    },
    output={
        "workflow": "Constant 'procure_to_pay'.",
        "supplier": "Echoed.",
        "purchase_order": "Created PO name.",
        "purchase_receipt": "Created PR name.",
        "purchase_invoice": "Created PI name.",
        "payment_entry": "Created PE name.",
    },
    examples=(
        {
            "title": "Two-line procure-to-pay",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json buying procure-to-pay --supplier \"PartsCo Inc\" "
                       "--item \"PART-A:20:15\" --item \"PART-B:10:25\" "
                       "--schedule-date 2026-05-10",
            "expect": "Names of all four created docs in one envelope.",
        },
    ),
    common_errors=(
        ERR_SUPPLIER_NOT_FOUND, ERR_ITEM_NOT_FOUND, ERR_NEEDS_BOOTSTRAP,
        {"error": "ValidationError",
         "cause": "Item with zero valuation rate; PR submission fails accounting check.",
         "fix": ERR_ITEM_NO_VALUATION["fix"]},
        ERR_VALIDATION_GENERIC, ERR_AUTH,
    ),
    related=("buying onboard-supplier", "buying receive", "accounts ap"),
)

HELP_DATA[("buying", "onboard-supplier")] = HelpRecord(
    summary="Create Supplier (if missing) + Purchase Order in one step. Idempotent.",
    process="Procure → Pay (zero-to-PO)",
    when_to_use=(
        "Use when you've been told a supplier name that may or may "
        "not exist yet AND you want to immediately raise a PO."
    ),
    param_notes={
        "--supplier": "Supplier master name.",
        "--item": _ITEM_SPEC_NOTE,
        "--items-json": _ITEMS_JSON_NOTE,
        "--items-file": _ITEMS_FILE_NOTE,
        "--company": _COMPANY_NOTE,
        "--supplier-group": "Default 'All Supplier Groups' is safe.",
        "--ensure-items": "If set, also creates missing Items with minimal defaults.",
    },
    output={
        "workflow": "Constant 'onboard_supplier_with_order'.",
        "supplier": "Echoed.",
        "purchase_order": "Created PO name.",
    },
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("buying procure-to-pay", "buying order"),
)

HELP_DATA[("buying", "dashboard")] = HelpRecord(
    summary="Supplier dashboard: open Purchase Orders + outstanding bills.",
    process="Reconnaissance",
    when_to_use="Use as a single read-only probe before paying or ordering more from a supplier.",
    param_notes={"SUPPLIER": "Supplier master name (positional)."},
    output={
        "supplier": "Echoed.",
        "open_purchase_orders": "List of submitted, not-fully-billed-or-received POs.",
        "outstanding_bills": "List of submitted PIs with outstanding > 0.",
        "total_outstanding": "Sum of all outstanding amounts.",
    },
    common_errors=(ERR_SUPPLIER_NOT_FOUND, ERR_AUTH),
    related=("buying list-open", "accounts ap"),
)

HELP_DATA[("buying", "list-open")] = HelpRecord(
    summary="List submitted Purchase Orders that aren't fully received/billed/closed.",
    process="Reconnaissance",
    when_to_use="Use to enumerate open POs for a supplier or company.",
    param_notes={
        "--supplier": "Optional supplier filter.",
        "--company": "Optional company filter.",
        "--limit": "Page size. Default 20.",
    },
    output={
        "(array)": "Each row: name, supplier, transaction_date, schedule_date, "
                   "status, per_received, per_billed, grand_total.",
    },
    common_errors=(ERR_AUTH,),
    related=("buying dashboard", "buying receive"),
)


# ── stock ────────────────────────────────────────────────────────────

_STOCK_RATE_NOTE = (
    "For stock commands, the CLI auto-promotes `rate` → `basic_rate` "
    "and sets `set_basic_rate_manually=1` so the row's price isn't "
    "overwritten by the Item master's `valuation_rate=0`. Pass `rate` "
    "on every row OR set `valuation_rate` on the Item master first; "
    "otherwise submission fails with a 'maintain valuation rate' error."
)

HELP_DATA[("stock", "transfer")] = HelpRecord(
    summary="Move stock from one warehouse to another (Stock Entry, Material Transfer).",
    process="Inventory (intra-company movement)",
    when_to_use=(
        "Use for warehouse-to-warehouse moves WITHOUT a sales/purchase "
        "context. Wraps a 'Material Transfer' Stock Entry."
    ),
    param_notes={
        "--item": _ITEM_SPEC_NOTE + " " + _STOCK_RATE_NOTE,
        "--items-json": _ITEMS_JSON_NOTE,
        "--items-file": _ITEMS_FILE_NOTE,
        "--source": "Source Warehouse name (e.g. 'Stores - ACME').",
        "--target": "Target Warehouse name.",
        "--company": _COMPANY_NOTE,
        "--posting-date": "ISO date for the Stock Entry. Defaults to today.",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={
        "name": "Created Stock Entry name.",
        "items": "Item rows with allocated qty per row.",
        "docstatus": "1 (submitted) by default.",
    },
    examples=(
        {
            "title": "Transfer 50 widgets to shop floor",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json stock transfer --item \"WIDGET-001:50\" "
                       "--source \"Stores - ACME\" --target \"Shop Floor - ACME\"",
            "expect": "Stock Entry name; balances updated atomically.",
        },
    ),
    common_errors=(
        {"error": "ValidationError",
         "cause": "Source warehouse has insufficient on-hand qty.",
         "fix": "Check current levels with `stock levels --item <X> --warehouse <SRC>` "
                 "before transferring."},
        ERR_VALIDATION_GENERIC, ERR_AUTH,
    ),
    related=("stock levels", "stock issue", "stock receipt"),
)

HELP_DATA[("stock", "issue")] = HelpRecord(
    summary="Issue stock out of a warehouse (consume / write-off, no target warehouse).",
    process="Inventory (outflow without sale)",
    when_to_use=(
        "Use for write-offs, samples given out, internal consumption "
        "where ERPNext isn't tracking the receiver. Wraps a 'Material "
        "Issue' Stock Entry."
    ),
    param_notes={
        "--item": _ITEM_SPEC_NOTE + " " + _STOCK_RATE_NOTE,
        "--items-json": _ITEMS_JSON_NOTE,
        "--items-file": _ITEMS_FILE_NOTE,
        "--source": "Source Warehouse name.",
        "--company": _COMPANY_NOTE,
        "--posting-date": "ISO date. Defaults to today.",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={"name": "Created Stock Entry name."},
    examples=(
        {
            "title": "Write off 10 damaged units",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json stock issue --item \"WIDGET-001:10:11.5\" "
                       "--source \"Stores - ACME\"",
            "expect": "Stock Entry name; balance decremented.",
        },
    ),
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("stock levels", "stock transfer"),
)

HELP_DATA[("stock", "receipt")] = HelpRecord(
    summary="Receive stock into a warehouse outside a Purchase Receipt (e.g., opening balances).",
    process="Inventory (inflow without purchase)",
    when_to_use=(
        "Use for opening-stock entries, found inventory, customer "
        "returns recorded as fresh stock — anywhere a PR doesn't fit. "
        "Wraps a 'Material Receipt' Stock Entry. For real purchases, "
        "use `buying receive` (driven by a PO) instead."
    ),
    param_notes={
        "--item": _ITEM_SPEC_NOTE + " " + _STOCK_RATE_NOTE,
        "--items-json": _ITEMS_JSON_NOTE + " "
                          "Per-row `allow_zero_valuation_rate: 1` if you really need "
                          "zero-cost rows.",
        "--items-file": _ITEMS_FILE_NOTE,
        "--target": "Target Warehouse name.",
        "--company": _COMPANY_NOTE,
        "--posting-date": "ISO date. Defaults to today.",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={"name": "Created Stock Entry name."},
    examples=(
        {
            "title": "Bulk opening balance",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json stock receipt "
                       "--items-json '[{\"item_code\":\"WIDGET-001\",\"qty\":1400,\"rate\":11.5},"
                       "{\"item_code\":\"BOLT-M8\",\"qty\":50,\"rate\":0.25}]' "
                       "--target \"Stores - ACME\" --company \"ACME\"",
            "expect": "One Stock Entry submitted with both rows.",
        },
    ),
    common_errors=(
        {"error": "ValidationError",
         "cause": "Item rows have zero rate AND zero valuation_rate on Item master.",
         "fix": ERR_ITEM_NO_VALUATION["fix"]},
        ERR_VALIDATION_GENERIC, ERR_AUTH,
    ),
    related=("stock levels", "stock transfer", "buying receive"),
)

HELP_DATA[("stock", "reconcile")] = HelpRecord(
    summary="Adjust stock quantities to match a physical count (Stock Reconciliation).",
    process="Inventory (cycle counting)",
    when_to_use=(
        "Use after a physical count to true-up the system. The "
        "Reconciliation row format is different: each row needs "
        "item_code, warehouse, qty, valuation_rate."
    ),
    param_notes={
        "--items-json": "Inline JSON. Each row: "
                          "`{\"item_code\":\"X\",\"warehouse\":\"Y\",\"qty\":N,\"valuation_rate\":R}`.",
        "--items-file": "Path to a JSON file with the same shape.",
        "--company": _COMPANY_NOTE,
        "--posting-date": "ISO date. Defaults to today.",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={"name": "Created Stock Reconciliation name."},
    examples=(
        {
            "title": "True up two SKUs",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json stock reconcile --items-json "
                       "'[{\"item_code\":\"WIDGET-001\",\"warehouse\":\"Stores - ACME\",\"qty\":1380,\"valuation_rate\":11.5}]'",
            "expect": "Stock Reconciliation submitted; ledger adjusted.",
        },
    ),
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("stock levels",),
)

HELP_DATA[("stock", "levels")] = HelpRecord(
    summary="Query current stock balances by item / warehouse.",
    process="Reconnaissance",
    when_to_use=(
        "Use to read on-hand qty before a movement. Filter by --item "
        "or --warehouse (or both) to narrow."
    ),
    param_notes={
        "--item": "Item Code filter.",
        "--warehouse": "Warehouse name filter.",
        "--company": "Company filter.",
        "--limit": "Page size. Default 100.",
    },
    output={
        "(array)": "Each row: item_code, warehouse, actual_qty, valuation_rate, "
                   "stock_uom (varies with ERPNext version).",
    },
    examples=(
        {
            "title": "On-hand for one SKU across all warehouses",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json stock levels --item \"WIDGET-001\"",
            "expect": "Array of {warehouse, actual_qty} rows.",
        },
    ),
    common_errors=(ERR_AUTH,),
    related=("stock warehouse",),
)

HELP_DATA[("stock", "warehouse")] = HelpRecord(
    summary="Summary of a single warehouse: items + total qty.",
    process="Reconnaissance",
    when_to_use="Use to inspect what's stored in a specific warehouse.",
    param_notes={"WAREHOUSE": "Warehouse name (positional)."},
    output={"warehouse": "Echoed.", "items": "Per-item totals."},
    common_errors=(ERR_AUTH,),
    related=("stock levels",),
)


# ── accounts ─────────────────────────────────────────────────────────

HELP_DATA[("accounts", "receive-payment")] = HelpRecord(
    summary="Create + submit a Payment Entry against a Sales Invoice (alias for selling collect-payment).",
    process="Accounting / AR",
    when_to_use=(
        "Functionally identical to `selling collect-payment`. Pick "
        "this when you're working in an 'accounting' mental model "
        "rather than a 'selling' one — they both call the same "
        "underlying builder."
    ),
    param_notes={
        "SALES_INVOICE": "Sales Invoice name (positional).",
        "--mode-of-payment": "Mode of Payment master name.",
        "--paid-amount": "Defaults to invoice's outstanding_amount.",
        "--reference-no": "Bank/cheque reference for non-cash modes.",
        "--reference-date": "ISO date.",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={"name": "Payment Entry name.", "paid_amount": "Echoed."},
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("selling collect-payment", "accounts ar"),
)

HELP_DATA[("accounts", "pay")] = HelpRecord(
    summary="Create + submit a Payment Entry against a Purchase Invoice (alias for buying pay).",
    process="Accounting / AP",
    when_to_use="Same as `buying pay`. Pick whichever fits your mental model.",
    param_notes={
        "PURCHASE_INVOICE": "Purchase Invoice name (positional).",
        "--mode-of-payment": "Mode of Payment master name.",
        "--paid-amount": "Defaults to invoice's outstanding_amount.",
        "--reference-no": "Reference number.",
        "--reference-date": "ISO date.",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={"name": "Payment Entry name.", "paid_amount": "Echoed."},
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("buying pay", "accounts ap"),
)

HELP_DATA[("accounts", "journal")] = HelpRecord(
    summary="Post a balanced Journal Entry across multiple accounts.",
    process="Accounting / GL",
    when_to_use=(
        "Use for adjustments / corrections / non-invoice cash moves "
        "where AR/AP doesn't fit. The accounts array MUST balance: "
        "sum of debits == sum of credits in account currency."
    ),
    param_notes={
        "--accounts-json": "Inline JSON array. Each row: "
                            "`{\"account\":\"<Account name>\","
                            "\"debit_in_account_currency\":<n>}` OR "
                            "`{\"account\":\"<X>\",\"credit_in_account_currency\":<n>}`. "
                            "For party rows add `party_type` (Customer|Supplier|Employee) and "
                            "`party`.",
        "--accounts-file": "Path to a JSON file with the same shape.",
        "--company": _COMPANY_NOTE,
        "--posting-date": "ISO date.",
        "--remark": "Free-text user remark.",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={"name": "Created Journal Entry name.", "total_debit": "Sum of debits."},
    examples=(
        {
            "title": "Balanced revenue recognition",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json accounts journal --accounts-json "
                       "'[{\"account\":\"Cash - ACME\",\"debit_in_account_currency\":1000},"
                       "{\"account\":\"Revenue - ACME\",\"credit_in_account_currency\":1000}]' "
                       "--company \"ACME Ltd\"",
            "expect": "Journal Entry name; total_debit=total_credit=1000.",
        },
    ),
    common_errors=(
        {"error": "WorkflowError",
         "cause": "Debits != credits.",
         "fix": "Sum the debit_in_account_currency vs credit_in_account_currency "
                 "across all rows; they must match exactly."},
        {"error": "ValidationError",
         "cause": "An account name doesn't exist or is a parent (group) account.",
         "fix": "Verify with `doc list Account --filter \"company=<C>\" "
                 "--filter \"is_group=0\"`. Use leaf accounts only."},
        ERR_AUTH,
    ),
    related=("accounts snapshot", "doc list Account"),
)

HELP_DATA[("accounts", "reconcile")] = HelpRecord(
    summary="Match unallocated payments to invoices for a customer or supplier.",
    process="Accounting / AR & AP",
    when_to_use=(
        "Use when there are unallocated payment entries that need to "
        "be applied against specific invoices. Wraps Payment "
        "Reconciliation."
    ),
    param_notes={
        "--party-type": "'Customer' or 'Supplier'.",
        "--party": "Party master name.",
        "--company": _COMPANY_NOTE + " REQUIRED.",
        "--from-date": "ISO date — start of reconciliation window.",
        "--to-date": "ISO date — end of window.",
        "--receivable-payable-account": "GL account; defaults to company default.",
    },
    output={
        "matched": "List of (payment, invoice, allocated_amount) triples.",
        "unmatched_payments": "Payments still unallocated.",
        "unmatched_invoices": "Invoices still outstanding.",
    },
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("accounts ar", "accounts ap"),
)

HELP_DATA[("accounts", "ar")] = HelpRecord(
    summary="Accounts Receivable: outstanding Sales Invoices.",
    process="Accounting / AR",
    when_to_use="Use for an AR aging snapshot or per-customer AR.",
    param_notes={
        "--customer": "Optional customer filter.",
        "--company": "Company filter; defaults to session context.",
        "--limit": "Page size. Default 100.",
    },
    output={
        "(array)": "Each row: name, customer, grand_total, outstanding_amount, "
                   "due_date, status.",
    },
    examples=(
        {
            "title": "Per-customer AR",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json accounts ar --customer \"Alice Ltd\"",
            "expect": "Array of outstanding SIs for Alice Ltd.",
        },
    ),
    common_errors=(ERR_AUTH,),
    related=("accounts snapshot", "selling dashboard"),
)

HELP_DATA[("accounts", "ap")] = HelpRecord(
    summary="Accounts Payable: outstanding Purchase Invoices.",
    process="Accounting / AP",
    when_to_use="Use for an AP aging snapshot or per-supplier AP.",
    param_notes={
        "--supplier": "Optional supplier filter.",
        "--company": "Company filter.",
        "--limit": "Page size. Default 100.",
    },
    output={
        "(array)": "Each row: name, supplier, grand_total, outstanding_amount, "
                   "due_date, status.",
    },
    common_errors=(ERR_AUTH,),
    related=("accounts snapshot", "buying dashboard"),
)

HELP_DATA[("accounts", "snapshot")] = HelpRecord(
    summary="Quick AR/AP cashflow position for a company.",
    process="Accounting / cashflow",
    when_to_use=(
        "Use as a single overview probe. Returns total receivables, "
        "total payables, and net cash position."
    ),
    param_notes={"--company": _COMPANY_NOTE + " REQUIRED if no session default."},
    output={
        "company": "Echoed.",
        "total_receivables": "Sum of outstanding SI amounts.",
        "total_payables": "Sum of outstanding PI amounts.",
        "net_position": "receivables - payables.",
    },
    common_errors=(
        {"error": "UsageError",
         "cause": "No --company and no session-default company set.",
         "fix": "Pass --company explicitly or `session set-context --company <X>`."},
        ERR_AUTH,
    ),
    related=("accounts ar", "accounts ap"),
)


# ── manufacturing ────────────────────────────────────────────────────

HELP_DATA[("manufacturing", "bom")] = HelpRecord(
    summary="Create a Bill of Materials for a finished good.",
    process="Manufacturing (BOM)",
    when_to_use=(
        "Use to define which raw materials go into a finished item, "
        "so Work Orders can compute consumption automatically."
    ),
    param_notes={
        "--item": "Item Code of the FINISHED good (the BOM's `item`).",
        "--items-json": "Required. Inline JSON of raw material rows: "
                          "`[{\"item_code\":\"RAW-1\",\"qty\":2,\"rate\":5}, ...]`.",
        "--items-file": "Path to a JSON file with the raw materials.",
        "--quantity": "BOM quantity (the FG batch size). Default 1.",
        "--company": _COMPANY_NOTE,
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={
        "name": "Created BOM name, e.g. 'BOM-PROD-A-001'.",
        "items": "Raw material rows.",
        "total_cost": "BOM total cost in company currency.",
    },
    examples=(
        {
            "title": "BOM for PROD-A from two raw materials",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json manufacturing bom --item \"PROD-A\" "
                       "--items-json '[{\"item_code\":\"RAW-1\",\"qty\":2,\"rate\":5},"
                       "{\"item_code\":\"RAW-2\",\"qty\":1,\"rate\":3}]'",
            "expect": "BOM name + 2 rows + total_cost.",
        },
    ),
    common_errors=(ERR_ITEM_NOT_FOUND, ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("manufacturing work-order", "manufacturing make-from-bom"),
)

HELP_DATA[("manufacturing", "work-order")] = HelpRecord(
    summary="Create a Work Order against a BOM.",
    process="Manufacturing (Work Order)",
    when_to_use="Use to plan a production run for a specific quantity.",
    param_notes={
        "--bom": "BOM name.",
        "--qty": "Quantity to produce (in FG units).",
        "--production-item": "Optional override for the FG item code (defaults to BOM's item).",
        "--company": _COMPANY_NOTE,
        "--fg-warehouse": "Finished Goods warehouse where the output lands.",
        "--wip-warehouse": "Work-In-Progress warehouse for raw materials.",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={
        "name": "Created Work Order name.",
        "production_item": "FG code being made.",
        "qty": "Echoed quantity.",
    },
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("manufacturing issue-materials", "manufacturing finish"),
)

HELP_DATA[("manufacturing", "issue-materials")] = HelpRecord(
    summary="Issue raw materials from WIP to the Work Order (Material Transfer for Manufacture).",
    process="Manufacturing (raw issuance)",
    when_to_use="Use after the Work Order is submitted and you need to consume raw materials.",
    param_notes={
        "WORK_ORDER": "Work Order name (positional).",
        "--qty": "Optional override for the qty to issue. Defaults to required qty.",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={"name": "Created Stock Entry name."},
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("manufacturing finish",),
)

HELP_DATA[("manufacturing", "finish")] = HelpRecord(
    summary="Mark a Work Order's qty as finished (Manufacture Stock Entry into FG warehouse).",
    process="Manufacturing (FG completion)",
    when_to_use="Use when production is complete and the FG should appear in stock.",
    param_notes={
        "WORK_ORDER": "Work Order name (positional).",
        "--qty": "Optional finished qty (default: full WO).",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={"name": "Created Stock Entry name (Manufacture type)."},
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("stock levels",),
)

HELP_DATA[("manufacturing", "make-from-bom")] = HelpRecord(
    summary="End-to-end: BOM → Work Order → (raw issue) → Manufacture (finished goods).",
    process="Manufacturing (full chain)",
    when_to_use=(
        "Use to run a production cycle in one call. Materials flow "
        "WIP → Manufacture → FG warehouse. Pass --skip-material-issue "
        "if you've already issued materials separately."
    ),
    param_notes={
        "--bom": "BOM name.",
        "--qty": "Qty to produce.",
        "--company": _COMPANY_NOTE,
        "--fg-warehouse": "Finished Goods warehouse.",
        "--wip-warehouse": "WIP warehouse for raw materials.",
        "--skip-material-issue": "If set, skip the material issuance step (you "
                                    "already did it manually).",
    },
    output={
        "workflow": "Constant 'make_from_bom'.",
        "work_order": "Created WO name.",
        "material_issue": "Created Stock Entry name (or null if skipped).",
        "manufacture": "Created Stock Entry name for the Manufacture step.",
    },
    examples=(
        {
            "title": "Make 10 of PROD-A end-to-end",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json manufacturing make-from-bom --bom \"BOM-PROD-A-001\" "
                       "--qty 10 --fg-warehouse \"Finished Goods - ACME\" "
                       "--wip-warehouse \"Work In Progress - ACME\"",
            "expect": "Three doc names: WO + material_issue + manufacture.",
        },
    ),
    common_errors=(ERR_ITEM_NO_VALUATION, ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("manufacturing bom", "stock levels"),
)


# ── crm ──────────────────────────────────────────────────────────────

HELP_DATA[("crm", "lead")] = HelpRecord(
    summary="Create a Lead (capture a sales prospect).",
    process="Lead → Customer (step 1: capture)",
    when_to_use="Use to record a fresh inbound prospect before they become a Customer.",
    param_notes={
        "--name": "Lead name (the value of `lead_name`).",
        "--company-name": "Their company name (separate from the system Company).",
        "--email": "Email address.",
        "--mobile": "Mobile number.",
        "--source": "Lead Source master name (e.g. 'Walk In', 'Existing Customer').",
    },
    output={"name": "Created Lead name."},
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("crm lead-to-opportunity", "crm lead-to-customer"),
)

HELP_DATA[("crm", "lead-to-opportunity")] = HelpRecord(
    summary="Promote a Lead to an Opportunity.",
    process="Lead → Customer (step 2: qualify)",
    when_to_use="Use when a lead becomes a real sales opportunity.",
    param_notes={"LEAD": "Lead name (positional)."},
    output={"name": "Created Opportunity name."},
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("crm opportunity-to-quote",),
)

HELP_DATA[("crm", "opportunity-to-quote")] = HelpRecord(
    summary="Create a Quotation from an Opportunity.",
    process="Lead → Customer (step 3: quote)",
    when_to_use=(
        "Use to draft a quotation off an Opportunity. Pass --item / "
        "--items-json to override the items; otherwise the opportunity's "
        "items copy forward."
    ),
    param_notes={
        "OPPORTUNITY": "Opportunity name (positional).",
        "--item": _ITEM_SPEC_NOTE,
        "--items-json": _ITEMS_JSON_NOTE,
        "--items-file": _ITEMS_FILE_NOTE,
    },
    output={"name": "Created Quotation name."},
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("selling quote-to-cash",),
)

HELP_DATA[("crm", "lead-to-customer")] = HelpRecord(
    summary="Convert a Lead directly to a Customer.",
    process="Lead → Customer (skip Opportunity)",
    when_to_use="Use when a lead is ready to become a customer with no opportunity stage.",
    param_notes={
        "LEAD": "Lead name (positional).",
        "--customer-group": "Default 'All Customer Groups'.",
        "--territory": "Default 'All Territories'.",
    },
    output={"name": "Created Customer name."},
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("selling order-to-cash", "selling onboard-customer"),
)

HELP_DATA[("crm", "lead-to-quotation")] = HelpRecord(
    summary="One-shot: create Lead (if needed) and a Quotation.",
    process="Lead → Customer (zero-to-quote)",
    when_to_use=(
        "Use when given just a person/company name and items — "
        "creates the Lead and a Quotation in one call."
    ),
    param_notes={
        "--name": "Lead name.",
        "--item": _ITEM_SPEC_NOTE,
        "--items-json": _ITEMS_JSON_NOTE,
        "--items-file": _ITEMS_FILE_NOTE,
        "--company-name": "Their company name.",
        "--email": "Email.",
        "--mobile": "Mobile number.",
    },
    output={"lead": "Lead name.", "quotation": "Quotation name."},
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("selling quote-to-cash",),
)


# ── hr ───────────────────────────────────────────────────────────────

HELP_DATA[("hr", "employee")] = HelpRecord(
    summary="Create an Employee record.",
    process="HR (master setup)",
    when_to_use="Use to add a single employee. For full new-hire flow, prefer `hr onboard`.",
    param_notes={
        "--first-name": "First name (required).",
        "--last-name": "Last name.",
        "--gender": "Gender; default 'Prefer not to say'.",
        "--dob": "Date of birth (ISO YYYY-MM-DD).",
        "--joining": "Date of joining (ISO).",
        "--company": _COMPANY_NOTE,
        "--department": "Department master name.",
        "--designation": "Designation master name.",
        "--user-id": "Existing User email; links Employee to a User account.",
    },
    output={"name": "Created Employee name (HR-EMP-XXXXX)."},
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("hr onboard", "hr leave"),
)

HELP_DATA[("hr", "leave")] = HelpRecord(
    summary="Apply for leave on behalf of an employee.",
    process="HR (leave)",
    when_to_use="Use to record a leave application.",
    param_notes={
        "--employee": "Employee name (HR-EMP-XXXXX).",
        "--type": "Leave Type master name (e.g. 'Casual Leave', 'Sick Leave').",
        "--from": "Leave start date (ISO).",
        "--to": "Leave end date (ISO).",
        "--reason": "Free-text reason.",
        "--half-day": "Mark as a half-day leave.",
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={"name": "Created Leave Application name."},
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("hr attendance",),
)

HELP_DATA[("hr", "attendance")] = HelpRecord(
    summary="Mark daily attendance for an employee.",
    process="HR (attendance)",
    when_to_use="Use to record one attendance row.",
    param_notes={
        "--employee": "Employee name.",
        "--date": "Attendance date (ISO).",
        "--status": "Status — choices: Present | Absent | On Leave | Half Day | Work From Home. "
                     "Default 'Present'.",
        "--hours": "Working hours (float).",
        "--company": _COMPANY_NOTE,
        "--no-submit": _NO_SUBMIT_NOTE,
    },
    output={"name": "Created Attendance name."},
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("hr leave",),
)

HELP_DATA[("hr", "onboard")] = HelpRecord(
    summary="Onboard a new employee (create Employee with department / designation / company).",
    process="HR (full onboarding)",
    when_to_use="Use for a complete new-hire setup in one call.",
    param_notes={
        "--first-name": "First name (required).",
        "--last-name": "Last name.",
        "--joining": "Date of joining (ISO, REQUIRED).",
        "--company": _COMPANY_NOTE + " REQUIRED.",
        "--department": "Department master name.",
        "--designation": "Designation master name.",
        "--gender": "Default 'Prefer not to say'.",
        "--user-id": "Email to link to an existing User.",
    },
    output={"name": "Created Employee name."},
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("hr employee",),
)


# ── doc ──────────────────────────────────────────────────────────────

HELP_DATA[("doc", "get")] = HelpRecord(
    summary="Fetch a single DocType record by name.",
    process="Generic / read",
    when_to_use=(
        "Use to inspect any DocType record. Both arguments are "
        "POSITIONAL (no --doctype/--name flags)."
    ),
    param_notes={
        "DOCTYPE": "DocType name (positional, first arg). Examples: 'Company', 'Sales Order'.",
        "NAME": "Record name (positional, second arg).",
    },
    output={"(object)": "Full DocType document including child tables."},
    examples=(
        {
            "title": "Inspect a Company",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json doc get \"Company\" \"BIEL\"",
            "expect": "Full Company document.",
        },
    ),
    common_errors=(
        {"error": "NotFoundError",
         "cause": "DocType or record name doesn't exist.",
         "fix": "List with `doc list <DocType> --limit 5` to confirm spelling."},
        ERR_AUTH,
    ),
    related=("doc list", "doc update"),
)

HELP_DATA[("doc", "list")] = HelpRecord(
    summary="List records of a DocType with optional filters.",
    process="Generic / read",
    when_to_use=(
        "Use to enumerate records. DOCTYPE is POSITIONAL — do NOT pass "
        "--doctype. Filters use `field=value` for equality or "
        "`field:op:value` for other operators."
    ),
    param_notes={
        "DOCTYPE": "DocType name (positional, first arg). NEVER use --doctype here.",
        "--filter": "Repeatable. Format: `field=value` (equality) or "
                     "`field:op:value` (e.g. `docstatus:=:1`, "
                     "`status:in:[\"Open\",\"To Bill\"]`). NOT --data.",
        "--field": "Repeatable. Field to project; defaults to 'name' only when "
                    "omitted in some Frappe versions.",
        "--limit": "Page size. Default 20.",
        "--start": "Offset for pagination.",
        "--order-by": "ORDER BY clause, e.g. 'creation desc'.",
    },
    output={"(array)": "Array of records (objects)."},
    examples=(
        {
            "title": "Top 5 Companies",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json doc list Company --limit 5",
            "expect": "Array of up to 5 Company records.",
        },
        {
            "title": "Submitted Sales Orders not yet closed",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json doc list \"Sales Order\" --filter \"docstatus=1\" "
                       "--filter \"status:not in:[\\\"Closed\\\",\\\"Completed\\\"]\" "
                       "--field name --field customer --field grand_total",
            "expect": "Array with name + customer + grand_total per row.",
        },
    ),
    common_errors=(
        {"error": "UsageError",
         "cause": "Used --doctype or --data instead of positional + --filter.",
         "fix": "Make DOCTYPE the first positional arg. Use --filter / --field / --limit. "
                "--data is only for `doc insert` / `doc update`."},
        {"error": "NotFoundError",
         "cause": "DocType name typo.",
         "fix": "Canonical names use spaces and Title Case: 'Sales Order', "
                "'Purchase Receipt', 'Mode of Payment'. Quote them."},
        ERR_AUTH,
    ),
    related=("doc get", "doc insert"),
)

HELP_DATA[("doc", "insert")] = HelpRecord(
    summary="Insert (create) a new document from inline JSON or a JSON file.",
    process="Generic / create",
    when_to_use=(
        "Use ONLY for DocTypes that don't have a domain command. For "
        "Sales Order / Purchase Order / Stock Entry / Sales Invoice, "
        "prefer the matching domain chain — they fill in copy-forward "
        "fields that raw `doc insert` does not."
    ),
    param_notes={
        "--doctype": "DocType name. Optional if `doctype` is in the JSON body.",
        "--file": "Path to a JSON file with the doc body.",
        "--data": "Inline JSON for the doc body. Use single quotes around the whole "
                   "thing in shell, JSON inside.",
        "--submit": "Also submit the document after insert. Idempotent for "
                     "already-submitted docs.",
    },
    output={"(object)": "Inserted document with Frappe-assigned `name`."},
    examples=(
        {
            "title": "Create a Company",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json doc insert --doctype Company --data "
                       "'{\"company_name\":\"BIEL\",\"abbr\":\"BIEL\","
                       "\"default_currency\":\"CNY\",\"country\":\"Hong Kong\"}'",
            "expect": "Created Company document.",
        },
        {
            "title": "Create + submit an Item",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json doc insert --doctype Item --data "
                       "'{\"item_code\":\"WIDGET-001\",\"item_name\":\"Widget A\","
                       "\"item_group\":\"All Item Groups\",\"stock_uom\":\"Pcs\"}' "
                       "--submit",
            "expect": "Created Item, submitted.",
        },
    ),
    common_errors=(
        {"error": "ValidationError",
         "cause": "Required field missing for that DocType.",
         "fix": "Read the message; consult SKILL.md's 'Per-DocType required fields' "
                 "table for masters (Company, Warehouse, Item, etc.)."},
        {"error": "ValidationError",
         "cause": "Currency 'RMB' or country 'HK' (Frappe canonical names differ).",
         "fix": "Use 'CNY' / 'Hong Kong'. Probe with `doc list Currency` / `doc list Country`."},
        {"error": "LinkValidationError",
         "cause": "A linked field (parent group, default account, etc.) names a "
                   "non-existent record.",
         "fix": "Verify each linked value with a separate `doc list <X>` call."},
        ERR_AUTH,
    ),
    related=("doc update", "doc submit", "doc list"),
)

HELP_DATA[("doc", "update")] = HelpRecord(
    summary="Patch fields on an existing draft document.",
    process="Generic / update",
    when_to_use=(
        "Use to change fields on a DRAFT (`docstatus=0`) document. "
        "Submitted documents are immutable — cancel + amend via "
        "`doc cancel` instead."
    ),
    param_notes={
        "DOCTYPE": "DocType name (positional, first arg).",
        "NAME": "Record name (positional, second arg).",
        "--data": "Inline JSON of fields to update.",
    },
    output={"(object)": "Updated document."},
    examples=(
        {
            "title": "Set valuation_rate on an Item",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json doc update Item WIDGET-001 "
                       "--data '{\"valuation_rate\": 11.5}'",
            "expect": "Updated Item.",
        },
    ),
    common_errors=(ERR_SUBMITTED_IMMUTABLE, ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("doc cancel",),
)

HELP_DATA[("doc", "submit")] = HelpRecord(
    summary="Submit a draft (`docstatus=0` → 1).",
    process="Generic / submit",
    when_to_use="Use to make a draft document official and trigger ledger entries.",
    param_notes={
        "DOCTYPE": "DocType name (positional).",
        "NAME": "Record name (positional).",
    },
    output={"(object)": "Submitted document."},
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("doc insert", "doc cancel"),
)

HELP_DATA[("doc", "cancel")] = HelpRecord(
    summary="Cancel a submitted document (`docstatus=1` → 2).",
    process="Generic / cancel",
    when_to_use=(
        "Use to reverse a submitted document. Cancellation creates "
        "reversing GL entries automatically. To re-create after "
        "cancellation, use the *amend* flow (insert with `amended_from`)."
    ),
    param_notes={
        "DOCTYPE": "DocType name (positional).",
        "NAME": "Record name (positional).",
    },
    output={"(object)": "Cancelled document."},
    common_errors=(ERR_VALIDATION_GENERIC, ERR_AUTH),
    related=("doc submit", "doc delete"),
)

HELP_DATA[("doc", "delete")] = HelpRecord(
    summary="Delete a draft or cancelled document.",
    process="Generic / delete",
    when_to_use=(
        "Use only on draft (`docstatus=0`) or cancelled (`docstatus=2`) "
        "documents. Submitted docs cannot be deleted — cancel first."
    ),
    param_notes={
        "DOCTYPE": "DocType name (positional).",
        "NAME": "Record name (positional).",
    },
    output={"(object)": "Confirmation of delete."},
    common_errors=(
        {"error": "ValidationError",
         "cause": "Trying to delete a submitted document.",
         "fix": "Cancel it first via `doc cancel`."},
        ERR_AUTH,
    ),
    related=("doc cancel",),
)

HELP_DATA[("doc", "call")] = HelpRecord(
    summary="Call any whitelisted Frappe method by dotted path.",
    process="Generic / RPC",
    when_to_use=(
        "Use for arbitrary whitelisted calls that don't fit any other "
        "command. Common case: `frappe.auth.get_logged_user`, "
        "`frappe.client.get_count`, custom whitelisted methods."
    ),
    param_notes={
        "METHOD": "Dotted method path (positional). Example: "
                   "'frappe.auth.get_logged_user'.",
        "--arg": "Repeatable. Format: `key=value` (string) or `key:=<json>` "
                  "(JSON-typed). E.g. `--arg \"doctype=Item\"` or "
                  "`--arg \"filters:=[[\\\"name\\\",\\\"=\\\",\\\"X\\\"]]\"`.",
        "--data": "Inline JSON; merged with --arg kwargs. Use for complex payloads.",
    },
    output={"(any)": "Whatever the method returns (often a dict, list, or scalar)."},
    examples=(
        {
            "title": "Whoami",
            "command": "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
                       "--json doc call frappe.auth.get_logged_user",
            "expect": "Logged-in user email.",
        },
    ),
    common_errors=(
        {"error": "PermissionError_",
         "cause": "Method is not whitelisted, or the user lacks the role to call it.",
         "fix": "Check the method's @frappe.whitelist() decorator and the user's roles."},
        ERR_AUTH,
    ),
    related=("doc get", "doc list"),
)


# ─────────────────────────────────────────────────────────────────────
# Public lookup helpers
# ─────────────────────────────────────────────────────────────────────

def get_group_record(group: str) -> GroupRecord | None:
    """Return the curated metadata for a group, or ``None`` if absent."""
    return GROUP_DATA.get(group)


def get_command_record(group: str, command: str) -> HelpRecord | None:
    """Return the curated metadata for a command, or ``None`` if absent."""
    return HELP_DATA.get((group, command))

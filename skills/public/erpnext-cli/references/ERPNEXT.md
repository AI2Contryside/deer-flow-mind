# ERPNext — Codebase Analysis (for cli-anything-erpnext)

This is the per-software SOP / analysis doc called out in HARNESS.md.
ERPNext is not a conventional "desktop GUI" so the standard harness
patterns are adapted: the "real software" is a live Frappe bench
serving REST, and the "backend engine" is Frappe's ORM + the ERPNext
DocType graph.

## Backend engine

- **Frappe Framework** — Python/ORM + bench + permission system +
  DocType engine. Everything in ERPNext is a DocType.
- **ERPNext** — the business-logic layer (modules: selling, buying,
  stock, accounts, manufacturing, crm, hr, projects, assets, ...).
  Installed on top of Frappe via `bench get-app erpnext`.

## How GUI actions become API calls

Every GUI button that creates a downstream document calls a whitelisted
Python method named `erpnext.<module>.doctype.<dt>.<dt>.make_<target>`:

| GUI action | Endpoint |
|------------|----------|
| *Sales Order → Create → Delivery Note* | `erpnext.selling.doctype.sales_order.sales_order.make_delivery_note` |
| *Sales Order → Create → Sales Invoice* | `erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice` |
| *Delivery Note → Create → Sales Invoice* | `erpnext.stock.doctype.delivery_note.delivery_note.make_sales_invoice` |
| *Sales Invoice → Create → Payment* | `erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry` |
| *Purchase Order → Create → Purchase Receipt* | `erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt` |
| *Purchase Order → Create → Purchase Invoice* | `erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_invoice` |
| *Material Request → Create → Purchase Order* | `erpnext.stock.doctype.material_request.material_request.make_purchase_order` |
| *Work Order → Create → Stock Entry* | `erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry` |
| *Quotation → Create → Sales Order* | `erpnext.selling.doctype.quotation.quotation.make_sales_order` |
| *Lead → Create → Opportunity* | `erpnext.crm.doctype.lead.lead.make_opportunity` |
| *Lead → Create → Customer* | `erpnext.crm.doctype.lead.lead.make_customer` |

These methods:
- Receive `source_name` (the parent doc's name) as input.
- Return a populated *unsaved* dict of the target DocType.
- Encapsulate which fields copy forward, which taxes re-apply, which
  warehouses default, etc.

Our CLI calls `/api/method/<dotted.path>`, receives the dict, optionally
merges caller-supplied overrides, then POSTs to
`/api/resource/<DocType>` to persist + `frappe.client.submit` to submit.

## Data model

- **Project state = DocType records.** All persistence is server-side.
- **Wire format:** JSON over REST (`/api/resource`, `/api/method`).
- **Submission model:** every transactional DocType has `docstatus ∈ {0:draft, 1:submitted, 2:cancelled}`. Most workflow builders require a submitted source.
- **Standard names:** auto-generated like `ACC-SINV-2026-00001`. Some
  DocTypes (Item, Customer, Supplier) autoname from a text field.

## Existing CLI tools

- `bench` — site/app management (not the business layer).
- `frappe.client` — the whitelisted REST surface for CRUD.
- No first-class business-flow CLI exists — that's the gap this
  harness fills.

## Authentication

Two Frappe-supported modes:

1. **API token** (preferred, stateless):
   `Authorization: token <api_key>:<api_secret>`
2. **Session login**: `POST /api/method/login` with `usr`+`pwd` → cookies.

Both supported by `core/client.py::FrappeClient`.

## Architecture patterns applied

**Use the real software — don't reimplement it.** Every business flow
chains ERPNext's own `make_*` methods. We never build a parallel
implementation of "how to create a Delivery Note from a Sales Order."

**Generate valid intermediates.** When a `make_*` method returns a dict,
we hand it directly to Frappe's `/api/resource/<DocType>` insert (with
optional merged overrides). The dict is the native format; Frappe does
all validation/submission.

**Fail loudly and clearly.** HTTP errors are classified to typed
exceptions (`AuthError`, `NotFoundError`, `ValidationError`,
`PermissionError_`, `WorkflowError`, `ServerError`) so agents can
programmatically decide whether to retry, re-auth, or surface.

**Session-aware context.** Every ERPNext transaction requires a
`company`; most stock ops require a `warehouse`. Rather than force every
command to re-pass them, the session tracks defaults.

## What's *not* wrapped (and why)

- **Site setup / migrations.** `bench new-site`, `bench migrate`,
  `bench backup` — these are admin tasks, not business flows.
- **Custom DocTypes the user authored.** For those, use the `doc`
  escape hatch (`cli-anything-erpnext doc insert --doctype ...`).
- **Complex approvals / workflows.** Multi-step approval via Frappe
  Workflow isn't yet modeled as a first-class command — agents can
  drive it via `doc update` setting `workflow_state`.
- **Regional tax regimes.** Tax templates are referenced by name; the
  CLI doesn't generate them. Configure them once in ERPNext and point
  your commands at their names.

## Coverage matrix

| Domain | Primitives | Chainers | End-to-end | Read helpers |
|--------|-----------|----------|------------|--------------|
| selling | quote, order | quote→SO, SO→DN, SO→SI, DN→SI, SI→PE | order-to-cash, quote-to-cash, onboard-customer | list-open, dashboard |
| buying | MR, PO, SQ | MR→RFQ, MR→PO, SQ→PO, PO→PR, PO→PI, PR→PI, PI→PE | procure-to-pay, request-to-pay, onboard-supplier | list-open, dashboard |
| stock | — | — | transfer, issue, receipt, reconcile | levels, warehouse |
| accounts | JE | SI→PE (receive), PI→PE (pay) | reconcile | AR, AP, snapshot |
| manufacturing | BOM, WO | WO→SE (transfer/manufacture) | make-from-bom | — |
| crm | lead | lead→opportunity, opportunity→quote, lead→customer | lead-to-quotation | — |
| hr | employee, leave, attendance | — | onboard | — |

## Open questions for future iterations

- Subcontracting flow (Subcontracting Receipt, Subcontracting Inward Order).
- Projects + timesheets + billing.
- Asset depreciation + disposal.
- Regional tax auto-application (e.g., India GST, UAE VAT).
- Payroll run end-to-end (Payroll Entry → Salary Slip → JE).

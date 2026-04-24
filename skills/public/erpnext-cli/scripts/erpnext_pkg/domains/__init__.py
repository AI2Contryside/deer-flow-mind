"""Business-process domain wrappers.

Each module in this package exposes **workflow-level** functions that
chain multiple DocTypes together the way a human user would operate the
GUI — not raw DocType CRUD.

Example (``selling``):

    order_to_cash(client, customer, items, ...)
    ->  Sales Order (submitted)
    ->  Delivery Note (submitted)
    ->  Sales Invoice (submitted)
    ->  Payment Entry (submitted)

The backbone of every workflow is an ERPNext whitelisted
``make_<target>(source_name)`` method — e.g.
``erpnext.selling.doctype.sales_order.sales_order.make_delivery_note``.
These functions were written to map 1:1 with the GUI's "Create →
Delivery Note" style actions, so our wrappers inherit their correctness.
"""

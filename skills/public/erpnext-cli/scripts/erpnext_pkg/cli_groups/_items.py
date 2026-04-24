"""Parse the ``--item`` / ``--items-json`` / ``--items-file`` option set.

Three equivalent ways to pass items to a command, from terse to full::

    --item ITEM-001:10
    --item ITEM-001:10:99.50          # code:qty:rate
    --items-json '[{"item_code":"X","qty":10,"rate":99.5}]'
    --items-file path/to/items.json
"""

from __future__ import annotations

import json
from pathlib import Path


def parse_item_arg(spec: str) -> dict:
    """Parse one ``--item`` CLI argument."""
    parts = spec.split(":")
    if len(parts) == 2:
        code, qty = parts
        return {"item_code": code.strip(), "qty": float(qty)}
    if len(parts) == 3:
        code, qty, rate = parts
        return {"item_code": code.strip(), "qty": float(qty), "rate": float(rate)}
    raise ValueError(
        f"--item expects CODE:QTY or CODE:QTY:RATE, got {spec!r}"
    )


def resolve_items(
    item_specs: tuple[str, ...] | list[str],
    items_json: str | None,
    items_file: str | None,
) -> list[dict]:
    """Combine the three option forms. At least one must produce rows."""
    rows: list[dict] = []
    if items_file:
        path = Path(items_file).expanduser()
        rows.extend(json.loads(path.read_text(encoding="utf-8")))
    if items_json:
        rows.extend(json.loads(items_json))
    for spec in item_specs or ():
        rows.append(parse_item_arg(spec))
    if not rows:
        raise ValueError(
            "No items provided. Use --item CODE:QTY (repeatable) "
            "or --items-json or --items-file."
        )
    return rows

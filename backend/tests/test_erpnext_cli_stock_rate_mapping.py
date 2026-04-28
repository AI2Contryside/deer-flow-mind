"""Tests for the ``rate`` → ``basic_rate`` promotion in stock entries.

Pinned against thread ``0bc19732-0315-457f-be3d-830e1b7289a5`` where the
agent burned ~5 supersteps reading the CLI source code to discover that
Stock Entry Detail wants ``basic_rate`` (writable) and that the row's
``rate`` is a computed column ERPNext recomputes on save.

The CLI now accepts the more intuitive ``rate`` field — matching what
``selling`` / ``buying`` already accept — and silently promotes it to
``basic_rate`` + ``set_basic_rate_manually=1`` so ERPNext doesn't blank
it from the Item master's valuation_rate.

Contract:

  - ``rate`` on an items row is moved to ``basic_rate`` and
    ``set_basic_rate_manually=1`` is auto-set.
  - Callers who pass ``basic_rate`` directly are not double-mapped, and
    their explicit ``set_basic_rate_manually`` value is preserved.
  - Rows without any rate field are unchanged (the call may rely on the
    Item master's ``valuation_rate``).
  - Tuple-form ``[(code, qty, rate)]`` goes through ``normalize_items``
    first, then gets the same promotion.
  - Zero-cost rows + ``allow_zero_valuation_rate=1`` are preserved
    (legitimate write-off / sample case).
  - Warehouse defaults are still applied per ``s_warehouse`` /
    ``t_warehouse``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Skill bundle import path (mirrors test_erpnext_cli_bootstrap_status.py).
_SKILL_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "public" / "erpnext-cli" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

from erpnext_pkg.domains.stock import _items_for_transfer  # noqa: E402


def test_rate_is_promoted_to_basic_rate():
    items = [{"item_code": "991-7-2400", "qty": 1400, "rate": 11.5}]

    out = _items_for_transfer(items, None, "成品仓 - TDG")

    assert out[0]["basic_rate"] == 11.5
    assert "rate" not in out[0]
    assert out[0]["set_basic_rate_manually"] == 1
    assert out[0]["t_warehouse"] == "成品仓 - TDG"


def test_explicit_basic_rate_is_preserved_with_caller_flag():
    """Callers who already know the ERPNext field name shouldn't be
    second-guessed — including their explicit set_basic_rate_manually."""
    items = [
        {
            "item_code": "X",
            "qty": 1,
            "basic_rate": 99.5,
            "set_basic_rate_manually": 0,
        }
    ]

    out = _items_for_transfer(items, None, "WH")

    assert out[0]["basic_rate"] == 99.5
    assert out[0]["set_basic_rate_manually"] == 0


def test_no_rate_field_means_no_auto_injection():
    """Without a rate, ERPNext should fall back to Item.valuation_rate.
    The CLI must not invent a basic_rate or flip the manual flag."""
    items = [{"item_code": "Y", "qty": 5}]

    out = _items_for_transfer(items, None, "WH")

    assert "basic_rate" not in out[0]
    assert "set_basic_rate_manually" not in out[0]


def test_tuple_form_is_normalized_then_promoted():
    """`(code, qty, rate)` tuples go through `normalize_items` first,
    where the third element becomes ``rate`` — and from there the same
    promotion applies."""
    items = [("Z", 10, 5.5)]

    out = _items_for_transfer(items, None, "WH")

    assert out[0]["item_code"] == "Z"
    assert out[0]["qty"] == 10
    assert out[0]["basic_rate"] == 5.5
    assert out[0]["set_basic_rate_manually"] == 1


def test_zero_rate_with_allow_zero_valuation_passes_through():
    """Free samples / write-offs need rate=0 but require an explicit
    `allow_zero_valuation_rate=1` to bypass ERPNext's accounting check.
    Both must survive the promotion."""
    items = [
        {
            "item_code": "FREEBIE",
            "qty": 1,
            "rate": 0,
            "allow_zero_valuation_rate": 1,
        }
    ]

    out = _items_for_transfer(items, None, "WH")

    assert out[0]["basic_rate"] == 0
    assert out[0]["allow_zero_valuation_rate"] == 1
    assert out[0]["set_basic_rate_manually"] == 1


def test_warehouse_defaults_applied_per_row():
    """Per-row s_warehouse/t_warehouse override the function defaults;
    rows without them get the function-level value as a fallback."""
    items = [
        {"item_code": "A", "qty": 1, "rate": 10},
        {
            "item_code": "B",
            "qty": 2,
            "rate": 20,
            "t_warehouse": "OVERRIDE-WH",
        },
    ]

    out = _items_for_transfer(items, "src-WH", "default-WH")

    assert out[0]["s_warehouse"] == "src-WH"
    assert out[0]["t_warehouse"] == "default-WH"
    assert out[1]["t_warehouse"] == "OVERRIDE-WH"  # row override wins
    assert out[1]["s_warehouse"] == "src-WH"


def test_multiple_rows_promoted_independently():
    items = [
        {"item_code": "A", "qty": 1, "rate": 10.0},
        {"item_code": "B", "qty": 2, "basic_rate": 20.0},  # already set
        {"item_code": "C", "qty": 3},  # no rate
    ]

    out = _items_for_transfer(items, None, "WH")

    assert out[0]["basic_rate"] == 10.0
    assert out[0]["set_basic_rate_manually"] == 1
    assert out[1]["basic_rate"] == 20.0
    # The middle row had basic_rate but no caller flag — defaults to absent.
    assert "set_basic_rate_manually" not in out[1]
    assert "basic_rate" not in out[2]

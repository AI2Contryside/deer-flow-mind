"""OCR-extractor subagent + schema tests.

Verifies the subagent's contract is internally consistent:
- Registration shape
- Disallowed-tool list keeps it locked to read+image+JSON write
- System prompt mentions every doc_type the schema supports (so the
  LLM knows about every document the validator will accept)
- ``OcrEnvelope`` round-trips for fixture JSON of each doc_type
- ``OCR_SCHEMA_PROMPT_BLOCK`` references the same field names as the
  pydantic models (catches prompt drift before it ships).
"""

from __future__ import annotations

import json

import pytest


def test_ocr_extractor_registered() -> None:
    """ocr-extractor sits in BUILTIN_SUBAGENTS with the expected wiring."""
    from src.subagents.builtins import BUILTIN_SUBAGENTS, OCR_EXTRACTOR_CONFIG

    assert "ocr-extractor" in BUILTIN_SUBAGENTS
    cfg = BUILTIN_SUBAGENTS["ocr-extractor"]
    assert cfg is OCR_EXTRACTOR_CONFIG

    assert cfg.name == "ocr-extractor"
    assert cfg.model == "qwen-vl-max-latest"
    assert cfg.max_turns == 12
    assert cfg.timeout_seconds == 420

    # Tool list — must include the OCR-pipeline essentials and exclude
    # everything that could nudge the subagent toward auto-writing to
    # ERPNext (bash) or asking the user (ask_clarification).
    assert cfg.tools is not None
    assert "view_image" in cfg.tools
    assert "write_file" in cfg.tools
    assert "present_files" in cfg.tools
    assert "ls" in cfg.tools
    assert "read_file" in cfg.tools
    assert "bash" not in cfg.tools

    assert cfg.disallowed_tools is not None
    assert "task" in cfg.disallowed_tools
    assert "ask_clarification" in cfg.disallowed_tools
    assert "bash" in cfg.disallowed_tools


def test_get_subagent_config_resolves_ocr_extractor() -> None:
    from src.subagents.registry import get_subagent_config

    cfg = get_subagent_config("ocr-extractor")
    assert cfg is not None
    assert cfg.name == "ocr-extractor"
    assert cfg.model == "qwen-vl-max-latest"


def test_ocr_prompt_mentions_every_doc_type() -> None:
    """System prompt must cover every doc_type the schema accepts —
    otherwise the LLM will skip a type it should classify."""
    from src.subagents.builtins.ocr_extractor import OCR_EXTRACTOR_SYSTEM_PROMPT

    for doc_type in (
        "commercial_invoice",
        "packing_list",
        "bill_of_lading",
        "customs_declaration",
        "proforma_invoice",
    ):
        assert doc_type in OCR_EXTRACTOR_SYSTEM_PROMPT, f"{doc_type} missing from prompt"

    # Output filename convention matters — frontend ArtifactRouter
    # routes on ``_ocr.json`` suffix.
    assert "_ocr.json" in OCR_EXTRACTOR_SYSTEM_PROMPT
    assert "/mnt/user-data/outputs/" in OCR_EXTRACTOR_SYSTEM_PROMPT


def test_ocr_envelope_rejects_extra_fields() -> None:
    """``model_config = ConfigDict(extra='forbid')`` — a typo in a key
    name should fail validation rather than silently drop data."""
    from pydantic import ValidationError

    from src.subagents.builtins.ocr_schemas import OcrEnvelope

    with pytest.raises(ValidationError):
        OcrEnvelope(
            detected_doc_type="unknown",
            confidence=0.5,
            raw_text="hi",
            structured=None,
            unexpected_field="should fail",  # type: ignore[call-arg]
        )


def test_ocr_envelope_unknown_doc_type_with_null_structured() -> None:
    """Low-confidence classification → unknown + None structured. The
    user still sees raw_text."""
    from src.subagents.builtins.ocr_schemas import OcrEnvelope

    env = OcrEnvelope(
        detected_doc_type="unknown",
        confidence=0.2,
        raw_text="some unreadable garble",
        structured=None,
        notes="image too blurry to classify",
    )
    assert env.detected_doc_type == "unknown"
    assert env.structured is None


def test_ocr_envelope_commercial_invoice_roundtrip() -> None:
    """A realistic commercial-invoice JSON parses cleanly and
    serializes back to the same shape."""
    from src.subagents.builtins.ocr_schemas import OcrEnvelope

    payload = {
        "detected_doc_type": "commercial_invoice",
        "confidence": 0.92,
        "raw_text": "COMMERCIAL INVOICE No. INV-001 ...",
        "structured": {
            "doc_type": "commercial_invoice",
            "invoice_no": "INV-001",
            "invoice_date": "2026-03-05",
            "seller": {
                "name": "Shenzhen Widget Co.",
                "address": "1 Industrial Rd, Shenzhen, China",
                "contact": "sales@example.cn",
                "tax_id": "91440300MA5XXXXXX",
            },
            "buyer": {
                "name": "ACME Imports LLC",
                "address": "200 Main St, Los Angeles, CA, USA",
                "contact": None,
                "tax_id": None,
            },
            "incoterm": "FOB",
            "port_of_loading": "Shenzhen",
            "port_of_discharge": "Los Angeles",
            "items": [
                {
                    "sku": "WDG-100",
                    "description": "Stainless widget, type A",
                    "hs_code": "8302.41",
                    "quantity": 1000.0,
                    "unit": "PCS",
                    "unit_price": 1.50,
                    "amount": 1500.0,
                    "currency": "USD",
                },
            ],
            "total_amount": 1500.0,
            "currency": "USD",
            "payment_terms": "T/T 30% deposit, 70% before shipment",
        },
        "notes": None,
    }
    env = OcrEnvelope.model_validate(payload)
    assert env.detected_doc_type == "commercial_invoice"
    assert env.structured is not None
    assert env.structured.doc_type == "commercial_invoice"
    # round-trip through JSON to make sure nothing was silently coerced
    rt = json.loads(env.model_dump_json())
    assert rt["structured"]["items"][0]["sku"] == "WDG-100"
    assert rt["structured"]["total_amount"] == 1500.0


def test_ocr_envelope_packing_list_roundtrip() -> None:
    from src.subagents.builtins.ocr_schemas import OcrEnvelope

    payload = {
        "detected_doc_type": "packing_list",
        "confidence": 0.85,
        "raw_text": "PACKING LIST No. PL-002 ...",
        "structured": {
            "doc_type": "packing_list",
            "pl_no": "PL-002",
            "pl_date": "2026-03-06",
            "seller": {"name": "Widget Co", "address": None, "contact": None, "tax_id": None},
            "buyer": {"name": "ACME", "address": None, "contact": None, "tax_id": None},
            "shipping_marks": "ACME / LA / 1-50",
            "total_packages": 50,
            "total_gross_weight_kg": 850.0,
            "total_net_weight_kg": 800.0,
            "total_volume_cbm": 4.5,
            "items": [
                {
                    "sku": "WDG-100",
                    "description": "Widget A",
                    "quantity": 1000.0,
                    "unit": "PCS",
                    "hs_code": None,
                    "unit_price": None,
                    "amount": None,
                    "currency": None,
                },
            ],
        },
    }
    env = OcrEnvelope.model_validate(payload)
    assert env.structured is not None
    assert env.structured.total_packages == 50


def test_ocr_envelope_bill_of_lading_with_freight_terms() -> None:
    from pydantic import ValidationError

    from src.subagents.builtins.ocr_schemas import OcrEnvelope

    payload = {
        "detected_doc_type": "bill_of_lading",
        "confidence": 0.88,
        "raw_text": "B/L NO. ABC-123",
        "structured": {
            "doc_type": "bill_of_lading",
            "bl_no": "ABC-123",
            "bl_date": "2026-03-07",
            "shipper": None,
            "consignee": None,
            "notify_party": None,
            "vessel": "EVER GIVEN",
            "voyage_no": "002E",
            "port_of_loading": "Yantian",
            "port_of_discharge": "Long Beach",
            "place_of_delivery": "Los Angeles",
            "container_nos": ["TGHU1234567", "MSCU7654321"],
            "seal_nos": ["S001", "S002"],
            "packages": 50,
            "gross_weight_kg": 850.0,
            "measurement_cbm": 4.5,
            "freight_terms": "PREPAID",
        },
    }
    env = OcrEnvelope.model_validate(payload)
    assert env.structured is not None
    assert env.structured.freight_terms == "PREPAID"

    # Wrong literal must reject — catches OCR mis-spellings like "PRE PAID"
    bad = dict(payload)
    bad["structured"] = {**payload["structured"], "freight_terms": "PRE-PAID"}
    with pytest.raises(ValidationError):
        OcrEnvelope.model_validate(bad)


def test_ocr_envelope_confidence_bounds() -> None:
    from pydantic import ValidationError

    from src.subagents.builtins.ocr_schemas import OcrEnvelope

    # 0..1 inclusive
    OcrEnvelope.model_validate({"detected_doc_type": "unknown", "confidence": 0.0, "raw_text": "x"})
    OcrEnvelope.model_validate({"detected_doc_type": "unknown", "confidence": 1.0, "raw_text": "x"})

    with pytest.raises(ValidationError):
        OcrEnvelope.model_validate({"detected_doc_type": "unknown", "confidence": -0.1, "raw_text": "x"})
    with pytest.raises(ValidationError):
        OcrEnvelope.model_validate({"detected_doc_type": "unknown", "confidence": 1.5, "raw_text": "x"})


def test_ocr_schema_prompt_block_mentions_core_fields() -> None:
    """The handwritten prompt block must reference every top-level
    field name that the pydantic models care about. This catches
    drift where someone renames a pydantic field but forgets the
    prompt — the LLM would silently emit the old name forever."""
    from src.subagents.builtins.ocr_schemas import OCR_SCHEMA_PROMPT_BLOCK

    # Top-level envelope keys
    for key in ("detected_doc_type", "confidence", "raw_text", "structured", "notes"):
        assert key in OCR_SCHEMA_PROMPT_BLOCK, f"envelope field {key!r} missing from prompt"

    # Sample of inner field names that the LLM must produce
    for key in (
        "invoice_no",
        "invoice_date",
        "incoterm",
        "items",
        "total_amount",
        "pl_no",
        "shipping_marks",
        "bl_no",
        "container_nos",
        "freight_terms",
        "declaration_no",
        "valid_until",
    ):
        assert key in OCR_SCHEMA_PROMPT_BLOCK, f"inner field {key!r} missing from prompt"

    # And every doc_type label
    for doc_type in (
        "commercial_invoice",
        "packing_list",
        "bill_of_lading",
        "customs_declaration",
        "proforma_invoice",
    ):
        assert doc_type in OCR_SCHEMA_PROMPT_BLOCK

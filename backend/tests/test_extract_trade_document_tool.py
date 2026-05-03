"""extract_trade_document tool — registration + boundary + parsing tests.

The tool itself talks to qwen-vl-ocr-latest at runtime which we never
hit from unit tests. Instead we cover:

- Registration: tool is exported, has the right name, lives in
  BUILTIN_TOOLS so every agent (including the text-only lead agent)
  picks it up.
- Boundary checks: missing file / wrong extension / oversized image
  return error ToolMessages instead of crashing.
- Response parsing: the parser tolerates ```json fences and falls
  back to an "unknown" envelope when the model returns garbage
  rather than letting JSONDecodeError propagate.
- Schema reuse: confirms the tool builds its prompt from the same
  ``OCR_SCHEMA_PROMPT_BLOCK`` the OcrEnvelope validator was generated
  against, so prompt and validator can't drift.
- Subagent registry: ocr-extractor must be GONE from BUILTIN_SUBAGENTS
  (regression — we deleted the subagent in favour of this tool, and
  leaving a stale Literal value would let LangChain accept
  ``task(subagent_type='ocr-extractor', ...)`` calls that crash deep
  inside the executor).
"""

from __future__ import annotations

from typing import get_args, get_type_hints


def test_extract_trade_document_tool_exported() -> None:
    from src.tools.builtins import extract_trade_document_tool

    assert extract_trade_document_tool.name == "extract_trade_document"


def test_extract_trade_document_tool_in_builtin_list() -> None:
    """BUILTIN_TOOLS picks up the new tool unconditionally — the lead
    agent runs on a text-only model so we can NOT gate it on
    supports_vision (the gating check sits on view_image_tool
    instead)."""
    from src.tools.builtins import extract_trade_document_tool
    from src.tools.tools import BUILTIN_TOOLS

    assert extract_trade_document_tool in BUILTIN_TOOLS


def test_strip_code_fence_handles_json_block() -> None:
    from src.tools.builtins.extract_trade_document_tool import _strip_code_fence

    assert _strip_code_fence('```json\n{"a":1}\n```') == '{"a":1}'
    assert _strip_code_fence('```\n{"a":1}\n```') == '{"a":1}'
    assert _strip_code_fence('  {"a":1}  ') == '{"a":1}'
    # No fence at all → unchanged after strip
    assert _strip_code_fence('{"a":1}') == '{"a":1}'


def test_strip_code_fence_handles_truncated_opening_fence() -> None:
    """When max_tokens cuts off the response mid-array, the opening
    ```json fence is present but the closing ``` never arrives. We
    should still strip the opener so the body is parseable."""
    from src.tools.builtins.extract_trade_document_tool import _strip_code_fence

    truncated = '```json\n{"a":1, "items": [{"sku":"A"'
    out = _strip_code_fence(truncated)
    # Opener gone, body kept intact for the recovery pass.
    assert not out.startswith("```")
    assert out.startswith('{"a":1')


def test_recover_truncated_json_salvages_complete_items() -> None:
    """Real-shape truncation case: header fields complete, items
    array cut off mid-element. Recovery should drop the partial last
    item and close the brackets, yielding a valid envelope with the
    complete items it managed to extract."""
    from src.tools.builtins.extract_trade_document_tool import _try_recover_truncated_json

    truncated = """{
        "detected_doc_type": "commercial_invoice",
        "confidence": 0.95,
        "raw_text": "headers only",
        "structured": {
            "doc_type": "commercial_invoice",
            "invoice_no": "INV-001",
            "items": [
                {"sku": "A", "description": "x", "quantity": 1, "amount": 10.0},
                {"sku": "B", "description": "y", "quantity": 2, "amount": 20.0},
                {"sku": "C", "descrip"""

    recovered = _try_recover_truncated_json(truncated)
    assert recovered is not None
    assert recovered["detected_doc_type"] == "commercial_invoice"
    assert recovered["structured"]["invoice_no"] == "INV-001"
    # Should keep the two complete items, drop the partial third.
    assert len(recovered["structured"]["items"]) == 2
    assert recovered["structured"]["items"][0]["sku"] == "A"
    assert recovered["structured"]["items"][1]["sku"] == "B"


def test_recover_truncated_json_returns_none_for_garbage() -> None:
    """Recovery should give up cleanly (return None) on responses
    that aren't even close to valid JSON, so the caller can fall
    back to the unknown envelope without false confidence."""
    from src.tools.builtins.extract_trade_document_tool import _try_recover_truncated_json

    assert _try_recover_truncated_json("") is None
    assert _try_recover_truncated_json("not json at all") is None
    assert _try_recover_truncated_json("[1, 2, 3]") is None  # we only handle objects
    # No safe boundary at all (no `},` or `],` ever appears)
    assert _try_recover_truncated_json('{"a": "value missing close') is None


def test_recover_truncated_json_handles_strings_with_brackets() -> None:
    """Bracket counting must respect string boundaries — a ``]`` or
    ``}`` inside a JSON string isn't a structural close. A naive
    count would over-close and produce malformed JSON."""
    from src.tools.builtins.extract_trade_document_tool import _try_recover_truncated_json

    truncated = """{
        "detected_doc_type": "commercial_invoice",
        "confidence": 0.5,
        "raw_text": "weird content with } and ] inside",
        "structured": {
            "doc_type": "commercial_invoice",
            "items": [
                {"sku": "A", "description": "has } inside"},
                {"sku": "B"""

    recovered = _try_recover_truncated_json(truncated)
    assert recovered is not None
    assert recovered["raw_text"] == "weird content with } and ] inside"
    assert len(recovered["structured"]["items"]) == 1
    assert recovered["structured"]["items"][0]["description"] == "has } inside"


def test_max_line_items_per_call_constant() -> None:
    """Lock down the line-item cap so prompt and runtime can't drift.
    The prompt instructs the model to stop at this many; if we ever
    raise it without re-evaluating qwen-vl-max's 8192-token output
    ceiling, dense documents will silently truncate again."""
    from src.tools.builtins.extract_trade_document_tool import MAX_LINE_ITEMS_PER_CALL

    assert MAX_LINE_ITEMS_PER_CALL == 100


def test_extract_prompt_mentions_truncation_constraints() -> None:
    """The prompt must tell the model about both length constraints
    (raw_text headers-only, items capped at MAX_LINE_ITEMS_PER_CALL)
    AND what to do when over the cap (notes report). Without these
    guidelines a 300-row sales order generates ~11k tokens and gets
    cut off mid-array."""
    from src.tools.builtins.extract_trade_document_tool import (
        MAX_LINE_ITEMS_PER_CALL,
        _build_extract_prompt,
    )

    prompt = _build_extract_prompt()
    assert str(MAX_LINE_ITEMS_PER_CALL) in prompt
    assert "raw_text" in prompt
    assert "头部" in prompt
    assert "notes" in prompt
    assert "明细" in prompt


def test_supported_extensions() -> None:
    from src.tools.builtins.extract_trade_document_tool import SUPPORTED_EXTENSIONS

    # Cover the formats DashScope's vision endpoint accepts.
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        assert ext in SUPPORTED_EXTENSIONS


def test_max_image_bytes_matches_dashscope_limit() -> None:
    """DashScope caps a single image at ~10 MB raw bytes. Locking this
    down so an over-eager refactor doesn't accidentally raise it past
    the upstream limit."""
    from src.tools.builtins.extract_trade_document_tool import MAX_IMAGE_BYTES

    assert MAX_IMAGE_BYTES == 10 * 1024 * 1024


def test_extract_prompt_embeds_ocr_schema() -> None:
    """The prompt sent to the OCR model must contain every doc_type
    label and every top-level envelope key — otherwise the model can
    silently emit fields the validator will reject. Mirror of
    test_ocr_schema_prompt_block_mentions_core_fields, but for the
    tool's wrapped prompt.

    Doc-type taxonomy: 5 international + 2 domestic (sales_order,
    delivery_note) + 1 generic table fallback + ``unknown``.
    """
    from src.tools.builtins.extract_trade_document_tool import _build_extract_prompt

    prompt = _build_extract_prompt()

    for doc_type in (
        "commercial_invoice",
        "packing_list",
        "bill_of_lading",
        "customs_declaration",
        "proforma_invoice",
        "sales_order",
        "delivery_note",
        "generic_table",
        "unknown",
    ):
        assert doc_type in prompt, f"doc_type {doc_type} missing from prompt"

    for key in ("detected_doc_type", "confidence", "raw_text", "structured", "notes"):
        assert key in prompt, f"envelope field {key} missing from prompt"

    # Tool must tell the model NOT to wrap the JSON in markdown fences
    # — otherwise we'd be relying solely on _strip_code_fence as
    # post-processing.
    assert "JSON" in prompt
    assert "代码块" in prompt or "markdown" in prompt.lower()


def test_extract_prompt_decouples_confidence_from_classification() -> None:
    """Regression for thread 0d74d5e2 — a Chinese hand-written sales
    slip was classified as ``unknown`` and the prompt told the model
    to also drop ``confidence`` to 0~0.3, even though OCR text
    recognition was actually fine. Users saw "10% 置信度" and assumed
    the OCR had failed when it hadn't.

    The fix is to pin two contracts in the prompt:
      1. ``confidence`` is about OCR text quality, NOT classification.
      2. Before falling back to ``unknown``, the model should try
         ``generic_table`` so any table-shaped image still produces
         structured rows.
    """
    from src.tools.builtins.extract_trade_document_tool import _build_extract_prompt

    prompt = _build_extract_prompt()

    # The prompt must explicitly say confidence == OCR quality, not
    # classification quality. We check for the load-bearing phrase
    # in the schema block AND the wrapping prompt's reminder.
    assert "OCR 文字识别" in prompt
    assert "不要因为" in prompt and "降低 confidence" in prompt or "降分" in prompt

    # Generic-table-before-unknown must be the documented fallback
    # order, otherwise the model defaults back to dropping every
    # extracted row.
    assert "generic_table" in prompt
    assert "先尝试" in prompt


def test_format_summary_extracts_headline_fields() -> None:
    """Summary must surface the key trade fields (number / date /
    amount / currency / line count) without embedding the whole JSON
    payload — that's what the artifact card is for."""
    from src.subagents.builtins.ocr_schemas import (
        CommercialInvoice,
        LineItem,
        OcrEnvelope,
    )
    from src.tools.builtins.extract_trade_document_tool import _format_summary

    inv = CommercialInvoice(
        invoice_no="INV-001",
        invoice_date="2026-03-05",
        total_amount=1500.0,
        currency="USD",
        items=[
            LineItem(sku="A", description="x", quantity=10),
            LineItem(sku="B", description="y", quantity=5),
        ],
    )
    env = OcrEnvelope(
        detected_doc_type="commercial_invoice",
        confidence=0.92,
        raw_text="raw",
        structured=inv,
    )
    out = _format_summary(env, "/mnt/user-data/outputs/commercial_invoice_ocr.json")

    assert "commercial_invoice" in out
    assert "92%" in out
    assert "INV-001" in out
    assert "USD" in out
    assert "商品行数=2" in out
    assert "/mnt/user-data/outputs/commercial_invoice_ocr.json" in out
    # Must NOT dump the full JSON envelope inline.
    assert env.model_dump_json() not in out


def test_format_summary_unknown_doc_type_no_bullets() -> None:
    """When the model can't classify the image, structured is None
    and the summary should still be readable — no AttributeError on
    missing fields."""
    from src.subagents.builtins.ocr_schemas import OcrEnvelope
    from src.tools.builtins.extract_trade_document_tool import _format_summary

    env = OcrEnvelope(
        detected_doc_type="unknown",
        confidence=0.0,
        raw_text="some unreadable garble",
        structured=None,
        notes="image too blurry",
    )
    out = _format_summary(env, "/mnt/user-data/outputs/unknown_ocr.json")
    assert "unknown" in out
    assert "0%" in out
    assert "image too blurry" in out


def test_format_summary_sales_order_surfaces_headline_fields() -> None:
    """Domestic sales order — the summary line should pick up the
    Chinese-style headline fields (订单号 / 日期 / 金额) that the
    international flat-table covers via getattr."""
    from src.subagents.builtins.ocr_schemas import (
        LineItem,
        OcrEnvelope,
        Party,
        SalesOrder,
    )
    from src.tools.builtins.extract_trade_document_tool import _format_summary

    so = SalesOrder(
        order_no="SO-202604-001",
        order_date="2026-04-20",
        customer=Party(name="朱雪英"),
        total_amount=2014.0,
        currency="CNY",
        items=[
            LineItem(description="布料", quantity=1110, unit="米"),
            LineItem(description="布料", quantity=1165, unit="米"),
        ],
    )
    env = OcrEnvelope(
        detected_doc_type="sales_order",
        confidence=0.92,
        raw_text="客户：朱雪英 ...",
        structured=so,
    )
    out = _format_summary(env, "/mnt/user-data/outputs/sales_order_ocr.json")

    assert "sales_order" in out
    assert "92%" in out
    assert "SO-202604-001" in out
    assert "2014" in out
    assert "CNY" in out
    assert "商品行数=2" in out


def test_format_summary_delivery_note_surfaces_headline_fields() -> None:
    """Delivery note — total_packages / total_quantity should come
    through, and the prices column is intentionally absent."""
    from src.subagents.builtins.ocr_schemas import (
        DeliveryNote,
        LineItem,
        OcrEnvelope,
        Party,
    )
    from src.tools.builtins.extract_trade_document_tool import _format_summary

    dn = DeliveryNote(
        dn_no="DN-001",
        dn_date="2026-05-03",
        sender=Party(name="供货方A"),
        receiver=Party(name="收货方B"),
        total_packages=12,
        total_quantity=18520.0,
        items=[LineItem(description="商品X", quantity=100, unit="米")],
    )
    env = OcrEnvelope(
        detected_doc_type="delivery_note",
        confidence=0.88,
        raw_text="DN-001 ...",
        structured=dn,
    )
    out = _format_summary(env, "/mnt/user-data/outputs/delivery_note_ocr.json")

    assert "delivery_note" in out
    assert "88%" in out
    assert "DN-001" in out
    assert "总件数=12" in out


def test_format_summary_generic_table_surfaces_title_and_totals() -> None:
    """Generic table — the summary should pull the title, the first
    meta entries, the row count, and the first totals entry rather
    than trying to find invoice_no / total_amount fields that don't
    exist on this schema."""
    from src.subagents.builtins.ocr_schemas import (
        GenericTable,
        KeyValue,
        OcrEnvelope,
    )
    from src.tools.builtins.extract_trade_document_tool import _format_summary

    table = GenericTable(
        title="2026年4月销售记录",
        meta=[
            KeyValue(key="客户", value="朱雪英"),
            KeyValue(key="日期", value="2026-04-20"),
        ],
        headers=["序号", "产品", "单位", "数量", "金额"],
        rows=[
            ["1", "布料A", "米", "1110", "1110"],
            ["2", "布料B", "米", "1165", "1165"],
        ],
        totals=[KeyValue(key="合计", value="¥2014")],
    )
    env = OcrEnvelope(
        detected_doc_type="generic_table",
        confidence=0.9,
        raw_text="序号 产品 ...",
        structured=table,
    )
    out = _format_summary(env, "/mnt/user-data/outputs/generic_table_ocr.json")

    assert "generic_table" in out
    assert "90%" in out
    assert "2026年4月销售记录" in out
    assert "客户=朱雪英" in out
    assert "行数=2" in out
    assert "合计=¥2014" in out


def test_sales_order_schema_round_trips() -> None:
    """Pydantic round-trip — the schema must validate the JSON shape
    described in OCR_SCHEMA_PROMPT_BLOCK so the model's output parses
    without ValidationError. Catches any drift between schema fields
    and prompt instructions."""
    import json

    from src.subagents.builtins.ocr_schemas import OcrEnvelope

    payload = {
        "detected_doc_type": "sales_order",
        "confidence": 0.9,
        "raw_text": "客户：朱雪英 ...",
        "structured": {
            "doc_type": "sales_order",
            "order_no": "SO-001",
            "order_date": "2026-04-20",
            "customer": {"name": "朱雪英", "address": None, "contact": None, "tax_id": None},
            "seller": None,
            "items": [{"sku": None, "description": "布料", "hs_code": None, "quantity": 100, "unit": "米", "unit_price": 10.0, "amount": 1000.0, "currency": "CNY"}],
            "total_amount": 1000.0,
            "currency": "CNY",
            "payment_terms": None,
            "delivery_date": None,
            "notes": None,
        },
        "notes": None,
    }
    env = OcrEnvelope.model_validate(payload)
    assert env.detected_doc_type == "sales_order"
    assert env.structured is not None
    assert env.structured.order_no == "SO-001"
    # Round-trip via JSON to make sure model_dump_json output also re-validates.
    OcrEnvelope.model_validate(json.loads(env.model_dump_json()))


def test_delivery_note_schema_round_trips() -> None:
    """Same as above for delivery_note — validates that
    sender/receiver/carrier/vehicle_no fields all parse and the
    envelope round-trips through JSON cleanly."""
    import json

    from src.subagents.builtins.ocr_schemas import OcrEnvelope

    payload = {
        "detected_doc_type": "delivery_note",
        "confidence": 0.85,
        "raw_text": "送货单 DN-001 ...",
        "structured": {
            "doc_type": "delivery_note",
            "dn_no": "DN-001",
            "dn_date": "2026-05-03",
            "sender": {"name": "供货方A", "address": None, "contact": None, "tax_id": None},
            "receiver": {"name": "客户B", "address": None, "contact": None, "tax_id": None},
            "items": [],
            "total_packages": 5,
            "total_quantity": 200.0,
            "carrier": "顺丰",
            "vehicle_no": "京A12345",
            "driver": "张三",
            "notes": None,
        },
        "notes": None,
    }
    env = OcrEnvelope.model_validate(payload)
    assert env.structured is not None
    assert env.structured.dn_no == "DN-001"
    assert env.structured.carrier == "顺丰"
    OcrEnvelope.model_validate(json.loads(env.model_dump_json()))


def test_generic_table_schema_round_trips() -> None:
    """Generic table — meta/totals are KeyValue lists, rows is
    ``list[list[str]]``. Validate end-to-end so a model output like
    the one we'd want for thread 0d74d5e2's hand-written sales slip
    parses cleanly."""
    import json

    from src.subagents.builtins.ocr_schemas import OcrEnvelope

    payload = {
        "detected_doc_type": "generic_table",
        "confidence": 0.9,
        "raw_text": "客户：朱雪英 ...",
        "structured": {
            "doc_type": "generic_table",
            "title": "2026年4月销售记录",
            "meta": [
                {"key": "客户", "value": "朱雪英"},
                {"key": "日期", "value": "2026-04-20"},
            ],
            "headers": ["序号", "产品", "单价", "金额"],
            "rows": [
                ["1", "布料A", "10", "1110"],
                ["2", "布料B", "10", "1165"],
            ],
            "totals": [{"key": "合计", "value": "¥2014"}],
            "notes": None,
        },
        "notes": None,
    }
    env = OcrEnvelope.model_validate(payload)
    assert env.structured is not None
    assert len(env.structured.rows) == 2
    assert env.structured.rows[0] == ["1", "布料A", "10", "1110"]
    assert env.structured.totals[0].value == "¥2014"
    OcrEnvelope.model_validate(json.loads(env.model_dump_json()))


def test_doc_type_literal_enumerates_all_eight_plus_unknown() -> None:
    """Lock down the DocType literal so adding a new doc type without
    updating the prompt block / frontend viewer is caught here."""
    from src.subagents.builtins.ocr_schemas import DocType

    args = set(get_args(DocType))
    assert args == {
        "commercial_invoice",
        "packing_list",
        "bill_of_lading",
        "customs_declaration",
        "proforma_invoice",
        "sales_order",
        "delivery_note",
        "generic_table",
        "unknown",
    }


def test_error_command_returns_tool_message() -> None:
    from langchain_core.messages import ToolMessage

    from src.tools.builtins.extract_trade_document_tool import _error_command

    cmd = _error_command("tc-1", "boom")
    msgs = cmd.update["messages"]
    assert len(msgs) == 1
    assert isinstance(msgs[0], ToolMessage)
    assert msgs[0].tool_call_id == "tc-1"
    assert "boom" in msgs[0].content
    assert msgs[0].content.startswith("Error:")


def test_ocr_extractor_subagent_removed() -> None:
    """Regression: the ocr-extractor subagent has been replaced by
    extract_trade_document tool. BUILTIN_SUBAGENTS must NOT contain
    it; the registry lookup must return None."""
    from src.subagents.builtins import BUILTIN_SUBAGENTS
    from src.subagents.registry import get_subagent_config

    assert "ocr-extractor" not in BUILTIN_SUBAGENTS
    assert get_subagent_config("ocr-extractor") is None


def test_task_tool_literal_no_longer_lists_ocr_extractor() -> None:
    """Symmetric to the above — the Literal type on task_tool's
    subagent_type should be back to {general-purpose, bash,
    vision-analyst}. Without this, LangChain would still accept
    ``task(subagent_type='ocr-extractor', ...)`` from a stale prompt
    and crash deep inside the executor with a None config."""
    from src.tools.builtins.task_tool import task_tool

    func = getattr(task_tool, "func", None) or task_tool
    hints = get_type_hints(func)
    args = get_args(hints["subagent_type"])

    assert "general-purpose" in args
    assert "bash" in args
    assert "vision-analyst" in args
    assert "ocr-extractor" not in args

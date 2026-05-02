"""Pydantic schemas for foreign-trade document OCR extraction.

The ``ocr-extractor`` subagent's output MUST validate against
``OcrEnvelope``. Schema is intentionally permissive (most fields are
``str | None``) because OCR fidelity varies — the goal is "fail closed
on missing data, not fail loud on a misread Unicode character."

Date strings stay as ``str`` (ISO ``yyyy-mm-dd``) rather than
``datetime.date`` so the LLM can write them directly without round-trip
parsing; the frontend renders them as-is. Numbers are floats so the LLM
doesn't have to think about int/decimal coercion.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Five known foreign-trade document types. ``unknown`` is the catch-all
# the LLM picks when classification confidence is low — the envelope
# still parses, ``structured`` is None, and the user sees the raw text
# with a warning chip.
DocType = Literal[
    "commercial_invoice",
    "packing_list",
    "bill_of_lading",
    "customs_declaration",
    "proforma_invoice",
    "unknown",
]

# ISO-4217 three-letter currency code or None when not visible.
Currency = str | None


class Party(BaseModel):
    """Buyer / seller / shipper / consignee / notify party.

    Address kept as a single free-text field rather than parsed sub-fields
    because international address formats vary too much to normalize at
    OCR time. Any tax ID (中国统一社会信用代码 / EIN / VAT) goes into
    ``tax_id`` regardless of format.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    address: str | None = None
    contact: str | None = None
    tax_id: str | None = None


class LineItem(BaseModel):
    """A row in the items table of an invoice / packing list / declaration."""

    model_config = ConfigDict(extra="forbid")

    sku: str | None = None
    description: str = ""
    hs_code: str | None = None
    quantity: float | None = None
    unit: str | None = None  # PCS / KG / CTN / SET / ...
    unit_price: float | None = None
    amount: float | None = None
    currency: Currency = None  # ISO-4217 three-letter code


class CommercialInvoice(BaseModel):
    """商业发票 — the primary settlement document for a shipment."""

    model_config = ConfigDict(extra="forbid")

    doc_type: Literal["commercial_invoice"] = "commercial_invoice"
    invoice_no: str | None = None
    invoice_date: str | None = None  # ISO yyyy-mm-dd
    seller: Party | None = None
    buyer: Party | None = None
    incoterm: str | None = None  # FOB / CIF / DDP / EXW / ...
    port_of_loading: str | None = None
    port_of_discharge: str | None = None
    items: list[LineItem] = Field(default_factory=list)
    total_amount: float | None = None
    currency: Currency = None
    payment_terms: str | None = None  # T/T 30% deposit ... / L/C at sight ...


class PackingList(BaseModel):
    """装箱单 — quantity / weight / volume per package, no prices."""

    model_config = ConfigDict(extra="forbid")

    doc_type: Literal["packing_list"] = "packing_list"
    pl_no: str | None = None
    pl_date: str | None = None
    seller: Party | None = None
    buyer: Party | None = None
    shipping_marks: str | None = None
    total_packages: int | None = None
    total_gross_weight_kg: float | None = None
    total_net_weight_kg: float | None = None
    total_volume_cbm: float | None = None
    items: list[LineItem] = Field(default_factory=list)


class BillOfLading(BaseModel):
    """提单 (B/L) — title document for the cargo, issued by the carrier."""

    model_config = ConfigDict(extra="forbid")

    doc_type: Literal["bill_of_lading"] = "bill_of_lading"
    bl_no: str | None = None
    bl_date: str | None = None
    shipper: Party | None = None
    consignee: Party | None = None
    notify_party: Party | None = None
    vessel: str | None = None
    voyage_no: str | None = None
    port_of_loading: str | None = None
    port_of_discharge: str | None = None
    place_of_delivery: str | None = None
    container_nos: list[str] = Field(default_factory=list)
    seal_nos: list[str] = Field(default_factory=list)
    packages: int | None = None
    gross_weight_kg: float | None = None
    measurement_cbm: float | None = None
    freight_terms: Literal["PREPAID", "COLLECT"] | None = None


class CustomsDeclaration(BaseModel):
    """报关单 — submitted to customs at export. Layout differs per country
    but core fields are similar. We keep this minimal; per-country
    extensions can subclass later if real demand shows up."""

    model_config = ConfigDict(extra="forbid")

    doc_type: Literal["customs_declaration"] = "customs_declaration"
    declaration_no: str | None = None
    declaration_date: str | None = None
    exporter: Party | None = None
    consignee: Party | None = None
    trade_country: str | None = None
    transport_mode: str | None = None  # SEA / AIR / LAND / ...
    transport_no: str | None = None
    items: list[LineItem] = Field(default_factory=list)
    total_amount: float | None = None
    currency: Currency = None


class ProformaInvoice(BaseModel):
    """形式发票 (PI) — pre-shipment quote / contract; same shape as a
    Commercial Invoice plus a validity date."""

    model_config = ConfigDict(extra="forbid")

    doc_type: Literal["proforma_invoice"] = "proforma_invoice"
    invoice_no: str | None = None
    invoice_date: str | None = None
    valid_until: str | None = None
    seller: Party | None = None
    buyer: Party | None = None
    incoterm: str | None = None
    port_of_loading: str | None = None
    port_of_discharge: str | None = None
    items: list[LineItem] = Field(default_factory=list)
    total_amount: float | None = None
    currency: Currency = None
    payment_terms: str | None = None


# Discriminated union for the ``structured`` field. The LLM sets
# ``doc_type`` on the inner object and pydantic uses it to dispatch.
StructuredDocument = (
    CommercialInvoice
    | PackingList
    | BillOfLading
    | CustomsDeclaration
    | ProformaInvoice
)


class OcrEnvelope(BaseModel):
    """Top-level wrapper that the ocr-extractor subagent MUST return.

    ``raw_text`` holds whatever the OCR model could read off the image,
    used as the human-readable fallback when ``structured`` is None or
    when the user wants to spot-check a field. Always populated.

    ``confidence`` is the LLM's self-reported confidence in the
    classification + extraction; the frontend renders it as a progress
    chip so the user knows when to double-check.
    """

    model_config = ConfigDict(extra="forbid")

    detected_doc_type: DocType
    confidence: float = Field(ge=0.0, le=1.0)
    raw_text: str
    structured: StructuredDocument | None = None
    notes: str | None = None


def _build_schema_prompt_block() -> str:
    """Render an LLM-friendly schema reference block for ocr-extractor.

    We avoid pasting ``model_json_schema()`` directly because the
    nested ``$defs`` form is hostile to a small vision LLM. A
    handwritten field table is shorter, more accurate, and pinned to
    the same field names by ``test_ocr_extractor_prompt_matches_schema``
    so prompt and code can't drift.
    """
    return """\
**Top-level envelope (REQUIRED for every output):**
{
  "detected_doc_type": "commercial_invoice|packing_list|bill_of_lading|customs_declaration|proforma_invoice|unknown",
  "confidence": <float 0..1>,
  "raw_text": "<image 中所有可读文字，按从上到下从左到右顺序，不带格式>",
  "structured": { ...对应 doc_type 的子结构... } | null,
  "notes": "<可选：识别困难/字段缺失说明>" | null
}

**Per-doc-type ``structured`` payloads** (字段缺失填 null，**不要编造**):

- ``commercial_invoice`` / ``proforma_invoice``:
  invoice_no, invoice_date(yyyy-mm-dd), seller{name,address,contact,tax_id},
  buyer{...}, incoterm(FOB/CIF/DDP/...), port_of_loading, port_of_discharge,
  items[{sku, description, hs_code, quantity, unit, unit_price, amount, currency}],
  total_amount, currency(ISO-4217 三字母如 USD/CNY/EUR), payment_terms
  proforma 额外: valid_until

- ``packing_list``:
  pl_no, pl_date, seller, buyer, shipping_marks, total_packages,
  total_gross_weight_kg, total_net_weight_kg, total_volume_cbm,
  items[{sku, description, quantity(=件数), unit}]

- ``bill_of_lading``:
  bl_no, bl_date, shipper, consignee, notify_party, vessel, voyage_no,
  port_of_loading, port_of_discharge, place_of_delivery,
  container_nos[], seal_nos[], packages, gross_weight_kg,
  measurement_cbm, freight_terms("PREPAID"|"COLLECT")

- ``customs_declaration``:
  declaration_no, declaration_date, exporter, consignee,
  trade_country, transport_mode("SEA"|"AIR"|"LAND"|...), transport_no,
  items, total_amount, currency

**硬性规则:**
1. 输出文件必须是有效 JSON（``json.loads`` 能解析），不要 ``\\`\\`\\`json`` 包裹、不要尾随逗号、字符串里换行用 ``\\n``。
2. 日期一律 ``yyyy-mm-dd``。"Mar 5, 2025" → "2025-03-05"，看不全年份填 null。
3. 货币代码用 ISO-4217 三字母（USD/CNY/EUR/JPY/HKD），看到 $ 默认 USD 但在 ``notes`` 里写明。
4. 数字不带千分位逗号、不带货币符号: ``amount=15000.00`` 而非 ``"$15,000"``。
5. 看不清/没出现的字段填 ``null``，**不要**编造（hallucination 是最严重违规）。
6. **禁止**调用 ERPNext / bash / 网络工具——你只识别，不入库。
"""


# Public constant so the ocr-extractor system prompt can splice it in.
OCR_SCHEMA_PROMPT_BLOCK: str = _build_schema_prompt_block()

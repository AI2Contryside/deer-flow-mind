"""Pydantic schemas for trade-document and generic-table OCR extraction.

The ``extract_trade_document`` tool's output MUST validate against
``OcrEnvelope``. Schema is intentionally permissive (most fields are
``str | None``) because OCR fidelity varies — the goal is "fail closed
on missing data, not fail loud on a misread Unicode character."

Date strings stay as ``str`` (ISO ``yyyy-mm-dd``) rather than
``datetime.date`` so the LLM can write them directly without round-trip
parsing; the frontend renders them as-is. Numbers are floats so the LLM
doesn't have to think about int/decimal coercion.

Doc-type coverage (8 known + 1 catch-all):
* International trade: ``commercial_invoice``, ``packing_list``,
  ``bill_of_lading``, ``customs_declaration``, ``proforma_invoice``.
* Domestic trade (added 2026-05): ``sales_order``, ``delivery_note``.
  Many trademind users upload Chinese sales/delivery slips that the
  international-only schema couldn't classify, forcing ``unknown`` and
  destroying every structured field even when OCR succeeded.
* Tabular fallback: ``generic_table`` is the structured catch-all for
  any document that has a clear table layout but doesn't match the
  named types above (price lists, inventory sheets, hand-written
  receipts, etc.). Picking ``generic_table`` is preferred over
  ``unknown`` whenever the model can identify rows + columns.
* ``unknown`` stays as the last resort for unreadable images or
  truly unstructured content.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Eight known doc types + ``unknown`` catch-all. ``unknown`` is reserved
# for images the model truly can't classify or extract — when there IS
# a visible table the model should prefer ``generic_table`` so the
# rows/columns survive into the artifact view.
DocType = Literal[
    "commercial_invoice",
    "packing_list",
    "bill_of_lading",
    "customs_declaration",
    "proforma_invoice",
    "sales_order",
    "delivery_note",
    "generic_table",
    "unknown",
]

# ISO-4217 three-letter currency code or None when not visible.
Currency = str | None


class Party(BaseModel):
    """Buyer / seller / shipper / consignee / notify party / customer.

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
    """A row in the items table of an invoice / packing list / declaration / sales order / delivery note."""

    model_config = ConfigDict(extra="forbid")

    sku: str | None = None
    description: str = ""
    hs_code: str | None = None
    quantity: float | None = None
    unit: str | None = None  # PCS / KG / CTN / SET / 米 / 件 / ...
    unit_price: float | None = None
    amount: float | None = None
    currency: Currency = None  # ISO-4217 three-letter code


class KeyValue(BaseModel):
    """Simple key-value pair used by ``GenericTable`` for free-form metadata
    (header KV like 单号/日期/客户) and totals (合计/折扣/税额).

    Both fields are strings so the model can write them verbatim — the
    frontend doesn't need to coerce types for display, and we sidestep
    "is 1,234.56 a number or a string with thousands separator" debates
    at OCR time. The named typed schemas (CommercialInvoice etc.) still
    parse numbers as floats; this is only for the generic fallback.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    value: str


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


class SalesOrder(BaseModel):
    """销售订单 — domestic sales order / 销货单 / 售货确认书.

    Most trademind users in mainland China upload a hand-written or
    printed sales slip rather than an international-style invoice. The
    layout typically has a customer (客户), an order number (单号),
    a date, line items, and a total in CNY. Currency defaults to CNY
    when the model can't read one explicitly because domestic slips
    rarely state it (the prompt block instructs the model to apply
    that default and note the assumption).
    """

    model_config = ConfigDict(extra="forbid")

    doc_type: Literal["sales_order"] = "sales_order"
    order_no: str | None = None
    order_date: str | None = None  # ISO yyyy-mm-dd
    customer: Party | None = None
    seller: Party | None = None
    items: list[LineItem] = Field(default_factory=list)
    total_amount: float | None = None
    currency: Currency = None  # 国内单据通常无显式币种 → 在 prompt 中默认 CNY
    payment_terms: str | None = None
    delivery_date: str | None = None  # ISO yyyy-mm-dd
    notes: str | None = None  # 备注/说明


class DeliveryNote(BaseModel):
    """送货单 / 发货单 / 出货单 — domestic delivery slip.

    Driver / receiver / vehicle are common; price columns are often
    absent. Any visible quantity total goes into ``total_quantity``
    rather than per-line-item summing — the model just transcribes the
    "合计" cell verbatim instead of trying to reconcile arithmetic.
    """

    model_config = ConfigDict(extra="forbid")

    doc_type: Literal["delivery_note"] = "delivery_note"
    dn_no: str | None = None
    dn_date: str | None = None  # ISO yyyy-mm-dd
    sender: Party | None = None  # 发货方/供货方
    receiver: Party | None = None  # 收货方/客户
    items: list[LineItem] = Field(default_factory=list)
    total_packages: int | None = None  # 总件数
    total_quantity: float | None = None  # 合计 (件/米/kg 由 unit 决定)
    carrier: str | None = None  # 承运方/快递公司
    vehicle_no: str | None = None  # 车牌号
    driver: str | None = None  # 司机
    notes: str | None = None


class GenericTable(BaseModel):
    """通用表格兜底 — any tabular document that doesn't match the named
    types above.

    Used for price lists, inventory sheets, hand-written receipts,
    rebate calculations, and the long tail of "I uploaded a table, just
    give me back the cells" requests. The schema is intentionally weak:

    * ``meta`` is a flat list of header KV pairs (单号/日期/客户/etc.)
      so the model doesn't have to fit them into a typed schema.
    * ``headers`` + ``rows`` keeps the table structure but stores
      everything as strings — the model writes cells verbatim and the
      frontend renders them with no type coercion. This avoids the
      "is column 3 always a number" guesswork that breaks the moment
      someone scribbles 'N/A' in one row.
    * ``totals`` carries any 合计 / 小计 / 折扣 / 税额 row (also as
      KV pairs), so the user gets "总额=2014" without us inventing a
      total_amount field that may not exist.

    Use this whenever the named types don't fit but the model can still
    pick out a table — much better than dropping straight to ``unknown``
    where ``structured`` is null and the frontend can only show raw text.
    """

    model_config = ConfigDict(extra="forbid")

    doc_type: Literal["generic_table"] = "generic_table"
    title: str | None = None  # 单据标题/抬头，看不出就 null
    meta: list[KeyValue] = Field(default_factory=list)  # 头部 KV：单号/日期/客户/...
    headers: list[str] = Field(default_factory=list)  # 列头
    rows: list[list[str]] = Field(default_factory=list)  # 行（每行长度应等于 headers）
    totals: list[KeyValue] = Field(default_factory=list)  # 合计/小计/税额
    notes: str | None = None


# Discriminated union for the ``structured`` field. The LLM sets
# ``doc_type`` on the inner object and pydantic uses it to dispatch.
StructuredDocument = CommercialInvoice | PackingList | BillOfLading | CustomsDeclaration | ProformaInvoice | SalesOrder | DeliveryNote | GenericTable


class OcrEnvelope(BaseModel):
    """Top-level wrapper that the extract_trade_document tool MUST return.

    ``raw_text`` holds whatever the OCR model could read off the image,
    used as the human-readable fallback when ``structured`` is None or
    when the user wants to spot-check a field. Always populated.

    ``confidence`` is the LLM's self-reported confidence in the
    **OCR text recognition itself** — i.e. "how sure am I that the
    characters/numbers I transcribed match the image". It is NOT a
    measure of "is this a known document type" — that question is
    already answered by ``detected_doc_type``. So a clear, fully
    readable image of a hand-written domestic receipt should still get
    a high ``confidence`` (≥0.8) even if the model classifies it as
    ``generic_table`` or ``unknown``. Only lower confidence when the
    image is blurry, partially occluded, low-resolution, or has
    illegible handwriting.
    """

    model_config = ConfigDict(extra="forbid")

    detected_doc_type: DocType
    confidence: float = Field(ge=0.0, le=1.0)
    raw_text: str
    structured: StructuredDocument | None = None
    notes: str | None = None


def _build_schema_prompt_block() -> str:
    """Render an LLM-friendly schema reference block for the OCR tool.

    We avoid pasting ``model_json_schema()`` directly because the
    nested ``$defs`` form is hostile to a small vision LLM. A
    handwritten field table is shorter, more accurate, and pinned to
    the same field names by ``test_extract_prompt_embeds_ocr_schema``
    so prompt and code can't drift.
    """
    return """\
**Top-level envelope (REQUIRED for every output):**
{
  "detected_doc_type": "commercial_invoice|packing_list|bill_of_lading|customs_declaration|proforma_invoice|sales_order|delivery_note|generic_table|unknown",
  "confidence": <float 0..1, **OCR 文字识别可信度**，与文档分类是否成功无关>,
  "raw_text": "<image 中所有可读文字，按从上到下从左到右顺序，不带格式>",
  "structured": { ...对应 doc_type 的子结构... } | null,
  "notes": "<可选：识别困难/字段缺失/默认值假设说明>" | null
}

**``confidence`` 评分规则（重要，请认真阅读）：**

- ``confidence`` 表示你对**文字识别本身**的信心，**不是**对"分类是否成功"的信心。
- 图片清晰、字符全部辨认得出 → ``0.85~1.0``，**即使** ``detected_doc_type`` 是 ``generic_table`` 或 ``unknown``。
- 图片整体可读但部分字段模糊/手写难辨 → ``0.5~0.8``。
- 图片严重模糊/仅能看到零星字符/分辨率不足 → ``0.0~0.4``。
- ⚠ **不要因为"这不是外贸单据"就降低 confidence**——分类用 ``detected_doc_type`` 表达，OCR 质量用 ``confidence`` 表达。

**``detected_doc_type`` 选择优先级（从高到低）：**

1. 如果是国际贸易单据 → 选对应的具体类型（``commercial_invoice``/``packing_list``/``bill_of_lading``/``customs_declaration``/``proforma_invoice``）。
2. 如果是国内销售单/订单（中文为主，常见字段：客户/单号/日期/明细/合计 ¥）→ ``sales_order``。
3. 如果是国内送货单/发货单/出货单（含发货方/收货方/承运/车牌等）→ ``delivery_note``。
4. 上述都不匹配但**有清晰表格结构**（列头 + 多行数据）→ ``generic_table``，把表格头放进 ``headers``，每行放进 ``rows``，合计/总计放进 ``totals``。
5. 真的无法识别为表格或文档（纯手写信件、纯图片、严重模糊到不可读）→ ``unknown``。
   ⚠ 在选择 ``unknown`` 之前**先尝试** ``generic_table``——只要能看出表格结构，就用 ``generic_table``。

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

- ``sales_order``（国内销售订单/销货单）:
  order_no, order_date(yyyy-mm-dd), customer{name,address,contact,tax_id},
  seller(可能为空), items[{sku, description, quantity, unit, unit_price, amount, currency}],
  total_amount, currency(国内单据无显式币种时填 "CNY" 并在 notes 中写明假设),
  payment_terms, delivery_date, notes

- ``delivery_note``（国内送货单/发货单）:
  dn_no, dn_date(yyyy-mm-dd), sender(发货方), receiver(收货方/客户),
  items[{sku, description, quantity, unit}],
  total_packages(总件数), total_quantity(合计数量),
  carrier(承运), vehicle_no(车牌), driver(司机), notes

- ``generic_table``（兜底通用表格）:
  title(单据标题，例如 "2026年4月销售记录"),
  meta[{key, value}]（头部 KV：客户、单号、日期等，每个 entry 是一对）,
  headers[字符串列头数组]（例如 ["序号","产品","单位","数量","单价","金额"]）,
  rows[二维字符串数组]（每行长度 = headers 长度，单元格按所见原样写入字符串），
  totals[{key, value}]（合计行，例如 {"key":"合计","value":"¥2014"}），
  notes(可选)

**硬性规则:**
1. 输出文件必须是有效 JSON（``json.loads`` 能解析），不要 ``\\`\\`\\`json`` 包裹、不要尾随逗号、字符串里换行用 ``\\n``。
2. 日期一律 ``yyyy-mm-dd``。"Mar 5, 2025" → "2025-03-05"，看不全年份填 null。
3. 货币代码用 ISO-4217 三字母（USD/CNY/EUR/JPY/HKD），看到 $ 默认 USD 但在 ``notes`` 里写明。国内单据无币种时 ``sales_order`` 默认 CNY 并在 notes 注明。
4. 数字不带千分位逗号、不带货币符号: ``amount=15000.00`` 而非 ``"$15,000"``。``generic_table`` 里因为 cells 是字符串，可以保留原样。
5. 看不清/没出现的字段填 ``null``，**不要**编造（hallucination 是最严重违规）。
6. **禁止**调用 ERPNext / bash / 网络工具——你只识别，不入库。
"""


# Public constant so the extract_trade_document prompt can splice it in.
OCR_SCHEMA_PROMPT_BLOCK: str = _build_schema_prompt_block()

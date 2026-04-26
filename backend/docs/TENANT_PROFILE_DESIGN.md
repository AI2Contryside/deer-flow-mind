# Tenant Profile Design

> 让 AI 不用每轮对话都花若干次工具调用去重新确认"这家租户是哪家公司、常用哪些客户/供应商/物料/仓库" —— 把可观察的稳定事实抽成 profile，开会话时直接注入 system prompt。
>
> **v1 scope**：核心交易（Selling / Buying / Stock / Accounts）+ Setup 模块。Manufacturing / Subcontracting / CRM / Projects / Assets 留 v2。
>
> **业务参考**：详尽的 ERPNext 业务模型见仓库根目录 `docs/ERPNEXT_BUSINESS_LOGIC.md`，本文档只引用其中与 profile 决策相关的部分。

---

## 1. 设计原则

1. **不是 schema 缓存（"系统里有哪些仓库"），是 operational memory（"这家常用哪些仓库"）**。判断标准是该字段对 LLM 决策有用，而不是 ERPNext 主数据完整性。
2. **观察层机械、总结层智能**。数据采集纯解析无 LLM，廉价；何时入榜、如何描述、是否衰减交给 sub-agent。
3. **三大 ledger 永不进 profile**。SLE / GL Entry / PLE 是流水真源，每秒都在变；进 profile 即刻过期。
4. **Transactional DocType 不进 profile，但其引用关系进**。SO/PO/SI/PI/DN/PR/PE/JE/MR/Stock Entry 不进 profile，但它们 payload 里出现的 `customer / supplier / item_code / warehouse / cost_center / tax_template` 才是真正"在用"的信号源。
5. **多公司、多币种是一等公民**。Setup 模块里 Company 是 NestedSet；Items/Customers/Suppliers 跨公司共享，但 Account/Warehouse/Cost Center/defaults 按公司隔离。Profile 必须区分。
6. **状态字段、金额字段、百分比字段绝不入榜**。`status / outstanding_amount / per_billed / per_delivered / actual_qty / projected_qty / valuation_rate` 都是动态派生量。

---

## 2. 三层架构

```
┌────────────────────────────────────────────────────────────────┐
│ 1. 观察层 [机械, 廉价, 无 LLM]                                  │
│    FrappeClient hook → usage_log.jsonl                          │
│    每次调用提取 (doctype, name, op, link_field?, ts)            │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│ 2. 总结层 [sub-agent, 离线异步]                                 │
│    profile_summarizer:                                          │
│      input  = facts + previous_profile + usage_log_since_last   │
│      output = tenant_profile.json (强 schema, pydantic 校验)    │
│    触发  = 事件量阈值 OR 时间阈值 OR 显式工具                    │
│    工具  = ERPNext read-only (get_doc / get_list)               │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│ 3. 注入层 [prompt 拼装时]                                       │
│    apply_prompt_template(tenant_id):                            │
│      _get_profile_context() → 渲染成系统提示段                   │
│      _get_memory_context()  → 已有 memory 段                     │
└────────────────────────────────────────────────────────────────┘
```

---

## 3. 字段白名单

### 3.1 Facts（bootstrap 一次性拉，TTL 24h，只有身份/配置类单值）

| 来源 | 字段 | 备注 |
|---|---|---|
| `Company`（按 NestedSet 列出，标识 primary） | `name, abbr, default_currency, country, default_warehouse, default_cost_center, default_income_account, default_expense_account, default_payable_account, default_receivable_account` | 多公司租户全列；primary 由当前用户的 `Employee.company` 决定，找不到则用第一个 |
| `Fiscal Year`（current） | `name, year_start_date, year_end_date` | 取 today 落在区间内的那个 |
| `Selling Settings` (singleton) | `cust_master_name, so_required, dn_required, sales_update_frequency, validate_selling_price, allow_against_multiple_purchase_orders` | 决定 SO→DN→SI 是否可跳步 |
| `Stock Settings` (singleton) | `item_naming_by, valuation_method, default_warehouse, allow_negative_stock, auto_indent, sample_retention_warehouse` | 决定估值方法 / 是否允许负库存 |
| `Accounts Settings` (singleton) | `auto_accounting_for_stock, acc_frozen_upto, credit_controller, role_allowed_to_over_bill` | 决定是否做 perpetual inventory |
| `Buying Settings` (singleton) | `supp_master_name, po_required, maintain_same_rate` | |
| `Global Defaults` (singleton) | `default_company, default_currency, country, hide_currency_symbol` | |
| `Employee`（current user 关联） | `name, employee_name, department, designation, company, default_shift` | |
| `User`（current user） | `email, full_name, language, time_zone, roles`（过滤系统角色，仅留业务相关） | |

### 3.2 Frequent（观察累积，top-K，K 配置化）

按业务文档 §4 的模块归类，每行：DocType — 入榜时一次性拉的字段（白名单严格） — 默认 K — 备注。

#### Selling
| DocType | 字段 | 默认 K | 信号源 |
|---|---|---|---|
| `Customer` | `name, customer_name, customer_group, territory, default_currency, default_price_list, default_sales_partner, payment_terms` | 20 | SO/Quotation/SI/DN/PE.party=Customer |

#### Buying
| `Supplier` | `name, supplier_name, supplier_group, country, default_currency, default_price_list, payment_terms` | 20 | PO/RFQ/SQ/PI/PR/PE.party=Supplier |

#### Stock
| `Item` | `name, item_name, item_group, stock_uom, is_stock_item, has_variants, has_serial_no, has_batch_no, default_warehouse(per primary company)` | 30 | 任何交易子表的 `item_code` |
| `Warehouse` | `name, parent_warehouse, company, warehouse_type, is_group` | 10 | DN/PR/SE/SLE 的 warehouse / from_warehouse / to_warehouse |
| `Price List` | `name, currency, buying, selling` | 5 | SO/Quotation/PO.selling_price_list / buying_price_list |
| `UOM` | `name, must_be_whole_number` | 10 | Item.stock_uom + 子表 uom |
| `Item Group` | `name, parent_item_group, is_group` | 10 | Item.item_group（推导树形上下文） |

#### Accounts
| `Account` | `name, account_type, root_type, company, is_group` | 15 | GL link 字段，但**仅当出现在 Tax Template / Payment Entry / Default Account 等"被引用"位置时计入**；不计 GL Entry 直接抓取（那是 ledger，太多） |
| `Cost Center` | `name, parent_cost_center, company, is_group` | 10 | 任何交易行的 `cost_center` |
| `Sales Taxes and Charges Template` | `name, company, is_default` | 5 | SO/SI.taxes_and_charges |
| `Purchase Taxes and Charges Template` | `name, company, is_default` | 5 | PO/PI.taxes_and_charges |
| `Payment Terms Template` | `name, +terms[].{credit_days, due_date_based_on, invoice_portion}` | 5 | SO/SI/PO/PI.payment_terms_template |
| `Pricing Rule` | `name, applicable_for, +items[]` | 5 | 出现在 SO/SI 项的 `pricing_rules` |

#### Setup（分类树 + 销售员）
| `Customer Group` | `name, parent_customer_group, is_group` | 10 | Customer.customer_group |
| `Supplier Group` | `name, parent_supplier_group, is_group` | 10 | Supplier.supplier_group |
| `Territory` | `name, parent_territory, is_group` | 10 | Customer.territory / SO.territory |
| `Sales Person` | `name, parent_sales_person, sales_person_name, is_group` | 10 | SO/SI.sales_team[].sales_person |
| `Brand` | `name` | 5 | Item.brand |

### 3.3 Denylist（即使高频也永不入榜）

**Transactional**: Quotation, Sales Order, Sales Invoice, Delivery Note, Pick List, Stock Reservation Entry, Material Request, Request for Quotation, Supplier Quotation, Purchase Order, Purchase Receipt, Purchase Invoice, Payment Entry, Journal Entry, Stock Entry, Stock Reconciliation, Shipment, Quality Inspection, Landed Cost Voucher, Dunning, Subscription, Subscription Plan, POS Invoice, POS Opening/Closing Entry, Bank Transaction, Period Closing Voucher。

**Ledger**: Stock Ledger Entry, GL Entry, Payment Ledger Entry, Bin, Serial and Batch Bundle, Item Price（逐物料价位行，太碎；价格信息走 Price List 摘要）。

**v2 暂不收**: Manufacturing 全模块（BOM/WO/Job Card/Workstation/Routing/Production Plan）、Subcontracting 全模块、Assets 全模块、CRM 全模块（Lead/Opportunity/Prospect/Campaign）、Projects 全模块。

---

## 4. 观察层

### 4.1 Hook 位置

挂在 `skills/public/erpnext-cli/scripts/erpnext_pkg/core/client.py` 的 `FrappeClient`，收口于：`get_doc / get_list / insert / update / submit / cancel / call_method`。每次调用 **成功** 后追加一条事件到 `tenant_profile/{tenant_id}/usage_log.jsonl`。

不在 skill 业务层挂钩 —— 那会要求每个 skill 单独适配；client 层挂一次全局生效。

### 4.2 一次"事件"长这样

```jsonc
{
  "ts": "2026-04-26T10:23:45.123Z",
  "tenant_id": "...",
  "skill": "erpnext-cli/selling/order-to-cash",  // 触发的 skill 路径，便于上下文
  "op": "call_method",                            // get_doc | get_list | insert | update | submit | cancel | call_method
  "doctype": "Sales Order",                       // 主调用 doctype
  "name": "SAL-ORD-2026-00042",                   // 主调用单据名（如有）
  "extracted_refs": [                             // 关键：从 payload / response 抽出的 link 引用
    {"doctype": "Customer", "name": "FOOCORP-001", "field": "customer", "weight": "primary"},
    {"doctype": "Item", "name": "VLV-001", "field": "items[].item_code", "weight": "primary"},
    {"doctype": "Warehouse", "name": "Stores - ACME", "field": "items[].warehouse", "weight": "primary"},
    {"doctype": "Price List", "name": "Standard Selling", "field": "selling_price_list", "weight": "secondary"}
  ]
}
```

### 4.3 计数规则（这是 v1 的核心）

按业务文档 §8（make_* 链）和 §6（三大 ledger）的洞察：**link-field 引用是"在用"的最强信号**，因为 ERPNext 的所有跨单据流转都靠 `make_*` + link 字段，不靠其他 doctype 互相 list 浏览。

| 调用形态 | extracted_refs 怎么填 | weight |
|---|---|---|
| `get_doc(dt, name)` | `{dt, name, field:"_self"}` | `primary` |
| `get_list(dt, filters={name:"X"})` 返回 ≤5 | 每条结果 `{dt, name, field:"_self"}` | `primary` |
| `get_list(dt)` 返回 >5 或无 filter | **跳过 transactional doctype 的整批结果**；master 数据回填 weight=`browse`（仅参考） | `browse` |
| `insert(doc)` / `update(dt, name, ...)` | self + payload 中所有 link 字段（含 child table 的 `item_code / warehouse / cost_center / account`）逐条展开 | `primary` |
| `submit(dt, name)` / `cancel(dt, name)` | 同 update | `primary` |
| `call_method(...)`（含 `make_*`） | 解析 method 名 + payload，递归抽 link 字段；method 含 `make_` 前缀视为强信号 | `primary` |
| schema/meta 调用 | 不计 | — |

**weight 由 summarizer 解读**：`primary` 强信号入榜，`secondary` 一般信号弱化，`browse` 信号默认不入榜（除非反复出现）。

### 4.4 反污染

- **同一 thread 内同一 `(doctype, name)` 限计 1 次**（thread_id 作为去重键），防止工具循环刷分。
- **transactional 调用对结果 list 整批不计数**（如 `get_list("Sales Order", limit=50)` 不会让那 50 个 SO 自身入榜 —— 它们本来就在 denylist；但其中引用的 customer/item 也不展开，因为这是浏览不是操作）。
- **discovery flag**：skill payload 里若声明 `--purpose discovery|search|browse`（v1 可不实现），事件不计；v2 引入。
- **写后回读不重复计数**：insert 后立即 get_doc 同名读回，第二次仅作为 weight 强化，不增计数。

### 4.5 文件布局与轮转

**本地**（`backend/.deer-flow/tenant_profile/{tenant_id}/`）：

```
├── usage_log.jsonl                                  # 追加日志（当期）
├── usage_log.archive/                               # 上传后保留的本地归档副本（gzip）
│   └── usage_log-<YYYYMMDD>-<sha8>.jsonl.gz
├── profile.json                                     # summarizer 输出
└── meta.json                                        # last_summarize_ts, event_count_since_last,
                                                     # schema_version, pending_oss_uploads[]
```

**远端归档**（阿里云 OSS，复用项目既有 `src/storage/` 抽象）：

```
oss://trademind-chat-session/tenants/{tenant_id}/profile/usage_log/<YYYYMMDD>-<sha8>.jsonl.gz
```

- bucket 与 chat uploads / artifacts 共用 `trademind-chat-session`
- key 模板加进 `src/storage/keys.py`（新增 `tenant_profile_usage_log_key(tenant_id, date, sha8)`），与现有 `chat_upload_key` 风格一致
- 走 `OssClient`（生产）/ `InMemoryStorage`（测试）的 `Storage` Protocol，AK 从 `ALIYUN_OSS_ACCESS_KEY_ID` / `ALIYUN_OSS_ACCESS_KEY_SECRET` 读取（沿用现有 env 约定）
- **保留策略交给 OSS bucket lifecycle rule**（冷归档/过期由运维在控制台配置），应用层不做时间度量

**总结成功后流程**：

1. 当期 `usage_log.jsonl` 关闭、gzip → 计算文件 sha8
2. 写入本地 `usage_log.archive/` 副本（保留，不删）
3. 上传 OSS（best-effort，沿用 storage 模块"missing config 不报错"约定）
4. 上传成功 → meta.json 记录上传时间；上传失败 → 加入 `pending_oss_uploads[]`，下次 summarize 触发前先重试该队列再上传当期
5. 新建空 jsonl 接收下一窗口事件

总结失败时 jsonl 不动，下次再试（OSS 不会上传未总结过的当期 jsonl）。

---

## 5. Summarizer Sub-agent

### 5.1 触发条件（任一满足即入队）

```yaml
tenant_profile:
  trigger:
    event_count_threshold: 50      # 自上次总结起累积事件数
    time_threshold_seconds: 86400  # 24h
    primary_link_event_threshold: 10  # 仅 primary weight 事件计数（更敏感）
  cooldown_seconds: 1800           # 同租户两次总结之间最小间隔
```

显式工具 `refresh_tenant_profile(tenant_id, force=False)` 暴露给主 agent，绕过 cooldown 时需 `force=True`。

### 5.2 输入 contract

喂给 summarizer 的 user message（结构化）：

```jsonc
{
  "tenant_id": "...",
  "now": "2026-04-26T...",
  "facts": { /* 见 §3.1, bootstrap 拉好的，summarizer 不动它 */ },
  "previous_profile": {
    "schema_version": 2,
    "summary": "...",
    "key_entities": { /* 上次产物 */ },
    "operational_patterns": { /* ... */ },
    "open_questions": [ /* ... */ ]
  } | null,
  "usage_window": {
    "from": "2026-04-19T...",
    "to":   "2026-04-26T...",
    "total_events": 327,
    "by_doctype": [
      {"doctype":"Customer","total":42,"primary":38,"secondary":4,"browse":0,
       "top": [
         {"name":"FOOCORP-001","count":24,"first_seen":"...","last_seen":"...",
          "skills":["selling/order-to-cash","accounts/collect-payment"],
          "sample_payload_paths":["sales_order.customer","sales_invoice.customer"]},
         /* 最多每 doctype 送 top 50 条进 prompt，超过截断 */
       ]
      },
      /* ... */
    ]
  },
  "config": {
    "top_k_overrides": { /* 按 §3.2 默认值，可被 config.yaml 覆盖 */ },
    "recently_quiet_runs_to_drop": 2
  }
}
```

**Token 预算**：input ≤8k tokens（usage_window 截断到 top 50/doctype），output ≤2k tokens（profile 强 schema）。

### 5.3 工具访问（read-only）

按用户决定，summarizer **可读不可写**。开放的 ERPNext 工具：

| 工具 | 用途 |
|---|---|
| `erpnext_get_doc(doctype, name)` | 单条字段补齐；解决 `open_questions` |
| `erpnext_get_list(doctype, filters, fields, limit≤20)` | 候选验证；NestedSet doctype 的批量字段补齐（见下） |

**禁止**：`insert / update / submit / cancel / call_method` 全部不可调用。运行环境用 FrappeClient 的只读封装（`ReadOnlyFrappeClient`，覆盖写方法 raise）。

**NestedSet 批量补齐策略**（API 扇出优化）：对 Account / Cost Center / Item Group / Customer Group / Supplier Group / Territory 这 6 个 NestedSet doctype，summarizer 必须**先聚合要入榜的 `name` 列表，再用一次** `get_list(doctype, filters={"name": ["in", [...]]}, fields=[白名单])` **批量拉字段**，禁止逐条 `get_doc`。这把 K=15 个 Account 入榜的扇出从 15 次往返压到 1 次。

**工具调用预算**：单次总结 ≤10 次工具调用，超过截断；其中 6 个 NestedSet doctype 各占 1 次（如全部命中即 6 次），剩 4 次留给 zero-shot get_doc 与 open_questions 解析。

### 5.4 模型选择

```yaml
tenant_profile:
  summarizer_model:
    # 显式配置则用之；缺省 fallback 主 agent 的模型
    provider: anthropic       # 可缺省
    model: claude-haiku-4-5-20251001  # 可缺省
    max_tokens: 4096
    temperature: 0.2          # 低，求稳定结构化输出
```

缺省时调用 `app_config.get_main_agent_model()`。

### 5.5 输出 schema（pydantic 强校验，解析失败保留旧 profile）

```python
class TenantProfile(BaseModel):
    schema_version: Literal[2]
    generated_at: datetime
    summary: str = Field(max_length=500)              # 1-3 句租户画像

    facts: TenantFacts                                 # 透传 input.facts，summarizer 只读

    operational_patterns: OperationalPatterns          # 见下
    key_entities: KeyEntities                          # 按模块组织
    taxonomy: Taxonomy                                 # 分类树
    open_questions: list[OpenQuestion] = Field(max_length=10)

class OperationalPatterns(BaseModel):
    primary_workflow: str | None = None
        # 例: "Order-to-Cash via SO→DN→SI" / "POS-driven retail" / "SO→SI direct (skip DN)"
    currencies_in_use: list[str] = []
        # 从交易看到的实际币种（不只是 default_currency）
    valuation_method_observed: str | None = None
        # 从 Stock Settings + 实际 SLE 事件推断
    default_warehouse_by_company: dict[str, str] = {}
        # company_name → 最常出现的 warehouse（不只看 Stock Settings）
    payment_terms_in_use: list[str] = []

class EntityRef(BaseModel):
    name: str                    # ERPNext 主键，必须在 usage_log 里出现过
    display: str                 # 人类可读名（customer_name 等）
    note: str | None = None      # summarizer 自由文本，≤80 字
    status: Literal["active", "recently_quiet"] = "active"
    quiet_runs: int = 0          # 连续多少次总结未出现
    extras: dict = {}            # 字段白名单内的其他字段

class KeyEntities(BaseModel):
    customers: list[EntityRef] = Field(max_length=20)
    suppliers: list[EntityRef] = Field(max_length=20)
    items: list[EntityRef] = Field(max_length=30)
    warehouses: list[EntityRef] = Field(max_length=10)
    price_lists: list[EntityRef] = Field(max_length=5)
    uoms: list[EntityRef] = Field(max_length=10)
    accounts: list[EntityRef] = Field(max_length=15)
    cost_centers: list[EntityRef] = Field(max_length=10)
    sales_tax_templates: list[EntityRef] = Field(max_length=5)
    purchase_tax_templates: list[EntityRef] = Field(max_length=5)
    payment_terms_templates: list[EntityRef] = Field(max_length=5)

class Taxonomy(BaseModel):
    item_groups: list[EntityRef] = Field(max_length=10)
    customer_groups: list[EntityRef] = Field(max_length=10)
    supplier_groups: list[EntityRef] = Field(max_length=10)
    territories: list[EntityRef] = Field(max_length=10)
    sales_persons: list[EntityRef] = Field(max_length=10)
    brands: list[EntityRef] = Field(max_length=5)

class OpenQuestion(BaseModel):
    question: str
    candidates: list[str] = []   # summarizer 已尝试 get_list 看到的候选
```

**解析失败 fallback**：保留 `previous_profile`，meta.json 记 `last_summarize_error`，次轮重试。绝不写入半成品。

### 5.6 previous_profile 处理（Option C）

按上面定的 (c) 方案：

1. **延续叙述**：`summary` 风格、`note` 措辞、`open_questions` 用词跟着上次走，避免每周输出风格抖动。
2. **重新校验事实**：每个 `EntityRef.name` **必须在 input.facts 或 input.usage_window.by_doctype.*.top 中出现**才能进新 profile —— summarizer prompt 里强制约束。
3. **recently_quiet 衰减**：
   - 上次在 profile、本期 usage_window 中 **没出现** → 保留条目，`status="recently_quiet"`，`quiet_runs += 1`，note 末尾追加"（本期暂无活动）"。
   - `quiet_runs >= recently_quiet_runs_to_drop`（默认 2）→ 真正下榜，不输出。
   - 一旦在 usage_window 中再次出现 → `status="active"`, `quiet_runs=0`。

### 5.7 Summarizer Prompt 模板

```text
# Role
You are the ERPNext tenant profile summarizer for a foreign-trade AI assistant.
Your output is injected into the main agent's system prompt. The main agent is a
business-domain assistant that helps users run order-to-cash, procure-to-pay, and
stock/account operations on ERPNext. Your job is to give the main agent a concise
"who is this tenant and how do they operate" briefing so it does NOT need to
re-query company / customer / supplier / item / warehouse on every conversation.

# ERPNext business model (must internalize)

ERPNext is a multi-company, multi-currency ERP. Key invariants you must respect:

- Master data (Customer, Supplier, Item, Warehouse, Account, Cost Center, Price List,
  Tax Template, Payment Terms, Item/Customer/Supplier Groups, Territory, Sales Person)
  is REFERENCE data — these go in the profile.
- Transactional documents (Sales Order, Purchase Order, Sales Invoice, Purchase Invoice,
  Delivery Note, Purchase Receipt, Payment Entry, Journal Entry, Material Request,
  Stock Entry, Quotation, etc.) are INSTANCES of business events. They DO NOT go in
  the profile. Their link-field references DO (because that's evidence of "in use").
- Three ledgers (Stock Ledger Entry, GL Entry, Payment Ledger Entry) are derived flux —
  never put them or any aggregate over them in the profile.
- Multi-company: Items/Customers/Suppliers are shared across companies, but Account,
  Warehouse, Cost Center, default_* settings are per-company. When listing
  operational defaults, group by company.
- Multi-currency: a company has default_currency, transactions have their own currency
  + conversion_rate; report observed currencies, not just the default.
- The "make_*" chain (e.g. make_sales_invoice from Sales Order) is the canonical
  cross-document flow. A link-field reference inside a make_* call is the strongest
  evidence that an entity is operationally in use.

# Decision rules

1. **Eligibility**: an EntityRef's `name` MUST appear either in `input.facts` or in
   `input.usage_window.by_doctype.*.top`. NEVER fabricate names. If unsure, put it
   in `open_questions` instead.

2. **Promotion**: prefer entities with `primary` weight count. Entities seen only
   under `browse` weight should NOT be promoted unless they appear ≥3 times.

3. **Continuity (previous_profile handling)**:
   - Keep narrative style, note phrasings, and open_questions wording from previous_profile.
   - For each entity in previous_profile.key_entities and taxonomy:
     - If it appears in current usage_window → status="active", quiet_runs=0,
       update note if new context emerged (e.g. "now also buying from them").
     - If absent in current window → keep it but set status="recently_quiet",
       quiet_runs = previous.quiet_runs + 1, append "（本期暂无活动）" or
       "(quiet this period)" to note. If quiet_runs >= 2, DROP it from output.
   - For new entities (not in previous_profile but in current window meeting
     promotion rules): add with status="active", quiet_runs=0.

4. **Notes**: write short (<80 chars), informative. Examples:
   - "主力客户，月度返单，主营 Korea OEM 出口"
   - "新增供应商，本季首次合作"
   - "默认成品仓"
   AVOID generic notes like "客户", "供应商" — if you can't say something specific, omit.

5. **Operational patterns**: infer from transactional events you SEE in usage_window:
   - If many SO directly produce SI without DN → primary_workflow mentions "skip DN".
   - currencies_in_use = distinct currencies seen across SO/PO/SI/PI events,
     not just facts.primary_company.default_currency.
   - default_warehouse_by_company = the warehouse most frequently seen in DN/PR
     for each company, NOT necessarily Stock Settings.default_warehouse.

6. **open_questions**: when usage_window shows a name you can't classify (appears
   in payload but doctype unclear, or candidate is a Project / Lead / Custom DocType),
   add an open_question. Use erpnext_get_list to enumerate candidates BEFORE asking.

7. **Tool budget**: at most 10 read-only tool calls. Prefer batching via get_list
   over per-name get_doc. If you can't resolve in budget, leave an open_question.

# Output

Return ONLY a valid JSON matching the TenantProfile schema. No prose outside JSON.
Do not invent fields not in the schema. Do not include any name not justified by
input data.
```

---

## 6. 注入层

### 6.1 时机

在 `backend/src/agents/lead_agent/prompt.py:apply_prompt_template()` 中，紧接 `_get_memory_context` 之前调用 `_get_profile_context(tenant_id)`。两段独立 system prompt section。

**首次会话冷启动**（profile.json 不存在）：

```
首会话进来 → _get_profile_context 检查 profile.json
  ├─ 存在 → 直接读，正常路径
  └─ 不存在 → 同步触发 facts bootstrap，硬超时 2s
       ├─ 2s 内完成 → 写一个 facts-only profile.json（frequent / taxonomy 段空）→ 注入
       │            → 后台启动 observation 接管 frequent 累积
       └─ 超时 → 注入空 section，本会话退化到现状（LLM 自己查 ERPNext）
                → 后台继续完成 bootstrap，下次会话即可命中
```

facts bootstrap 涉及约 8-12 次 ERPNext 调用（Company / Settings × 4 / Employee / User / Fiscal Year），局域网内 2s 通常足够；超时不阻塞用户。绝不让 profile feature 把首会话延迟拖到 2s 以上。

### 6.2 prompt 渲染格式

profile.json → 文本，按业务模块分组。**NestedSet 类 doctype（Account / Cost Center / Item Group / Customer Group / Supplier Group / Territory）按业务维度分桶渲染**，平铺写法不允许：

- `Account` 按 `root_type` 分桶（Asset / Liability / Income / Expense / Equity）
- `Cost Center` 按 `parent_cost_center` 分桶（直接父节点）
- `Item Group` / `Customer Group` / `Supplier Group` / `Territory` 按 `parent_*` 分桶

这是为 LLM 消费做的人体工程学优化：业务文档 §6.2 的双式记账规则与 root_type 直接对齐，分桶后 LLM 一眼对应到"DR 资产/费用、CR 负债/收入"。

**完整渲染模板**：

```markdown
## Tenant Profile

**Identity**: ACME Industrial Ltd (USD, China, FY2026 starts 2026-04-01)
**You are**: 张三 — Sales / Sales Manager / company=ACME-SH
**Operational mode**: Order-to-Cash via SO → DN → SI; primary FX USD/CNY.
**Tenant brief**: 上海工业阀门分销商，主营 OEM 出口；近期重点拓展东南亚市场。

### Frequent customers (active)
- FOOCORP-001 — Foo Industrial Korea (group: OEM-KR, terr: Korea) · 主力客户，月度返单
- BAR-002    — Bar Ltd Singapore   (group: OEM-SEA, terr: Singapore) · 新增主力，本季拓展

### Frequent suppliers (active)
- ...

### Frequent items
- VLV-001 — Industrial Valve 1" (group: Valves/Industrial, uom: Nos) · 主力 SKU
- ...

### Operational defaults (per company)
- ACME Industrial Ltd:
  - Default warehouse (observed): Stores - ACME
  - Common price lists: Standard Selling (USD), Wholesale CNY (CNY)
  - Sales tax templates: VAT 13% — China, Export-Zero
  - Payment terms commonly used: Net 30, 50% Advance + Net 30

### Frequent accounts (by root_type)
- Asset:
  - Bank - Cash CNY                (Bank)
  - Bank - HSBC USD                (Bank)
  - Trade Receivables - ACME       (Receivable)
- Liability:
  - Trade Payables - ACME          (Payable)
  - GST Payable - 13%              (Tax)
- Income:
  - Sales - Industrial Export
  - Sales - Domestic CN
- Expense:
  - COGS - Valves
  - Logistics Expense

### Frequent cost centers (by parent)
- Main - ACME:
  - Sales - ACME
  - Operations - ACME

### Taxonomy in use
- Item Groups (by parent):
  - Industrial: Valves, Pumps, Fittings
- Customer Groups (by parent):
  - OEM: OEM-KR, OEM-SEA
  - Distributor: Distributor-CN
- Territories: Korea, Singapore, China
- Sales persons: 张三, 李四, 王五

### Recently quiet (may need confirmation if user mentions)
- LEGACY-CUST-099 — Legacy Customer (no activity for 1 period)

### Open questions for the agent
- "Project Alpha" 多次出现但未在 Customer/Project/Custom doctype 中找到对应记录；下次用户提到时请确认实体类型。
```

`recently_quiet` 单独一段渲染但比 active 短，只列 name+display；`active` 才出 note。

### 6.3 token 预算

profile section 渲染上限 **2000 tokens**（占 system prompt 较大段，但比每轮多次工具调用便宜得多）。超限时按优先级裁剪：先裁 taxonomy → 再裁 operational defaults note → 再裁 frequent entities note → 最后裁条目数。

---

## 7. 配置

`config.yaml`：

```yaml
tenant_profile:
  enabled: true
  storage_path: backend/.deer-flow/tenant_profile  # 默认相对仓库根

  facts:
    bootstrap_ttl_seconds: 86400        # 24h 全量重拉
    primary_company_resolver: employee  # employee | global_default | first

  observation:
    log_max_size_bytes: 5242880          # 单文件 5MB 触发轮转
    skip_browse_events: true
    thread_dedupe: true
    failure_log_level: warning           # observer 失败时只打 warning（v1 不接 metric）

  archive:
    upload_to_oss: true
    bucket: trademind-chat-session       # 复用项目既有 OSS bucket（与 chat uploads / artifacts 同 bucket）
    key_template: "tenants/{tenant_id}/profile/usage_log/{date}-{sha8}.jsonl.gz"
    keep_local_after_upload: true        # 上传成功本地仍保留 archive 副本
    retry_on_failure: true               # 失败时进 meta.pending_oss_uploads，下次 summarize 触发前重试
    max_retry_attempts: 3                # 单个文件累计重试次数上限；超过仅本地保留 + warning

  trigger:
    event_count_threshold: 50
    primary_event_threshold: 10
    time_threshold_seconds: 86400
    cooldown_seconds: 1800

  summarizer:
    model:
      provider: null                     # null → fallback to main agent
      model: null
      max_tokens: 4096
      temperature: 0.2
    tool_call_budget: 10
    output_token_budget: 2000

  decay:
    recently_quiet_runs_to_drop: 2

  top_k:
    customer: 20
    supplier: 20
    item: 30
    warehouse: 10
    price_list: 5
    uom: 10
    item_group: 10
    customer_group: 10
    supplier_group: 10
    territory: 10
    sales_person: 10
    brand: 5
    account: 15
    cost_center: 10
    sales_tax_template: 5
    purchase_tax_template: 5
    payment_terms_template: 5
    pricing_rule: 5

  injection:
    max_tokens: 2000
    show_recently_quiet: true
    show_open_questions: true
```

---

## 8. 文件布局

新增模块挂在 `backend/src/agents/tenant_profile/`，与 `memory/` 平级：

```
backend/src/agents/tenant_profile/
├── __init__.py
├── facts.py              # bootstrap：拉 Company/Settings/Employee/User（含 2s 超时入口）
├── observer.py           # FrappeClient hook：抽 link_fields → usage_log.jsonl（warning 兜底）
├── extractors.py         # 各 op 的 link-field 抽取规则（per-doctype，见附录 A）
├── log.py                # jsonl 写入、轮转、读取
├── archive.py            # gzip + sha8 + OSS 上传 + pending 队列重试（复用 src/storage）
├── trigger.py            # 触发条件评估、cooldown 控制
├── queue.py              # 复用 memory/queue.py 模式的防抖队列
├── summarizer/
│   ├── __init__.py
│   ├── agent.py          # sub-agent 入口，包装 LLM 调用
│   ├── prompt.py         # §5.7 prompt 模板（含 NestedSet batch 与分桶约束）
│   ├── schema.py         # pydantic models (TenantProfile 等)
│   ├── tools.py          # ReadOnlyFrappeClient 包装出的 tool
│   └── runner.py         # 编排：load input → call LLM → validate → write
├── injection.py          # §6 渲染逻辑（含 NestedSet 分桶），被 prompt.py:_get_profile_context 调用
├── store.py              # profile.json / meta.json 读写，文件锁
└── config.py             # config.yaml 解析

backend/src/storage/keys.py
  └─ 新增 tenant_profile_usage_log_key(tenant_id, date, sha8) -> str

backend/src/agents/lead_agent/prompt.py
  └─ apply_prompt_template() 中加 _get_profile_context(tenant_id) 调用

skills/public/erpnext-cli/scripts/erpnext_pkg/core/client.py
  └─ FrappeClient 各方法收口处加 observer hook 调用（轻量、失败不影响主流程）

backend/src/gateway/routers/tenant_profile.py  # 新路由
  GET    /api/tenant_profile             # 读
  POST   /api/tenant_profile/refresh     # 显式触发（force option）
  PUT    /api/tenant_profile/notes       # 用户/管理员手工补 note（v2 可选）

config.yaml
  └─ tenant_profile: ...                 # §7 块
```

---

## 9. 失败模式 & fallback

| 失败 | 行为 |
|---|---|
| Observer hook 抛异常 | warning log（按 §7 `failure_log_level`），不影响主调用链；事件丢弃 |
| usage_log.jsonl 写不进（磁盘满） | warning；profile 走 stale 路径 |
| OSS 上传失败 | 本地 archive 副本已落盘 → 加入 meta.pending_oss_uploads；下次 summarize 触发时先重试该队列；累计 ≥3 次失败仅 warning，本地继续保留 |
| OSS 配置缺失 / AK 不可用 | best-effort 跳过上传（沿用 storage 模块约定），本地 archive 仍保留；不打 ALARM，仅启动时一次性 info log |
| Summarizer LLM 调用失败 | 重试 1 次后放弃；保留旧 profile；meta 记错误 |
| Summarizer 输出 JSON 解析失败 | 保留旧 profile；meta 记 schema 错误；trigger 进 cooldown |
| Summarizer 输出包含 facts/usage 中没有的 name | pydantic validator 拒绝；保留旧 profile |
| 工具调用超预算 | runner 强制截断；剩余作为 open_questions |
| Profile 文件不存在（首次会话） | 见 §6.1：同步 facts bootstrap 2s 超时，命中即注入 facts-only 段，超时退化空段 |
| facts bootstrap 超时 | 注入空段，后台继续；本会话 LLM 自己查 ERPNext（兼容现状） |
| Profile schema 升级（v2 → v3） | 旧 profile 视为 invalid，触发重新生成；不做数据迁移 |
| 多线程并发触发 summarizer | queue.py 同租户合并，至多一个 in-flight |

**绝不阻塞用户请求**：注入层永远 best-effort 读现有 profile.json，不存在/不可读就空段，不等任何后台任务。

---

## 10. v2 留口

- Manufacturing / Subcontracting / CRM / Projects / Assets 模块的 entity 白名单（结构已对齐 §3.2，只需扩字段表）
- 用户/管理员手工编辑 profile（PUT API + 标记 `user_curated=true` 永不衰减）
- 多语言 profile（按 `User.language` 渲染中/英）
- LLM 凝练的 1-3 句 "tenant brief" 作为 summary 字段的更精炼版本（v1 已含 summary，v2 提升）
- ERPNext webhook → 实时失效：`hooks.py:doc_events` 上挂 doctype-level 触发，对 Customer/Supplier/Item 等的 update/delete 立即标记需要重新总结
- discovery flag：skill payload 中 `--purpose` 字段从观察层过滤
- 跨租户共性提取（同一行业的 tenant 模板 profile）

---

## 11. v1 决议（已与负责人定）

| # | 议题 | 决议 | 实现要点 |
|---|---|---|---|
| 1 | observer 失败策略 | warning 日志，v1 不接 metric / 不打扰用户 | `try/except` 兜底 + `logging.warning`，不抛 raise；`config.tenant_profile.observation.failure_log_level=warning` 控制 |
| 2 | 归档存储 | 上传到阿里云 OSS（bucket=`trademind-chat-session`），保留策略交给 OSS lifecycle rule | 复用 `src/storage/OssClient`，AK 走 `ALIYUN_OSS_ACCESS_KEY_ID/SECRET` env；新增 `keys.tenant_profile_usage_log_key()`；上传失败本地保留 + pending 重试，本地副本上传后**也保留**（不删） |
| 3 | NestedSet doctype（Account/CC/Item Group/Customer Group/Supplier Group/Territory）的扇出与可读性 | **B+C**：批量 `get_list` 拉字段 + 按业务维度分桶渲染（Account 按 root_type；其余按 parent_*） | summarizer prompt 强制声明该模式；injection.py 渲染按桶输出；工具预算上限不变（10），其中 6 个 NestedSet 各占 1 次 batch get_list |
| 4 | 首会话冷启动 | 同步 facts bootstrap，硬超时 2s；超时退化空段 | `_get_profile_context()` 内置 timeout，超时 fallback 不抛错；后台继续完成 bootstrap |
| 5 | 集成测试策略 | 分层单测 + 自建 ~150 行 mock 跑 1-2 个 e2e；不引入 trademind-harness 的 MockFrappe 跨仓依赖 | observer / log / trigger / summarizer / injection 各自单测；e2e 用 `tests/fixtures/mock_frappe.py` 串"50 事件 → 触发 → summarize → profile 写入"和"recently_quiet 衰减"两个场景；LLM 调用以 `pytest-mock` 替换 |

每条决议在文档对应章节都已落地：1 → §7 配置、§10；2 → §4.5、§7、§10；3 → §3.2、§5.3、§6.2；4 → §6.1、§10；5 → 实施阶段在 `tests/` 落地，本文档不再展开。

## 12. 实施分解（仅供参考，待启动后细化）

按依赖关系分 4 个 milestone，每个独立可测：

1. **M1 — 观察层**：`extractors.py` 字段映射表（附录 A 全实现）+ `observer.py` hook 挂在 FrappeClient + `log.py` jsonl 读写 + 单测。可观察实际事件流，但不产 profile。
2. **M2 — 归档与 OSS**：`keys.tenant_profile_usage_log_key()` + `archive.py`（gzip + sha8 + 上传 + pending 队列）+ 单测。M1 的 jsonl 在 summarize 钩子之前先归档（即使 summarize 还没实现）。
3. **M3 — Summarizer + 触发**：`facts.py` bootstrap + `trigger.py` 阈值 + `queue.py` 防抖 + `summarizer/`（prompt / schema / runner / read-only tool）+ pydantic 校验。e2e 1：50 事件触发完整产 profile。
4. **M4 — 注入与冷启动**：`injection.py` 渲染（含 NestedSet 分桶）+ `_get_profile_context()` 接入 `apply_prompt_template` + 2s 超时同步 bootstrap + e2e 2：recently_quiet 衰减。

文档维护：M1-M4 任一环节出现与本文档不一致的实现，先改本文档再改代码。

---

## 附录 A：信号源映射表（observer 抽取规则示例）

按业务文档 §4 的 doctype 字段表整理 —— 完整表在 `extractors.py`，以下为关键样本：

| Doctype.field | extracted_ref |
|---|---|
| `Sales Order.customer` | `(Customer, value, "primary")` |
| `Sales Order.items[].item_code` | `(Item, value, "primary")` |
| `Sales Order.items[].warehouse` | `(Warehouse, value, "primary")` |
| `Sales Order.items[].cost_center` | `(Cost Center, value, "primary")` |
| `Sales Order.selling_price_list` | `(Price List, value, "primary")` |
| `Sales Order.taxes_and_charges` | `(Sales Taxes and Charges Template, value, "primary")` |
| `Sales Order.payment_terms_template` | `(Payment Terms Template, value, "primary")` |
| `Sales Order.sales_team[].sales_person` | `(Sales Person, value, "primary")` |
| `Purchase Order.supplier` | `(Supplier, value, "primary")` |
| `Purchase Order.buying_price_list` | `(Price List, value, "primary")` |
| `Delivery Note.customer / items[].warehouse / items[].against_sales_order` | Customer / Warehouse / —（against_sales_order 是 transactional 引用，跳过） |
| `Purchase Receipt.supplier / items[].warehouse / items[].purchase_order` | Supplier / Warehouse / — |
| `Sales Invoice.customer / debit_to / +taxes[].account_head` | Customer / Account / Account |
| `Purchase Invoice.supplier / credit_to / +taxes[].account_head` | Supplier / Account / Account |
| `Payment Entry.party_type+party / paid_from / paid_to` | Customer\|Supplier / Account / Account |
| `Stock Entry.from_warehouse / to_warehouse / items[].item_code` | Warehouse / Warehouse / Item |
| `Item.item_defaults[].default_warehouse` | (per-company default) Warehouse — bootstrap only |

`extractors.py` 实现：每个 doctype 一个 `extract_refs(payload: dict) -> list[Ref]`，集中维护 field → doctype 的映射表，新加 doctype 只要补一行。

---

> 文档维护：实施开始前由 reviewer 在 §11 上签字定开放问题；实施过程中字段增删先改本文档再改代码。

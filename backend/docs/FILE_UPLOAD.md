# 文件上传功能

## 概述

DeerFlow 后端提供了完整的文件上传功能，支持多文件上传。对于 PDF 和 Office 文档（Word / Excel /
PowerPoint），会通过 [`docling`](https://github.com/docling-project/docling) 抽取**结构化的
DoclingDocument JSON**（保留标题层级、表格的行/列跨度、公式、嵌入图片、PPT 页面 bbox 等），
并落盘一个轻量摘要 sidecar，供 Agent 在读取原始文件之前先看清楚结构。

## 功能特性

- ✅ 支持多文件同时上传
- ✅ 自动抽取结构化 JSON（PDF、PPT、Excel、Word）— 替换历史上的 `markitdown → markdown` 流水线
- ✅ 同时落盘 `*.docling.summary.json` 轻量摘要，方便 Agent 选策略
- ✅ 文件存储在线程隔离的目录中
- ✅ Agent 自动感知已上传的文件
- ✅ 支持文件列表查询和删除

## API 端点

### 1. 上传文件
```
POST /api/threads/{thread_id}/uploads
```

**请求体：** `multipart/form-data`
- `files`: 一个或多个文件

**响应：**
```json
{
  "success": true,
  "files": [
    {
      "filename": "document.pdf",
      "size": 1234567,
      "path": ".deer-flow/threads/{thread_id}/user-data/uploads/document.pdf",
      "virtual_path": "/mnt/user-data/uploads/document.pdf",
      "artifact_url": "/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/document.pdf",
      "docling_json_file": "document.docling.json",
      "docling_json_path": ".deer-flow/threads/{thread_id}/user-data/uploads/document.docling.json",
      "docling_json_virtual_path": "/mnt/user-data/uploads/document.docling.json",
      "docling_json_artifact_url": "/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/document.docling.json",
      "docling_summary_file": "document.docling.summary.json",
      "docling_summary_virtual_path": "/mnt/user-data/uploads/document.docling.summary.json",
      "docling_summary": {
        "format": "pdf",
        "size_bytes": 1234567,
        "page_count": 12,
        "table_count": 3,
        "picture_count": 5,
        "sections": [{"level": 1, "text": "Introduction"}, {"level": 2, "text": "Methods"}]
      }
    }
  ],
  "message": "Successfully uploaded 1 file(s)"
}
```

**路径说明：**
- `path`: 实际文件系统路径（相对于 `backend/` 目录）
- `virtual_path`: Agent 在沙箱中使用的虚拟路径
- `artifact_url`: 前端通过 HTTP 访问文件的 URL

### 2. 列出已上传文件
```
GET /api/threads/{thread_id}/uploads/list
```

**响应：**
```json
{
  "files": [
    {
      "filename": "document.pdf",
      "size": 1234567,
      "path": ".deer-flow/threads/{thread_id}/user-data/uploads/document.pdf",
      "virtual_path": "/mnt/user-data/uploads/document.pdf",
      "artifact_url": "/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/document.pdf",
      "extension": ".pdf",
      "modified": 1705997600.0
    }
  ],
  "count": 1
}
```

### 3. 删除文件
```
DELETE /api/threads/{thread_id}/uploads/{filename}
```

**响应：**
```json
{
  "success": true,
  "message": "Deleted document.pdf"
}
```

## 支持的文档格式

以下格式会通过 docling 抽取为结构化 JSON：
- PDF (`.pdf`)
- PowerPoint (`.ppt`, `.pptx`)
- Excel (`.xls`, `.xlsx`)
- Word (`.doc`, `.docx`)

每个原文件会附带两个 sidecar：
- `<stem>.docling.json` — 完整的 DoclingDocument JSON（顶层包含 `body` / `texts` / `tables` /
  `pictures` / `pages` / `groups`，每个元素含 `prov`、表格 cell 含 row/col span）
- `<stem>.docling.summary.json` — 轻量摘要（`format`、`size_bytes`、`page_count`、
  `table_count`、`picture_count`，加上 docx/pdf 的 `sections`、xlsx 的 `sheets`、pptx 的
  `slide_count`），UploadsMiddleware 直接读这个文件构造 `<uploaded_files>` 提示

## Agent 集成

### 自动文件列举

Agent 在每次请求时会自动收到已上传文件的列表，格式如下：

```xml
<uploaded_files>
The following files were uploaded in this message:

- document.pdf (1.2 MB) [pdf, 12 pages, 3 tables, 5 pictures, 2 sections]
  Path: /mnt/user-data/uploads/document.pdf
  Structured: /mnt/user-data/uploads/document.docling.json
  Sections: Introduction | Methods

Use `read_file` on the path above for plain reads. For PDF / Office files a
structured `*.docling.json` sibling is available with full DoclingDocument JSON
(headings, tables with row/col spans, formulas, embedded pictures, page bboxes).
For very large spreadsheets prefer DuckDB `read_xlsx(path, sheet=, range=)` or
python-calamine over `pd.read_excel`. For huge .docx / .pptx, stream via
`zipfile` + `lxml.etree.iterparse` rather than loading into context.
</uploaded_files>
```

### 使用上传的文件

Agent 在沙箱中运行，使用虚拟路径访问文件：

```python
# 读取原始文件（按需）
read_file(path="/mnt/user-data/uploads/document.pdf")

# 读取结构化 JSON（含表格 row/col span、heading level、formula LaTeX、picture annotations）
read_file(path="/mnt/user-data/uploads/document.docling.json")

# 大文件：写 Python，不要把全文塞进 context
import duckdb
duckdb.sql("SELECT * FROM read_xlsx('/mnt/user-data/uploads/big.xlsx', sheet='Sales', range='A1:D1000')")

# 也可以用 calamine 加速 pandas
import pandas as pd
df = pd.read_excel('/mnt/user-data/uploads/big.xlsx', engine='calamine')
```

**路径映射关系：**
- Agent 使用：`/mnt/user-data/uploads/document.pdf`（虚拟路径）
- 实际存储：`backend/.deer-flow/threads/{thread_id}/user-data/uploads/document.pdf`
- 前端访问：`/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/document.pdf`（HTTP URL）

上传流程采用“线程目录优先”策略：
- 先写入 `backend/.deer-flow/threads/{thread_id}/user-data/uploads/` 作为权威存储
- 本地沙箱（`sandbox_id=local`）直接使用线程目录内容
- 非本地沙箱会额外同步到 `/mnt/user-data/uploads/*`，确保运行时可见

## 测试示例

### 使用 curl 测试

```bash
# 1. 上传单个文件
curl -X POST http://localhost:2026/api/threads/test-thread/uploads \
  -F "files=@/path/to/document.pdf"

# 2. 上传多个文件
curl -X POST http://localhost:2026/api/threads/test-thread/uploads \
  -F "files=@/path/to/document.pdf" \
  -F "files=@/path/to/presentation.pptx" \
  -F "files=@/path/to/spreadsheet.xlsx"

# 3. 列出已上传文件
curl http://localhost:2026/api/threads/test-thread/uploads/list

# 4. 删除文件
curl -X DELETE http://localhost:2026/api/threads/test-thread/uploads/document.pdf
```

### 使用 Python 测试

```python
import requests

thread_id = "test-thread"
base_url = "http://localhost:2026"

# 上传文件
files = [
    ("files", open("document.pdf", "rb")),
    ("files", open("presentation.pptx", "rb")),
]
response = requests.post(
    f"{base_url}/api/threads/{thread_id}/uploads",
    files=files
)
print(response.json())

# 列出文件
response = requests.get(f"{base_url}/api/threads/{thread_id}/uploads/list")
print(response.json())

# 删除文件
response = requests.delete(
    f"{base_url}/api/threads/{thread_id}/uploads/document.pdf"
)
print(response.json())
```

## 文件存储结构

```
backend/.deer-flow/threads/
└── {thread_id}/
    └── user-data/
        └── uploads/
            ├── document.pdf                           # 原始文件
            ├── document.docling.json                  # 结构化 DoclingDocument JSON
            ├── document.docling.summary.json          # 轻量摘要（页/表/图计数 + 章节标题）
            ├── presentation.pptx
            ├── presentation.docling.json
            ├── presentation.docling.summary.json
            └── ...
```

## 限制

- 最大文件大小：100MB（可在 nginx.conf 中配置 `client_max_body_size`）
- 文件名安全性：系统会自动验证文件路径，防止目录遍历攻击
- 线程隔离：每个线程的上传文件相互隔离，无法跨线程访问

## 技术实现

### 组件

1. **Upload Router** (`src/gateway/routers/uploads.py`)
   - 处理文件上传、列表、删除请求
   - 通过 `src/utils/document_extract.py` 调用 docling 抽取结构化 JSON
   - 同步落盘 `*.docling.json` 与 `*.docling.summary.json`，并按需镜像到 OSS

2. **Document Extract Utility** (`src/utils/document_extract.py`)
   - `extract_with_docling(path)` — 异步包装，把 `DocumentConverter` 跑在 thread pool
   - `build_summary(doc, path)` — 仅聚合计数 + 顶层结构标识（含章节标题、sheet 名/行列数、slide 数）
   - `format_summary_inline(summary)` — 一行结构概览，给 prompt 注入用

3. **Uploads Middleware** (`src/agents/middlewares/uploads_middleware.py`)
   - 在每次 Agent 请求前注入文件列表
   - 读取 `*.docling.summary.json` 在 `<uploaded_files>` 块里附结构概览
   - 自动跳过 docling sidecar，避免 Agent 把派生文件当成用户上传

4. **Nginx 配置** (`nginx.conf`)
   - 路由上传请求到 Gateway API
   - 配置大文件上传支持

### 依赖

- `docling>=2.0.0` - PDF / Office 结构化抽取（替代 markitdown）
- `python-calamine>=0.2.3` - Rust mmap xlsx 读取，agent 处理大表用
- `python-docx>=1.1.0` - 与 `python-pptx`、`openpyxl` 一道，给 agent 在沙箱里写 Python 操作 Office 用
- `duckdb>=1.4.4` - `read_xlsx` 扩展，xlsx-as-SQL（已有）
- `python-multipart>=0.0.20` - 文件上传处理

## 故障排查

### 文件上传失败

1. 检查文件大小是否超过限制
2. 检查 Gateway API 是否正常运行
3. 检查磁盘空间是否充足
4. 查看 Gateway 日志：`make gateway`

### 文档抽取失败

1. 检查 docling 是否正确安装：`uv run python -c "import docling"`（首次运行会下载模型，可能耗时）
2. 查看日志中的具体错误信息
3. 某些损坏或加密的文档可能无法抽取，但原文件仍会保存（最终响应里 `docling_*` 字段会缺失）

### Agent 看不到上传的文件

1. 确认 UploadsMiddleware 已在 agent.py 中注册
2. 检查 thread_id 是否正确
3. 确认文件确实已上传到 `backend/.deer-flow/threads/{thread_id}/user-data/uploads/`
4. 非本地沙箱场景下，确认上传接口没有报错（需要成功完成 sandbox 同步）

## 开发建议

### 前端集成

```typescript
// 上传文件示例
async function uploadFiles(threadId: string, files: File[]) {
  const formData = new FormData();
  files.forEach(file => {
    formData.append('files', file);
  });

  const response = await fetch(
    `/api/threads/${threadId}/uploads`,
    {
      method: 'POST',
      body: formData,
    }
  );

  return response.json();
}

// 列出文件
async function listFiles(threadId: string) {
  const response = await fetch(
    `/api/threads/${threadId}/uploads/list`
  );
  return response.json();
}
```

### 扩展功能建议

1. **文件预览**：添加预览端点，支持在浏览器中直接查看文件
2. **批量删除**：支持一次删除多个文件
3. **文件搜索**：支持按文件名或类型搜索
4. **版本控制**：保留文件的多个版本
5. **压缩包支持**：自动解压 zip 文件
6. **图片 OCR**：对上传的图片进行 OCR 识别

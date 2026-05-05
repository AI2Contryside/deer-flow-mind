"""Inject ``<selected_template>`` context as a SystemMessage.

The gateway populates ``state.selected_template`` (a dict carrying
``template_id``, ``name``, ``type``, ``jinja_download_url``, ``fields``)
when ``chat_stream`` sees ``selected_template`` in the request body. The
``fill_template`` builtin tool reads it at call time, but the *lead
agent itself* only ever sees the user's text — without an injected
context block, the model has no idea "这个模板" refers to anything
specific and asks the user "which template?" on every turn.

This middleware closes that gap by appending a SystemMessage carrying a
``<selected_template>`` block to the message stream. The LangGraph
``add_messages`` reducer dedupes by message id, so re-emitting on every
turn with the same fixed id is a no-op (the existing copy stays). The
FE's chat stream handler (``api.ts::chatStream``) only renders
``type=ai`` and ``type=tool`` partials, so SystemMessage content stays
out of the user-visible bubble — exactly what we want for prompt
plumbing.

Why not modify the user's HumanMessage? Mutating ``HumanMessage.content``
to prepend the block also leaks it to the FE: the user-message renderer
shows whatever sits in ``content`` verbatim. SystemMessage is the
correct shape for "context the LLM sees, the user shouldn't."
"""

from __future__ import annotations

import logging
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

# Stable id so add_messages dedupes the SystemMessage across turns. If a
# user switches templates mid-thread, the dict content changes but the id
# stays — add_messages then *replaces* the prior copy with the new one,
# matching what we want (one live block reflecting the current state).
_SYSTEM_MESSAGE_ID = "__selected_template_block__"

_BLOCK_OPEN = "<selected_template>"
_BLOCK_CLOSE = "</selected_template>"


class SelectedTemplateMiddlewareState(AgentState):
    """Subset of ThreadState the middleware reads."""

    selected_template: NotRequired[dict | None]


class SelectedTemplateMiddleware(AgentMiddleware[SelectedTemplateMiddlewareState]):
    """Inject ``<selected_template>`` SystemMessage before each lead-agent turn."""

    state_schema = SelectedTemplateMiddlewareState

    @override
    def before_agent(self, state: SelectedTemplateMiddlewareState, runtime: Runtime) -> dict | None:
        selected = state.get("selected_template")
        if not selected or not isinstance(selected, dict):
            return None

        block = self._render_block(selected)
        message = SystemMessage(content=block, id=_SYSTEM_MESSAGE_ID)

        logger.debug(
            "selected_template injected: template_id=%s fields=%d",
            selected.get("template_id"),
            len(selected.get("fields") or []),
        )
        return {"messages": [message]}

    @staticmethod
    def _render_block(selected: dict) -> str:
        template_id = str(selected.get("template_id") or "(unknown)")
        name = str(selected.get("name") or "(unnamed)")
        ftype = str(selected.get("type") or "")
        fields = selected.get("fields") or []
        if not isinstance(fields, list):
            fields = []

        lines: list[str] = [_BLOCK_OPEN]
        lines.append("用户已经在前端选中了一个模板,你需要基于这个模板生成最终文件。")
        lines.append("")
        lines.append(f"- 模板名: {name}")
        lines.append(f"- 模板 ID: {template_id}")
        if ftype:
            lines.append(f"- 文件类型: {ftype}")
        lines.append("")

        if fields:
            lines.append("**模板字段**(用户提供数据时,你的 `data` 字典 key 必须与下方 `name` 完全一致):")
            for raw in fields:
                if not isinstance(raw, dict):
                    continue
                fname = str(raw.get("name") or "")
                flabel = str(raw.get("label") or fname)
                ftype_field = str(raw.get("type") or "string")
                req = "必填" if raw.get("required", True) else "可选"
                desc = str(raw.get("description") or "").strip()
                line = f"  - `{fname}` ({flabel}, {ftype_field}, {req})"
                if desc:
                    line += f" — {desc}"
                lines.append(line)
        else:
            lines.append("⚠️ 该模板尚未抽取到任何字段(fields 为空)。")
            lines.append("如果用户给的数据明显与文件结构匹配,可以把数据按合理的 key 直接传给 `fill_template` 让它尝试渲染;否则先用 `ask_clarification` 问用户具体要填哪些字段。")

        lines.append("")
        lines.append("**使用方式**:")
        lines.append("1. 如果用户已提供完整字段数据,**直接调用 `fill_template`** 生成成品文件,不要再问『用户选了哪个模板』或『要生成什么』——模板和类型已确定。")
        lines.append("2. 缺哪些字段就具体问哪些(例:『还需要 工号 和 部门』),不要笼统地问『还需要什么数据』。")
        lines.append("3. 单条 vs 多条记录:")
        if ftype == "excel":
            lines.append(
                "   - **xlsx + 多条记录**(如多个员工、多条订单行):**一次调用** "
                "`fill_template(output_name='...xlsx', data_list=[{...}, {...}, ...])`,"
                "工具会把每个 dict 展开为表格中的一行,产出**单个**含 N 行的 xlsx 文件。"
                "**绝不要**为每行单独调一次,那会生成多个文件。"
            )
            lines.append("   - **xlsx + 单条记录**:用 `fill_template(output_name='...xlsx', data={...})`。")
        else:
            lines.append("   - **docx**:每条记录调一次 `fill_template(output_name='...docx', data={...})`,生成对应数量的 docx 文件(docx 不支持表格行循环)。")
            lines.append("   - **xlsx**(如果未来切换模板):多条记录用 `data_list=[...]` 一次性渲染成单文件。")
        lines.append("4. **不要**自己用 docx/xlsx 库去拼,**不要**用 `present_files`,整条链路只走 `fill_template`。")
        lines.append(_BLOCK_CLOSE)
        return "\n".join(lines)

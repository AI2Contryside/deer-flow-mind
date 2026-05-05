"""Inject ``<selected_template>`` context into the last HumanMessage.

The gateway populates ``state.selected_template`` (a dict carrying
``template_id``, ``name``, ``type``, ``jinja_download_url``, ``fields``)
when ``chat_stream`` sees ``selected_template`` in the request body. The
``fill_template`` builtin tool reads it at call time, but the *lead
agent itself* only ever sees the user's text — without an injected
context block, the model has no idea "这个模板" refers to anything
specific and asks the user "which template?" on every turn.

This middleware closes that gap with the same shape as
``UploadsMiddleware``: on ``before_agent``, look at state, render a
prompt block, prepend it to the latest HumanMessage. Idempotent — if
the block is already present (mid-turn retry / resume), we skip
re-prepending so the message text stays clean.
"""

from __future__ import annotations

import logging
from typing import Any, NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

_BLOCK_OPEN = "<selected_template>"
_BLOCK_CLOSE = "</selected_template>"


class SelectedTemplateMiddlewareState(AgentState):
    """Subset of ThreadState the middleware reads."""

    selected_template: NotRequired[dict | None]


class SelectedTemplateMiddleware(AgentMiddleware[SelectedTemplateMiddlewareState]):
    """Inject ``<selected_template>`` block before each lead-agent turn."""

    state_schema = SelectedTemplateMiddlewareState

    @override
    def before_agent(
        self, state: SelectedTemplateMiddlewareState, runtime: Runtime
    ) -> dict | None:
        selected = state.get("selected_template")
        if not selected or not isinstance(selected, dict):
            return None

        messages = list(state.get("messages", []))
        if not messages:
            return None

        last_idx = self._find_last_human_message_index(messages)
        if last_idx is None:
            return None

        last = messages[last_idx]
        original_content = self._extract_text(last.content)

        # Idempotent: don't re-prepend on retry / resume.
        if _BLOCK_OPEN in original_content:
            return None

        block = self._render_block(selected)
        updated = HumanMessage(
            content=f"{block}\n\n{original_content}",
            id=last.id,
            additional_kwargs=last.additional_kwargs,
        )
        messages[last_idx] = updated
        logger.debug(
            "selected_template injected: template_id=%s fields=%d",
            selected.get("template_id"),
            len(selected.get("fields") or []),
        )
        return {"messages": messages}

    @staticmethod
    def _find_last_human_message_index(messages: list[Any]) -> int | None:
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], HumanMessage):
                return i
        return None

    @staticmethod
    def _extract_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
            return "\n".join(parts)
        return ""

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
            lines.append(
                "如果用户给的数据明显与文件结构匹配,可以把数据按合理的 key 直接传给 "
                "`fill_template` 让它尝试渲染;否则先用 `ask_clarification` 问用户具体要填哪些字段。"
            )

        lines.append("")
        lines.append("**使用方式**:")
        lines.append(
            "1. 如果用户已提供完整字段数据,**直接调用 `fill_template(data={...})`** 生成成品文件,"
            "不要再问『用户选了哪个模板』或『要生成什么』——模板和类型已确定。"
        )
        lines.append(
            "2. 缺哪些字段就具体问哪些(例:『还需要 工号 和 部门』),不要笼统地问『还需要什么数据』。"
        )
        lines.append(
            "3. 数据是多行(如多个员工)时,**为每一行调用一次 `fill_template`**,生成多个文件;"
            "或在工具支持时一次传 `data_list`。**不要**自己用 docx/xlsx 库去拼,**不要**用 `present_files`,"
            "整条链路只走 `fill_template`。"
        )
        lines.append(_BLOCK_CLOSE)
        return "\n".join(lines)

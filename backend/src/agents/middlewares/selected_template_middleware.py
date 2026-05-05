"""Inject ``<selected_template>`` context as a SystemMessage.

The gateway populates ``state.selected_template`` (a dict carrying
``template_id``, ``name``, ``type``, ``jinja_download_url``, ``fields``)
when ``chat_stream`` sees ``selected_template`` in the request body. The
``fill_template`` builtin tool reads it at call time, but the *lead
agent itself* only ever sees the user's text — without an injected
context block, the model has no idea "这个模板" refers to anything
specific and asks the user "which template?" on every turn.

**Block content is intentionally minimal.** It lists the template name,
id, type, and field schema — facts only. The "when to call",
"data vs data_list", and "don't roll your own xlsx" guidance lives in
``fill_template``'s tool docstring, which the LLM reads as part of the
tool's spec. Putting decision rules in prompt text makes them brittle
(any prompt edit risks losing the contract); putting them in the tool
description ties them to the code that actually enforces the contract.

LangGraph ``add_messages`` reducer dedupes by message id, so re-emitting
on every turn with the same fixed id is a no-op (the existing copy
stays). The FE's chat stream handler (``api.ts::chatStream``) only
renders ``type=ai`` and ``type=tool`` partials, so SystemMessage content
stays out of the user-visible bubble.
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
    def before_agent(
        self, state: SelectedTemplateMiddlewareState, runtime: Runtime
    ) -> dict | None:
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
        """Render the facts-only block.

        We deliberately do NOT include "how to call fill_template" guidance
        here. That contract belongs in the tool's docstring (see
        ``fill_template_tool``), where it ships with the implementation
        and survives prompt rewrites. The block exists so the model knows
        a template is in play and what its fields are; everything else
        is the tool's responsibility.
        """
        template_id = str(selected.get("template_id") or "(unknown)")
        name = str(selected.get("name") or "(unnamed)")
        ftype = str(selected.get("type") or "")
        fields = selected.get("fields") or []
        if not isinstance(fields, list):
            fields = []

        lines: list[str] = [_BLOCK_OPEN]
        lines.append("用户已选中模板,可调用 `fill_template` 工具生成成品文件(详见该工具说明)。")
        lines.append("")
        lines.append(f"- 模板名: {name}")
        lines.append(f"- 模板 ID: {template_id}")
        if ftype:
            lines.append(f"- 文件类型: {ftype}")
        lines.append("")

        if fields:
            lines.append("**模板字段**:")
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
            lines.append("⚠️ 该模板尚未抽取到任何字段(fields 为空),需先与用户确认要填的字段。")

        lines.append(_BLOCK_CLOSE)
        return "\n".join(lines)

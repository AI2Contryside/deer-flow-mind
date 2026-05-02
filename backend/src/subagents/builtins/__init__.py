"""Built-in subagent configurations."""

from .bash_agent import BASH_AGENT_CONFIG
from .general_purpose import GENERAL_PURPOSE_CONFIG
from .vision_analyst import VISION_ANALYST_CONFIG

__all__ = [
    "BASH_AGENT_CONFIG",
    "GENERAL_PURPOSE_CONFIG",
    "VISION_ANALYST_CONFIG",
]

# Registry of built-in subagents
#
# Note: ``tenant-onboarding`` and ``ocr-extractor`` both used to live
# here.
#
# - ``tenant-onboarding`` was removed because onboarding is
#   fundamentally a human-in-the-loop Q&A flow, and the subagent
#   abstraction has no clean way to surface ``ask_clarification``
#   interrupts back through the lead agent. The lead agent now runs
#   the onboarding spec inline (see
#   ``lead_agent/prompt.py::_get_onboarding_section``).
#
# - ``ocr-extractor`` was removed in favour of the
#   ``extract_trade_document`` builtin tool. qwen-vl-ocr-latest is a
#   single-turn checkpoint that rejects system messages, multi-turn
#   history, and tool-call sequences — incompatible with the
#   ``create_agent`` flow a subagent uses. Calling it through a tool
#   gives full control over the messages payload (one HumanMessage
#   with [image_url, text]) AND collapses what was a 3-5 LLM-call
#   subagent loop into a single LLM call. Free-form image Q&A still
#   uses a subagent (``vision-analyst`` on qwen-vl-plus) because
#   those workloads benefit from the multi-turn view_image agent
#   loop. See ``src/tools/builtins/extract_trade_document_tool.py``.
BUILTIN_SUBAGENTS = {
    "general-purpose": GENERAL_PURPOSE_CONFIG,
    "bash": BASH_AGENT_CONFIG,
    "vision-analyst": VISION_ANALYST_CONFIG,
}

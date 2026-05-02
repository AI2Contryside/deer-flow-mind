"""Built-in subagent configurations."""

from .bash_agent import BASH_AGENT_CONFIG
from .general_purpose import GENERAL_PURPOSE_CONFIG
from .ocr_extractor import OCR_EXTRACTOR_CONFIG
from .vision_analyst import VISION_ANALYST_CONFIG

__all__ = [
    "BASH_AGENT_CONFIG",
    "GENERAL_PURPOSE_CONFIG",
    "OCR_EXTRACTOR_CONFIG",
    "VISION_ANALYST_CONFIG",
]

# Registry of built-in subagents
#
# Note: ``tenant-onboarding`` used to live here. It was removed because
# onboarding is fundamentally a human-in-the-loop Q&A flow, and the
# subagent abstraction has no clean way to surface ``ask_clarification``
# interrupts back through the lead agent. The lead agent now runs the
# onboarding spec inline (see ``lead_agent/prompt.py::_get_onboarding_section``).
BUILTIN_SUBAGENTS = {
    "general-purpose": GENERAL_PURPOSE_CONFIG,
    "bash": BASH_AGENT_CONFIG,
    "vision-analyst": VISION_ANALYST_CONFIG,
    "ocr-extractor": OCR_EXTRACTOR_CONFIG,
}

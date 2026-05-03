"""Configuration for proactive next-step suggestion behavior.

Drives ``NextStepStateMiddleware`` and the ``<next_step_policy>`` /
``<next_step_catalog>`` blocks in the lead-agent system prompt. The
middleware computes per-conversation budget / cooldown state from message
history, the prompt blocks instruct the model when (and what) to
suggest, and a ``<next_step>`` hidden tag in the model's reply is
stripped + rendered as an action card by the frontend.

When ``enabled`` is false the middleware short-circuits and the prompt
blocks are not injected — zero overhead, no behavioural drift from the
pre-feature baseline.
"""

from pydantic import BaseModel, Field

DEFAULT_REJECT_KEYWORDS: list[str] = [
    "不用",
    "不要了",
    "不需要",
    "先不",
    "暂时不",
    "算了",
    "跳过",
    "没必要",
    "不必",
    "先这样",
    "先到这",
    "下次再说",
    "稍后再说",
    "回头再说",
    "no",
    "skip",
    "later",
]

DEFAULT_ACCEPT_KEYWORDS: list[str] = [
    "好的",
    "好啊",
    "可以",
    "继续",
    "走吧",
    "开始吧",
    "没问题",
    "行",
    "嗯",
    "好",
    "ok",
    "yes",
    "go",
    "do it",
]


class NextStepConfig(BaseModel):
    """Knobs for the proactive next-step suggestion feature."""

    enabled: bool = Field(
        default=True,
        description="Master switch. False = middleware not mounted, prompt blocks not injected.",
    )
    budget: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum proactive next-step suggestions per conversation.",
    )
    cooldown_turns: int = Field(
        default=5,
        ge=1,
        le=20,
        description="After hitting rejection_threshold, suppress suggestions for this many user turns.",
    )
    rejection_threshold: int = Field(
        default=2,
        ge=1,
        le=5,
        description="Consecutive explicit rejections before entering cooldown.",
    )
    same_topic_similarity: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="difflib SequenceMatcher ratio above which two suggestions are treated as the same topic.",
    )
    reject_keywords: list[str] = Field(
        default_factory=lambda: list(DEFAULT_REJECT_KEYWORDS),
        description="Substrings that mark the user's reply as a rejection. Matched case-insensitively after stripping.",
    )
    accept_keywords: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ACCEPT_KEYWORDS),
        description="Substrings that mark the user's reply as an acceptance. Matched case-insensitively after stripping.",
    )


_next_step_config: NextStepConfig = NextStepConfig()


def get_next_step_config() -> NextStepConfig:
    """Get the current next-step configuration."""
    return _next_step_config


def set_next_step_config(config: NextStepConfig) -> None:
    """Replace the next-step configuration (used in tests)."""
    global _next_step_config
    _next_step_config = config


def load_next_step_config_from_dict(config_dict: dict) -> None:
    """Load next-step configuration from a config.yaml block."""
    global _next_step_config
    _next_step_config = NextStepConfig(**config_dict)

"""Configuration for tool output truncation.

Caps any single ``ToolMessage`` content at a configurable size and spills
the original to a file in the sandbox workspace, so individual tool
responses can never single-handedly exceed
``summarization.trim_tokens_to_summarize``. See thread
``09417ecf-b0e0-470f-ae3d-244a0b1ba74d`` for the failure mode this
guards against (one giant docling-parsed xlsx response made
``trim_messages`` return an empty list, which collapsed the entire
thread history through langchain's summarization fallback path).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ToolOutputTruncationConfig(BaseModel):
    """Truncation policy for ``ToolMessage`` payloads.

    The character-based limits are deliberate: tokenizing every tool
    response on the hot path is expensive, and a fixed character cap is
    a safe upper bound (a 4-byte UTF-8 sequence still costs at most one
    token). For a 1M-token model window with
    ``trim_tokens_to_summarize=320000``, capping single messages at
    60000 chars (~20K tokens) keeps any 16-call burst inside the
    summarizer's input budget.
    """

    enabled: bool = Field(
        default=False,
        description="Whether to truncate oversized tool outputs and spill them to a sandbox file.",
    )
    max_chars: int = Field(
        default=60000,
        ge=1,
        description="Maximum character length of a single ToolMessage content before truncation kicks in.",
    )
    keep_head_chars: int = Field(
        default=12000,
        ge=0,
        description="Characters preserved from the start of the original content in the truncated message.",
    )
    keep_tail_chars: int = Field(
        default=8000,
        ge=0,
        description="Characters preserved from the end of the original content in the truncated message.",
    )
    spill_dir: str = Field(
        default=".tool_outputs",
        description="Subdirectory under /mnt/user-data/workspace where full payloads are spilled.",
    )
    skip_tool_names: list[str] = Field(
        default_factory=list,
        description="Tool names exempted from truncation (e.g. tools that already return structured/bounded output).",
    )

    @model_validator(mode="after")
    def _validate_keep_fits_max(self) -> ToolOutputTruncationConfig:
        if self.keep_head_chars + self.keep_tail_chars >= self.max_chars:
            msg = f"keep_head_chars + keep_tail_chars must be strictly less than max_chars; got head={self.keep_head_chars} tail={self.keep_tail_chars} max={self.max_chars}. Otherwise truncation can't shrink the content."
            raise ValueError(msg)
        return self


_tool_output_config: ToolOutputTruncationConfig = ToolOutputTruncationConfig()


def get_tool_output_config() -> ToolOutputTruncationConfig:
    """Return the active tool-output truncation config."""
    return _tool_output_config


def set_tool_output_config(config: ToolOutputTruncationConfig) -> None:
    """Replace the active tool-output truncation config."""
    global _tool_output_config
    _tool_output_config = config


def load_tool_output_config_from_dict(config_dict: dict) -> None:
    """Load tool-output truncation config from a dict (e.g. parsed YAML)."""
    global _tool_output_config
    _tool_output_config = ToolOutputTruncationConfig(**config_dict)

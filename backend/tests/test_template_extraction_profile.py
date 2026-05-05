"""Tests for the S3 template_extraction lead-agent profile + tool.

Coverage:
  - Registry routes ``task_type=template_extraction`` to the right profile.
  - The profile's prompt embeds the result-marker tags + a
    scan_template_fields call instruction.
  - ``scan_template_fields_tool`` correctly handles unsupported
    extensions, missing files, oversized files, and a real .xlsx round
    trip via the existing text_scanner.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from src.agents.lead_agent.profiles import (
    ProfileContext,
    resolve_profile,
    template_extraction,
)
from src.agents.lead_agent.template_extraction_prompt import (
    RESULT_CLOSE_TAG,
    RESULT_OPEN_TAG,
    apply_template_extraction_prompt_template,
)

# ---------- Routing ---------------------------------------------------------


def test_router_picks_template_extraction_when_task_type_is_explicit():
    ctx = ProfileContext(task_type="template_extraction")
    assert resolve_profile(ctx) is template_extraction.PROFILE


def test_template_extraction_explicit_task_type_overrides_tenant_routing():
    """A tenant_id without profile.json must NOT redirect a template_extraction
    run into onboarding — the explicit task_type wins."""
    ctx = ProfileContext(task_type="template_extraction", tenant_id="acme-001")
    assert resolve_profile(ctx) is template_extraction.PROFILE


# ---------- Prompt content --------------------------------------------------


def test_prompt_mentions_scan_template_fields_first():
    prompt = apply_template_extraction_prompt_template()
    # The agent must call the scanner once per file before reasoning;
    # if the wording drifts, the agent will burn turns reading raw bytes.
    assert "scan_template_fields" in prompt
    assert "必须先调" in prompt or "MUST" in prompt or "必须" in prompt


def test_prompt_pins_result_marker_tags():
    prompt = apply_template_extraction_prompt_template()
    assert RESULT_OPEN_TAG in prompt
    assert RESULT_CLOSE_TAG in prompt
    # The marker constants are part of the upstream contract; their
    # values must stay angle-bracketed so plain string-search works.
    assert RESULT_OPEN_TAG.startswith("<") and RESULT_CLOSE_TAG.endswith(">")


def test_prompt_does_not_carry_business_blocks():
    """Lean prompt — no vendor concealment, next step, vision routing, or
    erpnext-cli routing. Those are business-profile concerns."""
    prompt = apply_template_extraction_prompt_template()
    for forbidden in ("<vendor_concealment>", "<next_step", "<vision_routing>", "<trade_skill_routing>"):
        assert forbidden not in prompt, f"prompt should not contain {forbidden}"


def test_profile_includes_ask_clarification_tool_and_middleware():
    """Full B2 contract requires ask_clarification + ClarificationMiddleware
    so genuinely ambiguous fields surface as native FE cards instead of
    being guessed silently."""
    from src.agents.lead_agent.profiles import (
        ProfileContext,
        template_extraction,
    )
    from src.agents.lead_agent.profiles.types import ModelSpec
    from src.agents.middlewares.clarification_middleware import (
        ClarificationMiddleware,
    )

    ctx = ProfileContext(task_type="template_extraction")
    spec = ModelSpec(name="x", thinking_enabled=False)

    tool_names = {t.name for t in template_extraction.PROFILE.select_tools(ctx, spec)}
    assert "ask_clarification" in tool_names
    assert "scan_template_fields" in tool_names

    mws = template_extraction.PROFILE.select_middlewares(ctx, {}, spec)
    assert any(isinstance(m, ClarificationMiddleware) for m in mws)


# ---------- Profile model selection ----------------------------------------


def test_profile_disables_thinking_even_when_request_asks_for_it(monkeypatch):
    """Extraction is structured output; thinking budget is wasted."""
    from src.agents.lead_agent.profiles import _common as profiles_common
    from src.config.app_config import AppConfig
    from src.config.model_config import ModelConfig
    from src.config.sandbox_config import SandboxConfig

    app = AppConfig(
        models=[
            ModelConfig(
                name="thinking-model",
                display_name="thinking-model",
                description=None,
                use="langchain_openai:ChatOpenAI",
                model="thinking-model",
                supports_thinking=True,
                supports_vision=False,
            )
        ],
        sandbox=SandboxConfig(use="src.sandbox.local:LocalSandboxProvider"),
    )
    monkeypatch.setattr(profiles_common, "get_app_config", lambda: app)

    ctx = ProfileContext(task_type="template_extraction", thinking_enabled=True)
    spec = template_extraction.PROFILE.resolve_model(ctx)

    assert spec.name == "thinking-model"
    assert spec.thinking_enabled is False
    assert spec.reasoning_effort is None


# ---------- Tool: scan_template_fields_tool --------------------------------


def _build_xlsx_bytes(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "客户名称"
    ws["B1"] = "合同编号"
    wb.save(path)
    return path


def _invoke_tool(tool, **kwargs):
    """Call a @tool-decorated function with a stub runtime."""
    return tool.func(runtime=None, **kwargs)


def test_scan_template_fields_returns_error_on_missing_file():
    from src.tools.builtins import scan_template_fields_tool

    out = _invoke_tool(scan_template_fields_tool, file_path="/tmp/does_not_exist.docx")
    assert out.startswith("Error: file_not_found")


def test_scan_template_fields_rejects_unsupported_extension(tmp_path: Path):
    from src.tools.builtins import scan_template_fields_tool

    p = tmp_path / "foo.txt"
    p.write_text("hello")
    out = _invoke_tool(scan_template_fields_tool, file_path=str(p))
    assert out.startswith("Error: unsupported_extension")


def test_scan_template_fields_returns_chunks_for_xlsx(tmp_path: Path):
    from src.tools.builtins import scan_template_fields_tool

    p = _build_xlsx_bytes(tmp_path / "headers.xlsx")
    out = _invoke_tool(scan_template_fields_tool, file_path=str(p))

    assert not out.startswith("Error:"), f"unexpected scan failure: {out}"
    # The scanner emits header cells with location hints — we don't pin
    # exact text since the overview line varies, but the column headers
    # and their cell coordinates must show up.
    assert "客户名称" in out
    assert "合同编号" in out
    assert "Sheet!A1" in out or "!A1" in out

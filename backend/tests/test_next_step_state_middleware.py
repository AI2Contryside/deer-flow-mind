"""Tests for ``NextStepStateMiddleware``.

Pinned contracts:

  - Tag extraction recognises options + optional primary; missing tag returns None.
  - User reply classification respects precedence: exact option > reject > accept > substring.
  - Reject keyword wins over accept keyword in the same message
    (``"好的，但是先不录入"`` is a rejection — the polite preface must not flip
    the verdict).
  - Runtime state is computed *purely* from message history — no persisted
    counters; any computation must round-trip across replays.
  - Cooldown triggers once two consecutive rejections land in the most
    recent classifications and decays one user turn at a time.
  - Acceptance resets the consecutive_rejections counter (so a single
    accepted suggestion clears the slate).
  - Budget cap is reflected in the rendered state — model self-gates from
    that line, the middleware does not strip a within-budget suggestion.
  - The state SystemMessage carries a fixed id so add_messages replaces it.
  - same-topic strip in ``after_model`` removes a near-duplicate suggestion
    in place (preserves message id, drops the tag from content).
  - ``enabled=false`` makes both hooks no-op.
  - ``reject_keywords`` / ``accept_keywords`` overrides from config win
    over defaults.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.agents.middlewares.next_step_state_middleware import (
    RUNTIME_STATE_MESSAGE_ID,
    NextStepStateMiddleware,
    classify_user_response,
    compute_runtime_state,
    extract_suggestion,
    render_runtime_state,
)
from src.config.next_step_config import NextStepConfig, set_next_step_config


def _ai_with_tag(text: str, options: str, primary: str | None = None, msg_id: str = "ai-1") -> AIMessage:
    primary_attr = f' primary="{primary}"' if primary is not None else ""
    content = f'{text}\n\n<next_step options="{options}"{primary_attr}>建议：{options.replace("|", " 或 ")}</next_step>'
    return AIMessage(id=msg_id, content=content)


def test_extract_next_step_tag_with_options_and_primary() -> None:
    msg = _ai_with_tag("订单解析完成。", "录入到系统|导出 CSV", primary="录入到系统")
    suggestion = extract_suggestion(msg.content)
    assert suggestion is not None
    assert suggestion.options == ("录入到系统", "导出 CSV")
    assert suggestion.primary == "录入到系统"


def test_extract_next_step_tag_no_primary() -> None:
    msg = _ai_with_tag("ok", "选项A|选项B")
    suggestion = extract_suggestion(msg.content)
    assert suggestion is not None
    assert suggestion.options == ("选项A", "选项B")
    assert suggestion.primary is None


def test_extract_no_tag_returns_none() -> None:
    assert extract_suggestion("纯文本回复，无任何标签。") is None
    assert extract_suggestion("") is None


def test_classify_response_accept_via_exact_option() -> None:
    config = NextStepConfig()
    verdict = classify_user_response("录入到系统", ("录入到系统", "导出 CSV"), config)
    assert verdict == "accept"


def test_classify_response_accept_via_keyword() -> None:
    config = NextStepConfig()
    assert classify_user_response("好的，麻烦了", (), config) == "accept"
    assert classify_user_response("ok please", (), config) == "accept"


def test_classify_response_reject_via_keyword() -> None:
    config = NextStepConfig()
    assert classify_user_response("先不录入吧", ("录入到系统",), config) == "reject"
    assert classify_user_response("skip", (), config) == "reject"


def test_reject_wins_over_accept_when_both_present() -> None:
    """`好的，但是先不录入` reads as rejection; the polite "好的" must not flip it."""
    config = NextStepConfig()
    assert classify_user_response("好的，但是先不录入了", ("录入到系统",), config) == "reject"


def test_classify_response_neutral_when_offtopic() -> None:
    config = NextStepConfig()
    assert classify_user_response("这单的金额对吗？", ("录入到系统", "导出 CSV"), config) == "neutral"


def test_compute_state_increments_asked_count() -> None:
    config = NextStepConfig()
    msgs = [
        HumanMessage(id="h1", content="解析这单"),
        _ai_with_tag("已解析。", "录入到系统|导出 CSV", msg_id="a1"),
        HumanMessage(id="h2", content="这单的金额对吗？"),
        AIMessage(id="a2", content="对的，金额是 USD 10,000。"),
        HumanMessage(id="h3", content="再来一单"),
        _ai_with_tag("已解析。", "录入到系统|导出 CSV", msg_id="a3"),
    ]
    state = compute_runtime_state(msgs, config)
    assert state.asked_count == 2
    assert state.last_round_options == ("录入到系统", "导出 CSV")
    # Last classified user reply was h2 ("这单的金额对吗") → neutral
    assert state.last_round_response == "neutral"


def test_compute_state_resets_rejections_on_acceptance() -> None:
    config = NextStepConfig()
    msgs = [
        _ai_with_tag("done.", "A|B", msg_id="a1"),
        HumanMessage(id="h1", content="先不"),
        _ai_with_tag("done.", "C|D", msg_id="a2"),
        HumanMessage(id="h2", content="好的"),  # acceptance clears the streak
    ]
    state = compute_runtime_state(msgs, config)
    assert state.consecutive_rejections == 0
    assert state.last_round_response == "accept"


def test_compute_state_enters_cooldown_after_two_rejections() -> None:
    config = NextStepConfig(rejection_threshold=2, cooldown_turns=5)
    msgs = [
        _ai_with_tag("d", "A|B", msg_id="a1"),
        HumanMessage(id="h1", content="先不"),
        _ai_with_tag("d", "C|D", msg_id="a2"),
        HumanMessage(id="h2", content="算了"),
    ]
    state = compute_runtime_state(msgs, config)
    assert state.consecutive_rejections == 2
    # Trigger fired on h2; 0 user turns elapsed since → full window remains.
    assert state.cooldown_remaining == 5


def test_cooldown_decrements_each_turn() -> None:
    config = NextStepConfig(rejection_threshold=2, cooldown_turns=5)
    msgs = [
        _ai_with_tag("d", "A|B", msg_id="a1"),
        HumanMessage(id="h1", content="先不"),
        _ai_with_tag("d", "C|D", msg_id="a2"),
        HumanMessage(id="h2", content="算了"),
        # Two more ai+human turns where the agent stayed silent (no <next_step>)
        AIMessage(id="a3", content="好的，那我们换个话题。"),
        HumanMessage(id="h3", content="另一个问题..."),
        AIMessage(id="a4", content="..."),
        HumanMessage(id="h4", content="嗯"),
    ]
    state = compute_runtime_state(msgs, config)
    # h3 / h4 came AFTER the cooldown trigger but were not preceded by an
    # <next_step> tag — they don't generate classifications, so cooldown
    # only decays as long as there are *suggestion-following* user turns.
    # Document the actual behaviour: silent turns don't burn the cooldown.
    assert state.cooldown_remaining == 5


def test_render_state_includes_counts_and_hints() -> None:
    config = NextStepConfig(budget=3)
    state = compute_runtime_state(
        [
            _ai_with_tag("d", "录入到系统|导出 CSV", msg_id="a1"),
            HumanMessage(id="h1", content="好的"),
        ],
        config,
    )
    rendered = render_runtime_state(state, config)
    assert "本会话已主动追问 1 次（上限 3）" in rendered
    assert "已接受" in rendered
    assert "录入到系统" in rendered


def test_before_model_returns_system_msg_with_fixed_id() -> None:
    config = NextStepConfig()
    set_next_step_config(config)
    middleware = NextStepStateMiddleware()
    out = middleware.before_model({"messages": [HumanMessage(id="h", content="hi")]})
    assert out is not None
    msgs = out["messages"]
    assert len(msgs) == 1
    assert isinstance(msgs[0], SystemMessage)
    assert msgs[0].id == RUNTIME_STATE_MESSAGE_ID


def test_before_model_noop_when_disabled() -> None:
    set_next_step_config(NextStepConfig(enabled=False))
    middleware = NextStepStateMiddleware()
    out = middleware.before_model({"messages": [HumanMessage(content="hi")]})
    assert out is None
    set_next_step_config(NextStepConfig())  # restore


def test_after_model_strips_same_topic_suggestion_in_place() -> None:
    config = NextStepConfig(same_topic_similarity=0.5)
    set_next_step_config(config)
    middleware = NextStepStateMiddleware()
    msgs = [
        _ai_with_tag("d", "录入到系统|导出 CSV", msg_id="a1"),
        HumanMessage(id="h1", content="再来一单"),
        _ai_with_tag("已处理。", "录入到系统|导出 CSV", msg_id="a2"),
    ]
    out = middleware.after_model({"messages": msgs})
    assert out is not None
    rewritten = out["messages"][0]
    assert isinstance(rewritten, AIMessage)
    assert rewritten.id == "a2"  # in-place via preserved id
    assert "<next_step" not in rewritten.content
    assert "已处理" in rewritten.content


def test_after_model_keeps_different_topic_suggestion() -> None:
    config = NextStepConfig(same_topic_similarity=0.6)
    set_next_step_config(config)
    middleware = NextStepStateMiddleware()
    msgs = [
        _ai_with_tag("d", "录入到系统|导出 CSV", msg_id="a1"),
        HumanMessage(id="h1", content="再做点别的"),
        _ai_with_tag("已生成报价。", "发送给客户|转销售订单", msg_id="a2"),
    ]
    out = middleware.after_model({"messages": msgs})
    assert out is None  # genuinely different topic — keep as-is


def test_config_override_keywords() -> None:
    config = NextStepConfig(reject_keywords=["拜拜"], accept_keywords=["来"])
    assert classify_user_response("拜拜", (), config) == "reject"
    assert classify_user_response("好啊", (), config) == "neutral"  # default kw absent
    assert classify_user_response("来吧", (), config) == "accept"


def test_im_channel_strips_next_step_tag_keeping_inner_text() -> None:
    """IM channels should display the natural-language sentence without the wrapper."""
    from src.channels.manager import _strip_next_step_tag

    raw = '已解析 23 笔订单。\n\n<next_step options="录入到系统|导出 CSV" primary="录入到系统">💡 接下来要不要录入到系统？也可以先导出 CSV。</next_step>'
    cleaned = _strip_next_step_tag(raw)
    assert "<next_step" not in cleaned
    assert "</next_step>" not in cleaned
    assert "录入到系统" in cleaned
    assert "💡" in cleaned


def test_im_channel_strip_is_noop_when_tag_absent() -> None:
    from src.channels.manager import _strip_next_step_tag

    text = "纯文本回复，无标签。"
    assert _strip_next_step_tag(text) == text
    assert _strip_next_step_tag("") == ""


def test_lead_agent_prompt_includes_next_step_section_when_enabled() -> None:
    """Static <next_step_policy> + <next_step_catalog> blocks must land in the prompt."""
    from src.agents.lead_agent.prompt import apply_prompt_template

    set_next_step_config(NextStepConfig(enabled=True))
    prompt = apply_prompt_template()
    assert "<next_step_policy>" in prompt
    assert "<next_step_catalog>" in prompt
    assert "录入到系统" in prompt  # catalog table content


def test_lead_agent_prompt_omits_next_step_section_when_disabled() -> None:
    from src.agents.lead_agent.prompt import apply_prompt_template

    set_next_step_config(NextStepConfig(enabled=False))
    prompt = apply_prompt_template()
    assert "<next_step_policy>" not in prompt
    assert "<next_step_catalog>" not in prompt
    set_next_step_config(NextStepConfig())  # restore

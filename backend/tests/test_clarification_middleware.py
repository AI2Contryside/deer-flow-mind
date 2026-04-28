"""Unit tests for ClarificationMiddleware._format_clarification_message.

Covers the legacy fields (question, context, options) plus the new
structured `fields` parameter that lets the agent declare an interactive
form. The output must:

  - Stay readable for non-widget clients (IM channels, old frontend).
  - Be deterministic enough for the frontend's stripping regex to remove it
    once the rich widget is rendered.
"""

from src.agents.middlewares.clarification_middleware import ClarificationMiddleware


def _format(args: dict) -> str:
    return ClarificationMiddleware()._format_clarification_message(args)


def test_format_question_only_uses_default_icon():
    out = _format({"question": "请问您要继续吗?", "clarification_type": "missing_info"})

    assert out.startswith("❓ ")
    assert "请问您要继续吗?" in out


def test_format_renders_context_before_question():
    out = _format(
        {
            "question": "请问您的姓名、年龄和性别是什么?",
            "clarification_type": "missing_info",
            "context": "需要了解您的基本信息以便更好地为您服务。",
        }
    )

    # Context comes first, blank line, then the question.
    assert out.startswith("❓ 需要了解您的基本信息以便更好地为您服务。")
    assert "\n请问您的姓名、年龄和性别是什么?" in out


def test_format_options_use_numbered_list():
    out = _format(
        {
            "question": "您希望按哪种方式处理?",
            "clarification_type": "approach_choice",
            "options": ["保留原数据", "整体覆盖", "增量合并"],
        }
    )

    assert "🔀 您希望按哪种方式处理?" in out
    assert "  1. 保留原数据" in out
    assert "  2. 整体覆盖" in out
    assert "  3. 增量合并" in out


def test_format_fields_labels_appear_with_required_marker():
    out = _format(
        {
            "question": "请补充客户信息。",
            "clarification_type": "missing_info",
            "fields": [
                {"label": "公司名称", "type": "text", "required": True},
                {"label": "成立年份", "type": "number", "placeholder": "如 2018"},
            ],
        }
    )

    assert "  · 公司名称 *" in out
    # Non-required fields lack the asterisk; the type hint is parenthesised.
    assert "  · 成立年份（number）" in out


def test_format_fields_render_select_options_inline():
    out = _format(
        {
            "question": "请补充信息。",
            "clarification_type": "missing_info",
            "fields": [
                {
                    "label": "主要市场",
                    "type": "multiselect",
                    "options": ["北美", "欧洲", "东南亚", "中东"],
                }
            ],
        }
    )

    # multiselect type + options hint are merged into a single (…) suffix.
    assert "  · 主要市场（multiselect；选项：北美/欧洲/东南亚/中东）" in out


def test_format_fields_truncate_option_list_when_long():
    out = _format(
        {
            "question": "选择品类。",
            "clarification_type": "missing_info",
            "fields": [
                {
                    "label": "品类",
                    "type": "select",
                    "options": [f"选项{i}" for i in range(8)],
                }
            ],
        }
    )

    # Only the first 5 options appear, then an ellipsis. Keeps the inline
    # hint short for non-widget clients.
    assert "选项0/选项1/选项2/选项3/选项4/…" in out


def test_format_fields_skip_malformed_entries():
    # Entries missing a label or that aren't dicts are silently dropped so a
    # bad agent payload can't blow up the chat.
    out = _format(
        {
            "question": "请补充信息。",
            "clarification_type": "missing_info",
            "fields": [
                {"label": "姓名"},
                {"type": "text"},  # no label → dropped
                "not a dict",  # wrong type → dropped
                {"label": "  ", "type": "text"},  # empty label → dropped
                {"label": "电话"},
            ],
        }
    )

    assert "  · 姓名" in out
    assert "  · 电话" in out
    # Only two rendered field lines exist.
    assert out.count("  · ") == 2


def test_format_no_fields_no_options_returns_question_only():
    out = _format({"question": "怎么继续?", "clarification_type": "ambiguous_requirement"})

    # No trailing options/field block; the icon picks up ambiguous_requirement.
    assert out == "🤔 怎么继续?"

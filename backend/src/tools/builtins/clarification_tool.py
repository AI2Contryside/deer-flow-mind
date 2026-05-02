from typing import Any, Literal

from langchain.tools import tool


# return_direct must stay False here. Why:
#   ClarificationMiddleware now pauses execution via ``interrupt()`` and, on
#   resume, re-enters wrap_tool_call to commit a ToolMessage(formatted_question)
#   plus a HumanMessage(answer) via Command(update=...). After that update,
#   control flows through langchain's `tools_to_model` edge (factory.py
#   `_make_tools_to_model_edge`), which short-circuits to END whenever every
#   client-side tool call on the last AIMessage has return_direct=True. Setting
#   return_direct=True on this tool therefore caused the post-resume run to
#   exit instead of letting the model react to the user's reply — the agent
#   silently did nothing after the user submitted a clarification answer.
#
#   Under the old Command(goto=END) design, return_direct=True was harmless
#   because the goto explicitly ended the run before this edge ran. The flag
#   only became load-bearing — and load-bearing in the wrong direction —
#   once the middleware switched to interrupt()/resume.
@tool("ask_clarification", parse_docstring=True)
def ask_clarification_tool(
    question: str,
    clarification_type: Literal[
        "missing_info",
        "ambiguous_requirement",
        "approach_choice",
        "risk_confirmation",
        "suggestion",
    ],
    context: str | None = None,
    options: list[str] | None = None,
    fields: list[dict[str, Any]] | None = None,
) -> str:
    """Ask the user for clarification when you need more information to proceed.

    Use this tool when you encounter situations where you cannot proceed without user input:

    - **Missing information**: Required details not provided (e.g., file paths, URLs, specific requirements)
    - **Ambiguous requirements**: Multiple valid interpretations exist
    - **Approach choices**: Several valid approaches exist and you need user preference
    - **Risky operations**: Destructive actions that need explicit confirmation (e.g., deleting files, modifying production)
    - **Suggestions**: You have a recommendation but want user approval before proceeding

    The execution will be interrupted and the question will be presented to the user.
    Wait for the user's response before continuing.

    When to use ask_clarification:
    - You need information that wasn't provided in the user's request
    - The requirement can be interpreted in multiple ways
    - Multiple valid implementation approaches exist
    - You're about to perform a potentially dangerous operation
    - You have a recommendation but need user approval

    Best practices:
    - Ask ONE clarification at a time for clarity
    - Be specific and clear in your question
    - Don't make assumptions when clarification is needed
    - For risky operations, ALWAYS ask for confirmation
    - After calling this tool, execution will be interrupted automatically

    Frontend rendering hints:
    - When `clarification_type` is `risk_confirmation` and no `options`/`fields`
      are given, the UI renders a Confirm/Cancel button pair.
    - When `options` is set, the UI renders a single- or multi-select list.
    - When `fields` is set, the UI renders a structured form (one input per
      field) — use this whenever you need several discrete pieces of
      information in one turn (e.g. name + age + email). Prefer `fields` over
      a single comma-listed `question` — it gives the user labeled inputs and
      an unambiguous answer schema.
    - When neither `options` nor `fields` is set, the UI renders a free-text
      reply box. The frontend will also try a heuristic split of the question
      into fields, but supplying `fields` explicitly is far more reliable.

    `fields` schema — each entry is a dict with these keys:
        `label` (string, REQUIRED): the field name shown to the user.
        `key` (string): submission key, defaults to `label`.
        `type` (string): one of "text" (default), "textarea", "number", "date",
            "email", "select", "multiselect", "boolean".
        `placeholder` (string): hint text inside the input.
        `description` (string): help text shown below the label.
        `options` (list of strings): choices for select / multiselect.
        `required` (boolean): whether the user must fill it. Default false.

    Example `fields` payload — collecting tenant onboarding info::

        [
            {"label": "公司名称", "type": "text", "required": true},
            {"label": "成立年份", "type": "number", "placeholder": "如 2018"},
            {"label": "主要市场", "type": "multiselect",
             "options": ["北美", "欧洲", "东南亚", "中东"]},
            {"label": "是否有 ERPNext 经验", "type": "boolean"}
        ]

    Use `fields` whenever the question would otherwise read
    "请提供 A、B、C…" — it produces a labeled form rather than one free-text box.

    Args:
        question: The clarification question to ask the user. Be specific and clear.
        clarification_type: The type of clarification needed (missing_info, ambiguous_requirement, approach_choice, risk_confirmation, suggestion).
        context: Optional context explaining why clarification is needed. Helps the user understand the situation.
        options: Optional list of choices (for approach_choice or suggestion types). Present clear options for the user to choose from.
        fields: Optional list of structured field definitions; see the schema and example above. Pass this when you need multiple discrete inputs in one turn so the UI can render a form.
    """
    # This is a placeholder implementation
    # The actual logic is handled by ClarificationMiddleware which intercepts this tool call
    # and interrupts execution to present the question to the user
    return "Clarification request processed by middleware"

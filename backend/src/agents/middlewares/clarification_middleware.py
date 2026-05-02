"""Middleware for intercepting clarification requests and presenting them to the user."""

from collections.abc import Callable
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command, interrupt


class ClarificationMiddlewareState(AgentState):
    """Compatible with the `ThreadState` schema."""

    pass


class ClarificationMiddleware(AgentMiddleware[ClarificationMiddlewareState]):
    """Intercepts clarification tool calls and pauses execution via LangGraph
    ``interrupt()`` so the gateway can route the user's reply back into the same
    thread via ``Command(resume=...)``.

    Why interrupt() instead of Command(goto=END)
    --------------------------------------------
    The earlier design ended the run cleanly (status="success") with the
    formatted question persisted as a ToolMessage. From the persistence layer
    that looked indistinguishable from an ordinary completed turn, so any FE
    bookkeeping miss (page reload, lost local-id binding, accidental "new
    chat" click) would route the user's answer to a fresh thread and the
    original conversation appeared to vanish. See thread
    ``36ea98b4-1e6b-4fcc-8fca-0cd921699d65`` for the failure mode.

    With ``interrupt()`` the thread state itself signals "awaiting user
    response" (``status="interrupted"``, payload exposed via
    ``thread.interrupts``). The gateway can detect this and force the next
    user message to resume the interrupted thread instead of creating a new
    one — making the routing robust against FE state drift.

    On resume, the user's answer flows back through ``interrupt()``'s return
    value. The middleware then commits both the formatted question (as the
    ToolMessage closing the original tool_call) and a HumanMessage carrying
    the answer, so the persisted history remains
    ``AI[tool_call] -> Tool[question] -> Human[answer] -> AI[...]`` — the
    same shape FE renderers and IM channels already understand.
    """

    state_schema = ClarificationMiddlewareState

    def _format_clarification_message(self, args: dict) -> str:
        """Format the clarification arguments into a user-friendly message.

        The output is the canonical text representation a non-widget client
        (older frontend, IM channel) sees in chat history. Modern clients pull
        the structured args from the gateway's tool_invocation_start event and
        render a widget instead — but they still receive this text via the
        ToolMessage so we keep both paths in sync.

        Args:
            args: The tool call arguments containing clarification details

        Returns:
            Formatted message string
        """
        question = args.get("question", "")
        clarification_type = args.get("clarification_type", "missing_info")
        context = args.get("context")
        options = args.get("options", [])
        fields = args.get("fields") or []

        # Type-specific icons
        type_icons = {
            "missing_info": "❓",
            "ambiguous_requirement": "🤔",
            "approach_choice": "🔀",
            "risk_confirmation": "⚠️",
            "suggestion": "💡",
        }

        icon = type_icons.get(clarification_type, "❓")

        # Build the message naturally
        message_parts = []

        # Add icon and question together for a more natural flow
        if context:
            # If there's context, present it first as background
            message_parts.append(f"{icon} {context}")
            message_parts.append(f"\n{question}")
        else:
            # Just the question with icon
            message_parts.append(f"{icon} {question}")

        # Add options in a cleaner format
        if options and len(options) > 0:
            message_parts.append("")  # blank line for spacing
            for i, option in enumerate(options, 1):
                message_parts.append(f"  {i}. {option}")

        # Render structured form fields as a labeled list. This is the text
        # fallback for clients without the form widget; the rich widget reads
        # the same data structure from args_preview and ignores this block.
        if fields and isinstance(fields, list):
            message_parts.append("")
            for f in fields:
                if not isinstance(f, dict):
                    continue
                label = str(f.get("label") or "").strip()
                if not label:
                    continue
                ftype = str(f.get("type") or "text")
                required_mark = " *" if f.get("required") else ""
                line = f"  · {label}{required_mark}"
                # Append type / option hint so a plain-text reader still
                # understands what's expected. Keep it inline & short.
                hint_parts = []
                if ftype not in ("text", "textarea", ""):
                    hint_parts.append(ftype)
                opts = f.get("options")
                if isinstance(opts, list) and opts:
                    short = "/".join(str(o) for o in opts[:5])
                    if len(opts) > 5:
                        short += "/…"
                    hint_parts.append(f"选项：{short}")
                desc = str(f.get("description") or "").strip()
                if desc:
                    hint_parts.append(desc)
                if hint_parts:
                    line += f"（{'；'.join(hint_parts)}）"
                message_parts.append(line)

        return "\n".join(message_parts)

    def _build_resume_messages(
        self,
        formatted_question: str,
        tool_call_id: str,
        answer: object,
    ) -> list:
        """Build the messages to commit on resume.

        Returns the ToolMessage that closes the original ``ask_clarification``
        tool_call, plus a HumanMessage carrying the user's answer when one
        was provided. Empty / null answers (``Command(resume=None)`` or the
        user submitting an empty string) only commit the ToolMessage so the
        protocol invariant — every tool_call.id has a matching ToolMessage —
        still holds and the model can decide how to proceed.
        """
        tool_message = ToolMessage(
            content=formatted_question,
            tool_call_id=tool_call_id,
            name="ask_clarification",
        )
        messages: list = [tool_message]
        if answer is None:
            return messages
        answer_text = str(answer).strip() if not isinstance(answer, str) else answer.strip()
        if answer_text:
            messages.append(HumanMessage(content=answer_text))
        return messages

    def _handle_clarification(self, request: ToolCallRequest) -> Command:
        """Pause execution via interrupt() and, on resume, commit the answer.

        First call (model just emitted ``ask_clarification``):
            ``interrupt(payload)`` raises ``GraphInterrupt`` — LangGraph
            checkpoints the thread, marks it ``status="interrupted"``, and
            surfaces ``payload`` via ``thread.interrupts``. The function does
            not return.

        Second call (gateway forwarded ``Command(resume=user_text)``):
            LangGraph re-executes this whole wrapper. ``interrupt`` now
            returns the resume value rather than raising. We assemble the
            ToolMessage + HumanMessage and return them in a Command(update).
            The agent loop continues normally from there.
        """
        args = request.tool_call.get("args", {}) or {}
        tool_call_id = request.tool_call.get("id", "")
        formatted_message = self._format_clarification_message(args)

        # First-call path raises; second-call path returns the resume value.
        # The payload is what the gateway / FE see in `thread.interrupts`.
        # Keep it self-describing so downstream consumers (Go gateway, IM
        # channels, future tooling) can pattern-match on `type` without
        # parsing free-form text.
        answer = interrupt(
            {
                "type": "ask_clarification",
                "tool_call_id": tool_call_id,
                "args": args,
                "formatted_question": formatted_message,
            }
        )

        # Reached only on resume. Persist the canonical shape.
        messages = self._build_resume_messages(formatted_message, tool_call_id, answer)
        return Command(update={"messages": messages})

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """Intercept ask_clarification tool calls and interrupt execution (sync version).

        Args:
            request: Tool call request
            handler: Original tool execution handler

        Returns:
            Command that interrupts execution with the formatted clarification message
        """
        # Check if this is an ask_clarification tool call
        if request.tool_call.get("name") != "ask_clarification":
            # Not a clarification call, execute normally
            return handler(request)

        return self._handle_clarification(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """Intercept ask_clarification tool calls and interrupt execution (async version).

        Args:
            request: Tool call request
            handler: Original tool execution handler (async)

        Returns:
            Command that interrupts execution with the formatted clarification message
        """
        # Check if this is an ask_clarification tool call
        if request.tool_call.get("name") != "ask_clarification":
            # Not a clarification call, execute normally
            return await handler(request)

        return self._handle_clarification(request)

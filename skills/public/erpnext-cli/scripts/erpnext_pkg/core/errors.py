"""Typed errors for the ERPNext client.

Agents need unambiguous error classes to self-correct. Every failure mode
we can distinguish gets its own subclass of ``ERPNextError``.

Two payload surfaces exist on every error:

  - ``internal_payload`` — full diagnostic context (URL, raw response
    body) for **server-side logs only**. Never returned through the CLI's
    ``--json`` output and never reachable by the agent.
  - ``to_dict()`` — sanitized public representation. Strips URLs,
    bearer tokens, ``Authorization`` headers, internal IPs, and HTML
    debugger pages so this is safe to surface to the agent / end user.

This split was added after session b987fdbe-... where ``payload={"url":
url}`` and raw 500 HTML pages with ``Authorization: token <ak>:<sk>``
were echoed straight to chat.
"""

from __future__ import annotations

import re
from typing import Any

# Patterns that must never appear in agent-visible error output.
_TOKEN_RE = re.compile(r"token\s+[A-Za-z0-9]+:[A-Za-z0-9]+", re.IGNORECASE)
_AUTH_HEADER_RE = re.compile(r"Authorization\s*:\s*[^\s\"',]+", re.IGNORECASE)
_API_KV_RE = re.compile(r"(api[_-]?(?:key|secret))\s*[:=]\s*[\"']?[A-Za-z0-9_-]+", re.IGNORECASE)
_INTERNAL_IP_RE = re.compile(r"\bhttps?://(?:10|127|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d+\.\d+(?::\d+)?\S*")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _sanitize_text(text: str | None, *, max_len: int = 500) -> str:
    """Strip tokens / Authorization headers / internal URLs / HTML noise.

    Used on every error message we expose to the agent. We deliberately
    keep this lossy — agents do not need URLs to recover, and the user
    must never see them.
    """
    if not text:
        return ""
    s = str(text)
    # If body is an HTML debugger page (Werkzeug / Flask 500), keep only
    # the <title> line — the full body trails into stack frames that may
    # contain credentials and host paths.
    if "<!doctype html" in s.lower() or "<html" in s.lower():
        m = re.search(r"<title>([^<]+)</title>", s, flags=re.IGNORECASE | re.DOTALL)
        s = (m.group(1).strip() if m else "ServerError") + " (HTML response suppressed)"
    s = _TOKEN_RE.sub("token ***:***", s)
    s = _AUTH_HEADER_RE.sub("Authorization: ***", s)
    s = _API_KV_RE.sub(r"\1=***", s)
    s = _INTERNAL_IP_RE.sub("[internal-url]", s)
    # Collapse remaining HTML tags as a safety net.
    s = _HTML_TAG_RE.sub("", s).strip()
    if len(s) > max_len:
        s = s[:max_len] + "…"
    return s


class ERPNextError(Exception):
    """Base class for all cli-anything-erpnext errors.

    Three surfaces:
      - ``internal_payload`` — full context, for log aggregation only.
      - ``to_dict()`` — sanitized, safe to surface to agents / users.
      - ``next_actions`` — structured "what to try next" hints. Pinned
        after session b987fdbe-... where the agent saw raw
        ``"BrokenPipeError [Errno 32]"`` strings and could only guess.
        Each subclass populates a default list; classify_http_error
        enriches with field-level hints when it can extract them.
    """

    # Subclass override: a static fallback list of next actions used when
    # the classifier can't produce more specific guidance. Subclasses
    # define this in class scope so it remains visible to test suites
    # without instantiating an exception.
    default_next_actions: list[dict[str, str]] = []

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: dict | None = None,
        next_actions: list[dict[str, str]] | None = None,
    ):
        super().__init__(message)
        # Keep the raw message for log paths via ``internal_payload``;
        # ``self.message`` exposes the sanitized form because some
        # callers ``str(exc)`` it directly.
        self._raw_message = message
        self.message = _sanitize_text(message)
        self.status_code = status_code
        self.internal_payload: dict[str, Any] = dict(payload or {})
        # ``self.payload`` is preserved as a back-compat alias but it now
        # points at the *public* (sanitized) view, never the URL.
        self.payload: dict[str, Any] = {}
        # next_actions is sanitized by construction (it never contains
        # URLs or tokens — only command strings + reason text we author).
        self.next_actions: list[dict[str, str]] = list(
            next_actions if next_actions is not None else self.default_next_actions
        )

    def to_dict(self) -> dict[str, Any]:
        """Sanitized representation. Safe for ``--json`` output / agent prompt."""
        return {
            "error": type(self).__name__,
            "message": self.message,
            "status_code": self.status_code,
            "payload": self.payload,
            "next_actions": list(self.next_actions),
        }

    def to_internal_dict(self) -> dict[str, Any]:
        """Full diagnostic context. Server logs only — never surface this."""
        return {
            "error": type(self).__name__,
            "message": self._raw_message,
            "status_code": self.status_code,
            "internal_payload": self.internal_payload,
            "next_actions": list(self.next_actions),
        }


class AuthError(ERPNextError):
    """Authentication or session error.

    By the time this surfaces, the client has already attempted a
    transparent re-login (for username/password mode) or confirmed that
    no harness-injected credentials are usable. The agent should escalate,
    not retry.
    """

    default_next_actions = [
        {"action": "ask user", "reason": "凭证缺失或失效; 提示用户重新授权, 不要自行重试或调用 session login"},
    ]


class NotFoundError(ERPNextError):
    """Target DocType or record does not exist."""

    default_next_actions = [
        {"action": "doc list <DocType>", "reason": "确认要查找的记录是否存在 / 拼写是否正确"},
        {"action": "bootstrap status", "reason": "如租户为空, 先创建必要 master"},
    ]


class ValidationError(ERPNextError):
    """Frappe rejected the request with a validation error."""

    default_next_actions = [
        {"action": "ask user", "reason": "请用户提供缺失字段的取值, 不要自行编造"},
    ]


class PermissionError_(ERPNextError):
    """Frappe rejected the request with a permission error."""

    default_next_actions = [
        {"action": "ask user", "reason": "当前账号无权限, 提示用户切换或扩权"},
    ]


class ServerError(ERPNextError):
    """Frappe returned 5xx."""

    default_next_actions = [
        {"action": "stop_retrying", "reason": "上游 5xx 通常需要运维介入, 重试无意义"},
        {"action": "ask user", "reason": "告知用户系统暂时不可用, 询问是否换种方式或稍后重试"},
    ]


class WorkflowError(ERPNextError):
    """A business-flow precondition failed (e.g., submitting a cancelled doc)."""

    default_next_actions = [
        {"action": "doc get <DocType> <Name>", "reason": "确认文档当前状态再决定下一步"},
    ]


# Frappe 的 MandatoryError 消息常见三种形态:
#   1. "Mandatory fields required: <field>"        — keyword + colon + field
#   2. "<field> is required"                        — field + " is required"
#   3. "row N: <field>.<sub> is mandatory"          — "<field>... mandatory"
# 第一规则要求 keyword 紧跟冒号 (避免吃掉 "required" 自身), 第二规则要求
# 字段名后紧跟 "is required" / "is mandatory"。两条 OR 起来取第一处命中。
_MANDATORY_FIELD_RE = re.compile(
    r'(?:'
    # "Mandatory fields ...: <field>" / "Missing fields: <field>"
    r'(?:Mandatory|Missing)(?:\s+fields?)?[^:\n]{0,40}[:=]\s*["\']?([a-z_][a-z0-9_]{1,60})'
    r'|'
    # "<field> is mandatory" / "<field> is required"
    r'\b([a-z_][a-z0-9_]{1,60})\b\s+is\s+(?:mandatory|required)'
    r')',
    re.IGNORECASE,
)
# Some Frappe error pages name the linked DocType, e.g. "Could not find Customer".
_LINK_TARGET_RE = re.compile(
    r'(?:Could not find|Link not found|does not exist|not found)\s+([A-Z][A-Za-z ]{1,40}?)\b',
)


def _enrich_validation_actions(message: str) -> list[dict[str, str]]:
    """Build per-field next_actions from a Frappe validation message."""
    actions: list[dict[str, str]] = []
    m = _MANDATORY_FIELD_RE.search(message)
    if m:
        # Whichever alternation arm matched, the captured group lives in
        # group(1) or group(2) — pick the first non-None.
        field = m.group(1) or m.group(2)
        if field:
            actions.append({
                "action": f"set field {field}",
                "reason": f"Frappe 报告字段 {field} 必填; 取值需要用户确认",
            })
            actions.append({
                "action": "ask user",
                "reason": f"询问用户 {field} 的值, 不要自行编造",
            })
    return actions or list(ValidationError.default_next_actions)


def _enrich_notfound_actions(message: str) -> list[dict[str, str]]:
    """Build a Link-aware next_actions list from a NotFound message."""
    m = _LINK_TARGET_RE.search(message)
    if m:
        target = m.group(1).strip()
        return [
            {"action": f"doc list {target}", "reason": f"先列出可用的 {target} 记录"},
            {"action": "bootstrap status", "reason": "确认租户是否已建好 master 数据"},
        ]
    return list(NotFoundError.default_next_actions)


def classify_http_error(status_code: int, body: dict | str, url: str) -> ERPNextError:
    """Map an HTTP status code + body to a typed error.

    ``url`` is captured into ``internal_payload`` for log diagnostics
    only — it is never echoed back to the agent. ``next_actions`` is
    populated with per-field / per-class hints so the agent has a
    concrete next step instead of guessing (see session b987fdbe-...
    where vague errors led to 84-step thrash).
    """
    if isinstance(body, dict):
        exc_type = body.get("exc_type") or ""
        message = body.get("message") or body.get("exception") or str(body)
    else:
        exc_type = ""
        message = str(body)[:1000]

    payload = {"url": url}

    if status_code == 401 or status_code == 403:
        if "PermissionError" in exc_type:
            return PermissionError_(message, status_code=status_code, payload=payload)
        return AuthError(message, status_code=status_code, payload=payload)
    if status_code == 404 or "DoesNotExistError" in exc_type:
        return NotFoundError(
            message, status_code=status_code, payload=payload,
            next_actions=_enrich_notfound_actions(message),
        )
    if status_code in (417, 422) or "ValidationError" in exc_type or "MandatoryError" in exc_type:
        return ValidationError(
            message, status_code=status_code, payload=payload,
            next_actions=_enrich_validation_actions(message),
        )
    if status_code >= 500:
        return ServerError(message, status_code=status_code, payload=payload)
    return ERPNextError(message, status_code=status_code, payload=payload)

"""Typed errors for the ERPNext client.

Agents need unambiguous error classes to self-correct. Every failure mode
we can distinguish gets its own subclass of ``ERPNextError``.
"""

from __future__ import annotations


class ERPNextError(Exception):
    """Base class for all cli-anything-erpnext errors."""

    def __init__(self, message: str, *, status_code: int | None = None,
                 payload: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload or {}

    def to_dict(self) -> dict:
        return {
            "error": type(self).__name__,
            "message": self.message,
            "status_code": self.status_code,
            "payload": self.payload,
        }


class AuthError(ERPNextError):
    """Authentication or session error."""


class NotFoundError(ERPNextError):
    """Target DocType or record does not exist."""


class ValidationError(ERPNextError):
    """Frappe rejected the request with a validation error."""


class PermissionError_(ERPNextError):
    """Frappe rejected the request with a permission error."""


class ServerError(ERPNextError):
    """Frappe returned 5xx."""


class WorkflowError(ERPNextError):
    """A business-flow precondition failed (e.g., submitting a cancelled doc)."""


def classify_http_error(status_code: int, body: dict | str, url: str) -> ERPNextError:
    """Map an HTTP status code + body to a typed error."""
    if isinstance(body, dict):
        exc_type = body.get("exc_type") or ""
        message = body.get("message") or body.get("exception") or str(body)
    else:
        exc_type = ""
        message = str(body)[:500]

    if status_code == 401 or status_code == 403:
        if "PermissionError" in exc_type:
            return PermissionError_(message, status_code=status_code, payload={"url": url})
        return AuthError(message, status_code=status_code, payload={"url": url})
    if status_code == 404 or "DoesNotExistError" in exc_type:
        return NotFoundError(message, status_code=status_code, payload={"url": url})
    if status_code in (417, 422) or "ValidationError" in exc_type or "MandatoryError" in exc_type:
        return ValidationError(message, status_code=status_code, payload={"url": url})
    if status_code >= 500:
        return ServerError(message, status_code=status_code, payload={"url": url})
    return ERPNextError(message, status_code=status_code, payload={"url": url})

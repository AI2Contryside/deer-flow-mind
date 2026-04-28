"""Tests for erpnext-cli core/errors.py public/internal payload split.

Pinned after session b987fdbe-... where ``payload={"url": url}`` and full
HTML 500 debugger pages bearing ``Authorization: token <ak>:<sk>`` were
echoed straight back to the agent and then to the user. The rules:

  - ``to_dict()`` (used by ``--json`` output) must never expose the
    upstream URL, the bearer token, the api_key/api_secret, or the
    internal IP address.
  - ``to_internal_dict()`` retains the full diagnostic context for
    server-side log aggregation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The skill bundle lives outside the backend src/ path; expose it for import.
_SKILL_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "public" / "erpnext-cli" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

from erpnext_pkg.core.errors import (  # noqa: E402
    AuthError,
    ERPNextError,
    NotFoundError,
    ServerError,
    ValidationError,
    classify_http_error,
)


@pytest.mark.unit
def test_to_dict_strips_internal_url_from_payload() -> None:
    err = classify_http_error(
        500,
        "BrokenPipeError: [Errno 32] Broken pipe",
        url="http://10.37.31.108:8000/api/method/frappe.client.insert",
    )
    public = err.to_dict()
    serialized = repr(public)
    assert "10.37.31.108" not in serialized
    assert "/api/method/" not in serialized
    # The public payload should not carry url at all.
    assert public["payload"] == {}


@pytest.mark.unit
def test_to_internal_dict_retains_url_for_logs() -> None:
    err = classify_http_error(
        500,
        "BrokenPipeError",
        url="http://10.37.31.108:8000/api/method/frappe.client.insert",
    )
    internal = err.to_internal_dict()
    assert internal["internal_payload"]["url"] == "http://10.37.31.108:8000/api/method/frappe.client.insert"


@pytest.mark.unit
def test_message_strips_authorization_token() -> None:
    raw = "Upstream rejected request: curl -H 'Authorization: token b7f7419b3bb9fc1:bbe0bce22f13719' failed with 500"
    err = ServerError(raw, status_code=500, payload={"url": "http://10.37.31.108:8000/foo"})
    public_msg = err.to_dict()["message"]
    assert "b7f7419b3bb9fc1" not in public_msg
    assert "bbe0bce22f13719" not in public_msg
    assert "Authorization: token" not in public_msg
    assert "***" in public_msg


@pytest.mark.unit
def test_message_strips_api_key_and_secret_kv_form() -> None:
    err = ValidationError(
        "validation failed: api_key=b7f7419b3bb9fc1, api_secret=bbe0bce22f13719",
        status_code=417,
    )
    msg = err.to_dict()["message"]
    assert "b7f7419b3bb9fc1" not in msg
    assert "bbe0bce22f13719" not in msg


@pytest.mark.unit
def test_message_collapses_html_500_debugger_page() -> None:
    html_body = (
        "<!doctype html><html lang=en><head>"
        "<title>BrokenPipeError: [Errno 32] Broken pipe // Werkzeug Debugger</title>"
        "</head><body>"
        "<pre>Authorization: token b7f7419b3bb9fc1:bbe0bce22f13719</pre>"
        "<pre>file /data00/home/wuwenzhao/frappe/frappe-bench/...</pre>"
        "</body></html>"
    )
    err = classify_http_error(500, html_body, url="http://10.37.31.108:8000/api/")
    msg = err.to_dict()["message"]
    assert "<!doctype" not in msg.lower()
    assert "<html" not in msg.lower()
    assert "/data00/" not in msg
    assert "b7f7419b3bb9fc1" not in msg
    # Title fragment should survive so the agent has something to act on.
    assert "BrokenPipeError" in msg
    assert "HTML response suppressed" in msg


@pytest.mark.unit
def test_message_redacts_internal_ip_in_freeform_text() -> None:
    err = AuthError(
        "denied at http://10.37.31.108:8000/api/resource/Company",
        status_code=403,
    )
    msg = err.to_dict()["message"]
    assert "10.37.31.108" not in msg
    assert "[internal-url]" in msg


@pytest.mark.unit
def test_classify_routes_status_codes_to_typed_classes() -> None:
    assert isinstance(classify_http_error(401, "no", url="http://10.0.0.1/x"), AuthError)
    assert isinstance(classify_http_error(404, "no", url="http://10.0.0.1/x"), NotFoundError)
    assert isinstance(classify_http_error(417, "no", url="http://10.0.0.1/x"), ValidationError)
    assert isinstance(classify_http_error(500, "no", url="http://10.0.0.1/x"), ServerError)


@pytest.mark.unit
def test_payload_alias_is_public_view_not_url() -> None:
    # Back-compat: any caller that read .payload directly must get the
    # sanitized public view (which is empty), not the raw {"url": ...}.
    err = classify_http_error(500, "BrokenPipe", url="http://10.0.0.1/x")
    assert err.payload == {}
    assert err.internal_payload == {"url": "http://10.0.0.1/x"}


@pytest.mark.unit
def test_base_class_constructs_without_payload() -> None:
    err = ERPNextError("just a message")
    assert err.to_dict()["message"] == "just a message"
    assert err.to_dict()["payload"] == {}
    assert err.to_internal_dict()["internal_payload"] == {}


# ---------- next_actions enrichment (P1.1b) -------------------------------


@pytest.mark.unit
def test_to_dict_includes_next_actions_field() -> None:
    err = ServerError("BrokenPipeError", status_code=500)
    out = err.to_dict()
    assert "next_actions" in out
    assert isinstance(out["next_actions"], list)
    assert len(out["next_actions"]) >= 1
    for entry in out["next_actions"]:
        assert "action" in entry and "reason" in entry


@pytest.mark.unit
def test_server_error_says_stop_retrying() -> None:
    # Pinned to b987fdbe behaviour: agent saw BrokenPipe 8x in a row.
    # The error envelope must signal "don't retry" so the agent escalates.
    err = classify_http_error(500, "BrokenPipeError", url="http://10.0.0.1/x")
    actions = err.to_dict()["next_actions"]
    assert any(a["action"] == "stop_retrying" for a in actions)


@pytest.mark.unit
def test_validation_error_extracts_missing_field_name() -> None:
    raw = "Mandatory fields required: default_currency"
    err = classify_http_error(417, raw, url="http://10.0.0.1/x")
    actions = err.to_dict()["next_actions"]
    # First action should call out the specific field, not just say "ask user".
    assert any("default_currency" in a["action"] for a in actions)


@pytest.mark.unit
def test_validation_error_falls_back_when_no_field_extractable() -> None:
    err = classify_http_error(422, "Validation failed", url="http://10.0.0.1/x")
    actions = err.to_dict()["next_actions"]
    assert len(actions) >= 1
    assert all("action" in a and "reason" in a for a in actions)


@pytest.mark.unit
def test_notfound_error_targets_link_when_present() -> None:
    err = classify_http_error(404, "Could not find Customer 'BIEL'", url="http://10.0.0.1/x")
    actions = err.to_dict()["next_actions"]
    assert any("doc list Customer" in a["action"] for a in actions)
    # Empty-tenant hint should still be present.
    assert any(a["action"] == "bootstrap status" for a in actions)


@pytest.mark.unit
def test_auth_error_does_not_suggest_retry() -> None:
    err = classify_http_error(401, "token rejected", url="http://10.0.0.1/x")
    actions = err.to_dict()["next_actions"]
    # Two failure modes this guards against:
    # - b987fdbe-...: the agent retried with curl after the CLI complained.
    #   `next_actions` must not invite retry of any kind.
    # - 09417ecf precursor: the harness now retries cookie-based auth
    #   transparently before surfacing AuthError, so by the time this
    #   error reaches the agent, "check session status" is the wrong
    #   advice — the only correct path is escalating to the user. The
    #   action surface therefore must include an "ask user" action and
    #   must NOT suggest re-running session status (which would just
    #   loop).
    assert any("ask user" in a["action"] for a in actions), actions
    assert not any("session status" in a["action"] for a in actions), actions
    assert not any("retry" in a["action"].lower() for a in actions), actions


@pytest.mark.unit
def test_next_actions_does_not_leak_url() -> None:
    err = classify_http_error(500, "BrokenPipe", url="http://10.37.31.108:8000/api/method/x")
    serialized = repr(err.to_dict()["next_actions"])
    assert "10.37.31.108" not in serialized
    assert "/api/method" not in serialized


@pytest.mark.unit
def test_subclass_default_next_actions_visible_at_class_scope() -> None:
    # Test pins that defaults are introspectable without instantiation
    # (the agent prompt may want to enumerate them at build time).
    assert isinstance(ServerError.default_next_actions, list)
    assert isinstance(AuthError.default_next_actions, list)
    assert any(a["action"] == "stop_retrying" for a in ServerError.default_next_actions)


@pytest.mark.unit
def test_explicit_next_actions_override_default() -> None:
    custom = [{"action": "do_xyz", "reason": "test override"}]
    err = ValidationError("any", next_actions=custom)
    assert err.to_dict()["next_actions"] == custom

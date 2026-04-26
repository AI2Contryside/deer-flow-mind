"""Low-level Frappe/ERPNext REST client.

Wraps Frappe's two API surfaces:
  - ``/api/resource/<DocType>/<name>``  — DocType CRUD
  - ``/api/method/<path.to.method>``    — whitelisted RPC (the real
    engine for every business-process chain: ``make_sales_invoice``,
    ``make_delivery_note``, ``get_payment_entry``, etc.)

Authentication
--------------
Two modes, both first-class:

  1. **API token** (preferred, non-interactive):
       ``Authorization: token <api_key>:<api_secret>``
  2. **Session login** (for username/password flows):
       ``POST /api/method/login`` with ``usr`` + ``pwd`` → cookie jar

The client follows HARNESS.md's "fail loudly and clearly" rule: every
non-2xx response is classified into a typed error (see ``core.errors``)
so agents can decide whether to retry, re-auth, or surface to the user.
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable
from urllib.parse import urljoin

import requests

from .errors import AuthError, ERPNextError, classify_http_error


DEFAULT_TIMEOUT = 60


class FrappeClient:
    """Minimal, typed Frappe REST client.

    Instances are cheap to create but hold a ``requests.Session`` internally
    for connection pooling and cookie persistence (when using session login).
    """

    def __init__(
        self,
        url: str,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        username: str | None = None,
        password: str | None = None,
        tenant_id: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        verify_ssl: bool = True,
    ):
        # Tenant scoping is mandatory per CLAUDE.md: every ERPNext call
        # originating from DeerFlow must carry X-Tenant-ID. The server uses
        # this header to set the PG session variable that drives row-level
        # security; a missing/empty header makes tenant-scoped tables appear
        # empty (and would silently cross tenant boundaries on writes).
        # Normalize empty strings to "missing" and refuse to construct a
        # tenant-less client.
        normalized_tenant = (tenant_id or "").strip() or None
        if not normalized_tenant:
            raise AuthError(
                "X-Tenant-ID is required. Pass --tenant <id> or set "
                "ERPNEXT_TENANT_ID before invoking the CLI."
            )

        self.url = url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.verify = verify_ssl
        self._api_key = api_key
        self._api_secret = api_secret
        self._username = username
        self._password = password
        self._tenant_id = normalized_tenant
        self._logged_in = False

        if api_key and api_secret:
            self._session.headers["Authorization"] = f"token {api_key}:{api_secret}"
        self._session.headers.setdefault("Accept", "application/json")
        self._session.headers.setdefault("X-Frappe-CLI", "cli-anything-erpnext")
        self._session.headers["X-Tenant-ID"] = normalized_tenant

    @property
    def tenant_id(self) -> str | None:
        return self._tenant_id

    def with_tenant(self, tenant_id: str) -> "FrappeClient":
        """Return a shallow copy bound to a different tenant. Each clone owns
        its own requests.Session so concurrent use across tenants cannot
        race on shared headers. ``tenant_id`` must be non-empty — the
        constructor enforces the same invariant."""
        return FrappeClient(
            self.url,
            api_key=self._api_key,
            api_secret=self._api_secret,
            username=self._username,
            password=self._password,
            tenant_id=tenant_id,
            timeout=self.timeout,
            verify_ssl=self._session.verify,
        )

    # ── Auth ──────────────────────────────────────────────────────────

    def login(self, username: str | None = None, password: str | None = None) -> dict:
        """Log in via username/password and store cookies in the session.

        Not needed if ``api_key``/``api_secret`` were provided — token auth
        requires no session state.
        """
        usr = username or self._username
        pwd = password or self._password
        if not usr or not pwd:
            raise AuthError("login() requires username and password")
        resp = self._session.post(
            urljoin(self.url + "/", "api/method/login"),
            data={"usr": usr, "pwd": pwd},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise classify_http_error(resp.status_code, _body(resp), resp.url)
        self._logged_in = True
        return _body(resp) or {"message": "Logged In"}

    def logout(self) -> None:
        try:
            self._session.get(
                urljoin(self.url + "/", "api/method/logout"),
                timeout=self.timeout,
            )
        finally:
            self._logged_in = False

    def ping(self) -> dict:
        """Sanity-check the connection + auth. Returns the currently
        authenticated Frappe user (or ``Guest``)."""
        return self.call_method("frappe.auth.get_logged_user")

    # ── DocType CRUD ──────────────────────────────────────────────────

    def get_doc(self, doctype: str, name: str) -> dict:
        """Fetch a full document by DocType + name."""
        r = self._request("GET", f"/api/resource/{_q(doctype)}/{_q(name)}")
        return r.get("data", r)

    def get_list(
        self,
        doctype: str,
        *,
        filters: list | dict | None = None,
        fields: Iterable[str] | None = None,
        limit: int = 20,
        start: int = 0,
        order_by: str | None = None,
    ) -> list[dict]:
        """List documents of ``doctype`` with Frappe's standard filter syntax."""
        params: dict[str, Any] = {"limit_page_length": limit, "limit_start": start}
        if fields:
            params["fields"] = json.dumps(list(fields))
        if filters is not None:
            params["filters"] = json.dumps(filters)
        if order_by:
            params["order_by"] = order_by
        r = self._request("GET", f"/api/resource/{_q(doctype)}", params=params)
        return r.get("data", [])

    def insert(self, doc: dict) -> dict:
        """Insert a new document. ``doc`` must include ``doctype``."""
        if "doctype" not in doc:
            raise ValueError("insert(): doc must include 'doctype'")
        r = self._request(
            "POST",
            f"/api/resource/{_q(doc['doctype'])}",
            json_body={"data": json.dumps(doc)},
        )
        return r.get("data", r)

    def update(self, doctype: str, name: str, changes: dict) -> dict:
        """PUT a partial update onto an existing document."""
        r = self._request(
            "PUT",
            f"/api/resource/{_q(doctype)}/{_q(name)}",
            json_body={"data": json.dumps(changes)},
        )
        return r.get("data", r)

    def delete(self, doctype: str, name: str) -> dict:
        r = self._request("DELETE", f"/api/resource/{_q(doctype)}/{_q(name)}")
        return r or {"message": "ok"}

    def set_value(self, doctype: str, name: str, field: str, value: Any) -> dict:
        return self.call_method(
            "frappe.client.set_value",
            doctype=doctype, name=name, fieldname=field, value=value,
        )

    def submit(self, doctype: str, name: str) -> dict:
        """Submit (docstatus 0 → 1) a document."""
        return self.call_method("frappe.client.submit", doc=self.get_doc(doctype, name))

    def cancel(self, doctype: str, name: str) -> dict:
        """Cancel (docstatus 1 → 2) a document."""
        return self.call_method("frappe.client.cancel", doctype=doctype, name=name)

    def rename(self, doctype: str, old: str, new: str, merge: bool = False) -> dict:
        return self.call_method(
            "frappe.client.rename_doc",
            doctype=doctype, old_name=old, new_name=new, merge=int(merge),
        )

    def exists(self, doctype: str, name: str) -> bool:
        try:
            self.get_doc(doctype, name)
            return True
        except ERPNextError as exc:
            if exc.status_code == 404:
                return False
            raise

    # ── RPC: the real workflow engine ─────────────────────────────────

    def call_method(self, method: str, **kwargs) -> Any:
        """Call a whitelisted Frappe method by dotted path.

        This is how every ``make_*`` business chainer is invoked —
        ``make_sales_invoice(source_name=...)``, ``get_payment_entry(dt, dn)``,
        etc. The return value is Frappe's ``message`` payload (unwrapped).
        """
        payload = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                   for k, v in kwargs.items()}
        r = self._request("POST", f"/api/method/{method}", form=payload)
        if isinstance(r, dict) and "message" in r:
            return r["message"]
        return r

    def run_doc_method(self, doctype: str, name: str, method: str,
                       **kwargs) -> Any:
        """Invoke a DocType-bound whitelisted method (instance method)."""
        return self.call_method(
            "frappe.client.run_doc_method",
            dt=doctype, dn=name, method=method, args=kwargs or {},
        )

    # ── Transport ─────────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        form: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        url = urljoin(self.url + "/", path.lstrip("/"))
        resp = self._session.request(
            method, url,
            params=params,
            data=form if form is not None else json_body,
            timeout=self.timeout,
        )
        body = _body(resp)
        if resp.status_code >= 400:
            raise classify_http_error(resp.status_code, body, url)
        if isinstance(body, dict):
            return body
        return {"data": body}


# ── Helpers ───────────────────────────────────────────────────────────

def _body(resp: requests.Response) -> dict | str:
    try:
        return resp.json()
    except ValueError:
        return resp.text


def _q(s: str) -> str:
    """Percent-encode a path segment. Frappe DocType names contain spaces."""
    from urllib.parse import quote
    return quote(str(s), safe="")


# ── Convenience constructor ───────────────────────────────────────────

def client_from_env() -> FrappeClient:
    """Build a client from env vars. Used by the CLI when no session exists.

    Env vars:
      - ``ERPNEXT_URL``          (required)
      - ``ERPNEXT_API_KEY``      (preferred) + ``ERPNEXT_API_SECRET``
      - ``ERPNEXT_USERNAME``     (fallback) + ``ERPNEXT_PASSWORD``
      - ``ERPNEXT_TENANT_ID``    tenant bound to outgoing X-Tenant-ID header
      - ``ERPNEXT_VERIFY_SSL``   ("0" to disable)
    """
    url = os.environ.get("ERPNEXT_URL")
    if not url:
        raise AuthError(
            "ERPNEXT_URL is not set. Run: "
            "cli-anything-erpnext session login --url https://... --api-key ... --api-secret ..."
        )
    tenant = (os.environ.get("ERPNEXT_TENANT_ID") or "").strip() or None
    if not tenant:
        raise AuthError(
            "ERPNEXT_TENANT_ID is not set. Every ERPNext call must be "
            "tenant-scoped via X-Tenant-ID."
        )
    return FrappeClient(
        url,
        api_key=os.environ.get("ERPNEXT_API_KEY"),
        api_secret=os.environ.get("ERPNEXT_API_SECRET"),
        username=os.environ.get("ERPNEXT_USERNAME"),
        password=os.environ.get("ERPNEXT_PASSWORD"),
        tenant_id=tenant,
        verify_ssl=os.environ.get("ERPNEXT_VERIFY_SSL", "1") != "0",
    )

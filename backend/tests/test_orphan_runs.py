"""Unit tests for orphan run cleanup at startup."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.agents.checkpointer.orphan_runs import mark_orphan_runs_as_interrupted


@pytest.fixture
def fake_global_store():
    """Patch ``GLOBAL_STORE`` with a plain dict that mimics the in-memory
    runtime's persistent store."""
    store: dict = {"runs": []}

    fake_module = SimpleNamespace(GLOBAL_STORE=store)
    fake_root = SimpleNamespace(database=fake_module)

    saved_root = sys.modules.get("langgraph_runtime_inmem")
    saved_db = sys.modules.get("langgraph_runtime_inmem.database")

    sys.modules["langgraph_runtime_inmem"] = fake_root
    sys.modules["langgraph_runtime_inmem.database"] = fake_module

    yield store

    if saved_root is None:
        sys.modules.pop("langgraph_runtime_inmem", None)
    else:
        sys.modules["langgraph_runtime_inmem"] = saved_root
    if saved_db is None:
        sys.modules.pop("langgraph_runtime_inmem.database", None)
    else:
        sys.modules["langgraph_runtime_inmem.database"] = saved_db


class TestMarkOrphanRunsAsInterrupted:
    def test_flips_running_runs_to_interrupted(self, fake_global_store):
        before_call = datetime.now(UTC)
        fake_global_store["runs"] = [
            {"run_id": "r1", "status": "running", "updated_at": before_call - timedelta(minutes=10)},
            {"run_id": "r2", "status": "running", "updated_at": before_call - timedelta(minutes=10)},
        ]

        n = mark_orphan_runs_as_interrupted()

        assert n == 2
        assert fake_global_store["runs"][0]["status"] == "interrupted"
        assert fake_global_store["runs"][1]["status"] == "interrupted"
        # updated_at should be refreshed past the prior value
        assert fake_global_store["runs"][0]["updated_at"] >= before_call
        assert fake_global_store["runs"][1]["updated_at"] >= before_call

    def test_leaves_other_statuses_untouched(self, fake_global_store):
        fake_global_store["runs"] = [
            {"run_id": "r1", "status": "pending"},
            {"run_id": "r2", "status": "success"},
            {"run_id": "r3", "status": "interrupted"},
            {"run_id": "r4", "status": "error"},
            {"run_id": "r5", "status": "running"},
        ]

        n = mark_orphan_runs_as_interrupted()

        assert n == 1
        statuses = [r["status"] for r in fake_global_store["runs"]]
        assert statuses == ["pending", "success", "interrupted", "error", "interrupted"]

    def test_returns_zero_when_no_runs(self, fake_global_store):
        fake_global_store["runs"] = []

        assert mark_orphan_runs_as_interrupted() == 0

    def test_returns_zero_when_runs_key_missing(self, fake_global_store):
        fake_global_store.pop("runs", None)

        assert mark_orphan_runs_as_interrupted() == 0

    def test_returns_zero_when_no_running_runs(self, fake_global_store):
        fake_global_store["runs"] = [
            {"run_id": "r1", "status": "success"},
            {"run_id": "r2", "status": "pending"},
        ]

        assert mark_orphan_runs_as_interrupted() == 0
        # Pending must NOT be touched — workers will pick it up legitimately.
        assert fake_global_store["runs"][1]["status"] == "pending"

    def test_no_op_when_inmem_runtime_not_installed(self):
        """Production deployments use a hosted LangGraph runtime, not
        ``langgraph_runtime_inmem`` — the helper must degrade silently."""
        with patch(
            "src.agents.checkpointer.orphan_runs._load_global_store",
            return_value=None,
        ):
            assert mark_orphan_runs_as_interrupted() == 0

    def test_load_global_store_returns_none_on_import_error(self):
        from src.agents.checkpointer import orphan_runs

        # Pretend the inmem runtime module doesn't exist at all.
        saved_root = sys.modules.pop("langgraph_runtime_inmem", None)
        saved_db = sys.modules.pop("langgraph_runtime_inmem.database", None)

        # Force ImportError on the next import attempt.
        sys.modules["langgraph_runtime_inmem"] = None  # type: ignore[assignment]

        try:
            assert orphan_runs._load_global_store() is None
        finally:
            sys.modules.pop("langgraph_runtime_inmem", None)
            if saved_root is not None:
                sys.modules["langgraph_runtime_inmem"] = saved_root
            if saved_db is not None:
                sys.modules["langgraph_runtime_inmem.database"] = saved_db

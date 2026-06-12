"""Tests for the FastAPI memory-activity + capability-inbox routes.

These live on the FastAPI app (`jarvis serve`) — the deployment that
actually answers on 7710. Regression guard for the 2026-06-13 discovery
that endpoints added to the legacy cli/brain_server never serve here.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from openjarvis.server.app import create_app  # noqa: E402


def _make_engine():
    engine = MagicMock()
    engine.engine_id = "mock"
    engine.health.return_value = True
    engine.list_models.return_value = ["test-model"]
    return engine


@pytest.fixture()
def client():
    app = create_app(_make_engine(), "test-model")
    return TestClient(app)


class TestMemoryActivity:
    def test_returns_snapshot(self, client, monkeypatch):
        from openjarvis.tools import memory_activity

        monkeypatch.setattr(
            memory_activity, "snapshot",
            lambda since=None: {"ts": 1.0, "nodes": [{"id": "vault"}], "pulses": [], "log": [], "since": since},
        )
        resp = client.get("/memory/activity")
        assert resp.status_code == 200
        data = resp.json()
        assert data["nodes"][0]["id"] == "vault"
        assert data["since"] is None

    def test_since_param_passed_through(self, client, monkeypatch):
        from openjarvis.tools import memory_activity

        monkeypatch.setattr(
            memory_activity, "snapshot",
            lambda since=None: {"ts": 1.0, "nodes": [], "pulses": [], "log": [], "since": since},
        )
        resp = client.get("/memory/activity?since=123.5")
        assert resp.json()["since"] == 123.5


class TestCapabilityInbox:
    def test_inbox_list(self, client, monkeypatch):
        from openjarvis.tools import capability_inbox

        monkeypatch.setattr(
            capability_inbox, "list_inbox",
            lambda limit=10: {"date": "2026-06-13", "items": [{"capability": "x", "status": "pending"}]},
        )
        resp = client.get("/capability/inbox")
        assert resp.status_code == 200
        assert resp.json()["items"][0]["capability"] == "x"

    def test_approve_routes_to_inbox(self, client, monkeypatch):
        from openjarvis.tools import capability_inbox

        calls = []
        monkeypatch.setattr(
            capability_inbox, "approve",
            lambda cap: calls.append(cap) or {"ok": True, "task_id": "t_1"},
        )
        resp = client.post("/capability/inbox/approve", json={"capability": "pdf-tables"})
        assert resp.status_code == 200
        assert resp.json()["task_id"] == "t_1"
        assert calls == ["pdf-tables"]

    def test_approve_unknown_is_404(self, client, monkeypatch):
        from openjarvis.tools import capability_inbox

        monkeypatch.setattr(
            capability_inbox, "approve", lambda cap: {"ok": False, "error": "unknown"}
        )
        resp = client.post("/capability/inbox/approve", json={"capability": "nope"})
        assert resp.status_code == 404

    def test_dismiss(self, client, monkeypatch):
        from openjarvis.tools import capability_inbox

        monkeypatch.setattr(capability_inbox, "dismiss", lambda cap: {"ok": True})
        resp = client.post("/capability/inbox/dismiss", json={"capability": "x"})
        assert resp.json()["ok"] is True

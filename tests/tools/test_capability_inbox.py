"""Tests for the self-improvement approval inbox (Phase 7 #6)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjarvis.tools import capability_inbox


def _write_queue(root: Path, date: str, items):
    d = root / date
    d.mkdir(parents=True, exist_ok=True)
    (d / "queue.json").write_text(
        json.dumps({"type": "capability-queue", "date": date, "items": items}),
        encoding="utf-8",
    )


ITEM = {
    "capability": "pdf-table-extraction",
    "priority": 80,
    "severity": "high",
    "occurrences": 3,
    "trigger": "operator asked for bank-statement table parsing",
    "action": "prototype",
    "next_agent": "qwen-builder",
    "reason": "high severity; 3 occurrences",
    "scout": {"recommendation": "pdfplumber", "score": 82},
}


@pytest.fixture()
def inbox_home(tmp_path, monkeypatch):
    qroot = tmp_path / "capability_queue"
    monkeypatch.setattr(capability_inbox, "_queue_root", lambda: qroot)
    monkeypatch.setattr(capability_inbox, "_STATE_PATH", tmp_path / "capability_inbox.json")
    return qroot


class TestListInbox:
    def test_lists_latest_queue_as_pending(self, inbox_home):
        _write_queue(inbox_home, "2026-06-11", [{"capability": "old-item", "priority": 10}])
        _write_queue(inbox_home, "2026-06-12", [ITEM])
        result = capability_inbox.list_inbox()
        assert result["date"] == "2026-06-12"
        assert result["items"][0]["capability"] == "pdf-table-extraction"
        assert result["items"][0]["status"] == "pending"

    def test_empty_when_no_queue(self, inbox_home):
        result = capability_inbox.list_inbox()
        assert result["items"] == []

    def test_decisions_survive_new_queue_days(self, inbox_home):
        _write_queue(inbox_home, "2026-06-12", [ITEM])
        capability_inbox.dismiss("pdf-table-extraction")
        _write_queue(inbox_home, "2026-06-13", [ITEM])
        result = capability_inbox.list_inbox()
        assert result["items"][0]["status"] == "dismissed"


class TestDecisions:
    def test_approve_queues_prototype_task(self, inbox_home, monkeypatch):
        calls = []

        class _Reg:
            def add_task(self, title, agent_id, prompt, project_id=None, **kw):
                calls.append({"title": title, "agent_id": agent_id, "prompt": prompt})
                return "t_proto1"

        monkeypatch.setattr(capability_inbox, "_registry", lambda: _Reg())
        _write_queue(inbox_home, "2026-06-12", [ITEM])
        result = capability_inbox.approve("pdf-table-extraction")
        assert result["ok"] is True
        assert result["task_id"] == "t_proto1"
        assert calls[0]["agent_id"] == "qwen-builder"
        assert "pdf-table-extraction" in calls[0]["prompt"]
        assert "pdfplumber" in calls[0]["prompt"]
        assert "sandbox" in calls[0]["prompt"].lower()
        assert capability_inbox.list_inbox()["items"][0]["status"] == "approved"
        assert capability_inbox.list_inbox()["items"][0]["task_id"] == "t_proto1"

    def test_approve_unknown_capability_fails_cleanly(self, inbox_home):
        _write_queue(inbox_home, "2026-06-12", [ITEM])
        result = capability_inbox.approve("nope")
        assert result["ok"] is False

    def test_approve_is_idempotent(self, inbox_home, monkeypatch):
        calls = []

        class _Reg:
            def add_task(self, *a, **kw):
                calls.append(1)
                return "t_proto1"

        monkeypatch.setattr(capability_inbox, "_registry", lambda: _Reg())
        _write_queue(inbox_home, "2026-06-12", [ITEM])
        capability_inbox.approve("pdf-table-extraction")
        second = capability_inbox.approve("pdf-table-extraction")
        assert second["ok"] is True
        assert second["task_id"] == "t_proto1"
        assert len(calls) == 1

    def test_dismiss_marks_item(self, inbox_home):
        _write_queue(inbox_home, "2026-06-12", [ITEM])
        result = capability_inbox.dismiss("pdf-table-extraction")
        assert result["ok"] is True
        assert capability_inbox.list_inbox()["items"][0]["status"] == "dismissed"


class TestBriefingSection:
    def test_pending_items_surface(self, inbox_home):
        _write_queue(inbox_home, "2026-06-12", [ITEM])
        md = capability_inbox.briefing_section()
        assert "pdf-table-extraction" in md
        assert "pending" in md.lower()

    def test_none_when_nothing_pending(self, inbox_home):
        _write_queue(inbox_home, "2026-06-12", [ITEM])
        capability_inbox.dismiss("pdf-table-extraction")
        assert capability_inbox.briefing_section() is None

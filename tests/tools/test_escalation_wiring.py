"""Tests for the escalation ladder wiring in agent_runner (plan Task C)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjarvis.tools import agent_runner, outcomes
from openjarvis.tools.agent_runner import Task


VERIFY_FAILED = {"available": True, "verified": False, "rounds": 3, "exit_code": 1}


def _task(**overrides):
    base = dict(
        id="t-orig",
        title="Fix the widget",
        agent_id="qwen-builder",
        prompt="Fix the widget so the check passes.",
        priority=20,
        project_id="proj-1",
        repo_root=r"E:\Claude\OpenJarvis",
    )
    base.update(overrides)
    return Task(**base)


class _StubReg:
    def __init__(self, child_id="t_child123", raise_on_add=False):
        self.child_id = child_id
        self.raise_on_add = raise_on_add
        self.calls = []

    def add_task(self, title, agent_id, prompt, project_id=None, **kwargs):
        if self.raise_on_add:
            raise RuntimeError("boom")
        self.calls.append(
            dict(title=title, agent_id=agent_id, prompt=prompt,
                 project_id=project_id, **kwargs)
        )
        return self.child_id


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    """Enabled escalation + stub registry + temp day-cap store + workspace."""
    from openjarvis.tools import escalation

    monkeypatch.setenv("OPENJARVIS_ESCALATION", "1")
    monkeypatch.delenv("OPENJARVIS_ESCALATION_AGENT", raising=False)
    monkeypatch.setattr(escalation, "_ESCALATIONS_DIR", tmp_path / "escalations")
    stub = _StubReg()
    monkeypatch.setattr(agent_runner, "_reg", stub)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "RESULT.md").write_text("qwen partial work", encoding="utf-8")
    return SimpleNamespace(stub=stub, ws=ws, tmp=tmp_path)


class TestTaskFields:
    def test_task_has_escalation_fields_defaulting_none(self):
        t = _task()
        assert t.escalation_of is None
        assert t.escalated_to is None


class TestMaybeEscalate:
    def test_verify_failed_queues_child_with_inherited_fields(self, wired):
        t = _task()
        note = agent_runner._maybe_escalate_qwen_task(t, wired.ws, VERIFY_FAILED, "output")
        assert "t_child123" in note
        assert len(wired.stub.calls) == 1
        call = wired.stub.calls[0]
        assert call["agent_id"] == "backend-dev"
        assert call["project_id"] == "proj-1"
        assert call["repo_root"] == r"E:\Claude\OpenJarvis"
        assert call["priority"] == 20
        assert call["parent_task_id"] == "t-orig"
        assert call["escalation_of"] == "t-orig"
        assert "qwen partial work" in call["prompt"]
        assert t.escalated_to == "t_child123"

    def test_disabled_by_default_no_child(self, wired, monkeypatch):
        monkeypatch.delenv("OPENJARVIS_ESCALATION", raising=False)
        note = agent_runner._maybe_escalate_qwen_task(
            _task(), wired.ws, VERIFY_FAILED, "output"
        )
        assert note == ""
        assert wired.stub.calls == []

    def test_escalation_child_never_re_escalates(self, wired):
        t = _task(id="t-child", escalation_of="t-orig")
        note = agent_runner._maybe_escalate_qwen_task(t, wired.ws, VERIFY_FAILED, "x")
        assert note == ""
        assert wired.stub.calls == []

    def test_no_signal_no_child(self, wired):
        note = agent_runner._maybe_escalate_qwen_task(
            _task(), wired.ws, None, "DONE all good"
        )
        assert note == ""
        assert wired.stub.calls == []

    def test_add_task_failure_is_swallowed(self, wired):
        wired.stub.raise_on_add = True
        note = agent_runner._maybe_escalate_qwen_task(
            _task(), wired.ws, VERIFY_FAILED, "output"
        )
        assert note == ""

    def test_day_cap_records_escalation(self, wired):
        from openjarvis.tools import escalation

        agent_runner._maybe_escalate_qwen_task(_task(), wired.ws, VERIFY_FAILED, "x")
        assert escalation._read_day_record()["count"] == 1


class TestOutcomeRecord:
    def test_record_captures_escalation_fields(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(outcomes, "_write_record", lambda r: captured.update(r))
        t = _task(id="t-child", escalation_of="t-orig")
        t.escalated_to = None
        t.status = "done"
        outcomes.record_agent_task(t)
        assert captured["escalation_of"] == "t-orig"
        assert captured["escalated_to"] is None

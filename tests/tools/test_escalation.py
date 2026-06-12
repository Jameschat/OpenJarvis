"""Tests for the Qwen escalation ladder policy module (plan Task B)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjarvis.tools import escalation


def _task(**overrides):
    base = dict(
        id="t-orig",
        title="Fix the widget",
        agent_id="qwen-builder",
        prompt="Fix the widget so the check passes.",
        priority=20,
        project_id="proj-1",
        repo_root=r"E:\Claude\OpenJarvis",
        escalation_of=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


VERIFY_FAILED = {"available": True, "verified": False, "rounds": 3, "exit_code": 1}
VERIFY_PASSED = {"available": True, "verified": True, "rounds": 1, "exit_code": 0}


@pytest.fixture()
def esc_home(tmp_path, monkeypatch):
    """Point the day-cap store at a temp dir and enable the feature."""
    monkeypatch.setattr(escalation, "_ESCALATIONS_DIR", tmp_path / "escalations")
    monkeypatch.setenv("OPENJARVIS_ESCALATION", "1")
    monkeypatch.delenv("OPENJARVIS_ESCALATION_AGENT", raising=False)
    monkeypatch.delenv("OPENJARVIS_ESCALATION_MAX_PER_DAY", raising=False)
    return tmp_path


class TestShouldEscalate:
    def test_off_by_default(self, esc_home, monkeypatch):
        monkeypatch.delenv("OPENJARVIS_ESCALATION", raising=False)
        d = escalation.should_escalate(_task(), VERIFY_FAILED, "some output")
        assert not d.go
        assert "disabled" in d.reason

    def test_verify_failed_escalates(self, esc_home):
        d = escalation.should_escalate(_task(), VERIFY_FAILED, "some output")
        assert d.go
        assert d.reason == "verify-failed"
        assert d.target_agent == "backend-dev"

    def test_verify_passed_does_not_escalate(self, esc_home):
        d = escalation.should_escalate(_task(), VERIFY_PASSED, "DONE all good")
        assert not d.go

    def test_no_signal_does_not_escalate(self, esc_home):
        d = escalation.should_escalate(_task(), None, "DONE everything fine")
        assert not d.go

    def test_blocked_marker_escalates(self, esc_home):
        content = "I could not finish.\nBLOCKED: the sandbox lacks node.exe"
        d = escalation.should_escalate(_task(), None, content)
        assert d.go
        assert d.reason == "blocked"

    def test_state_prefixed_blocked_marker_escalates(self, esc_home):
        d = escalation.should_escalate(_task(), None, "state: BLOCKED - missing model")
        assert d.go

    def test_lowercase_blocked_prose_is_ignored(self, esc_home):
        d = escalation.should_escalate(
            _task(), None, "The drain was blocked but I fixed it. DONE"
        )
        assert not d.go

    def test_escalation_task_never_re_escalates(self, esc_home):
        d = escalation.should_escalate(
            _task(escalation_of="t-orig"), VERIFY_FAILED, "BLOCKED: still failing"
        )
        assert not d.go
        assert "one hop" in d.reason

    def test_target_agent_env_override(self, esc_home, monkeypatch):
        monkeypatch.setenv("OPENJARVIS_ESCALATION_AGENT", "gpt-backend")
        d = escalation.should_escalate(_task(), VERIFY_FAILED, "x")
        assert d.go
        assert d.target_agent == "gpt-backend"

    def test_day_cap_blocks_when_reached(self, esc_home, monkeypatch):
        monkeypatch.setenv("OPENJARVIS_ESCALATION_MAX_PER_DAY", "2")
        escalation.record_escalation("t-a", "t-a-child")
        escalation.record_escalation("t-b", "t-b-child")
        d = escalation.should_escalate(_task(), VERIFY_FAILED, "x")
        assert not d.go
        assert "cap" in d.reason

    def test_day_cap_tolerates_corrupt_file(self, esc_home):
        escalation._ESCALATIONS_DIR.mkdir(parents=True, exist_ok=True)
        (escalation._ESCALATIONS_DIR / escalation._day_filename()).write_text(
            "{not json", encoding="utf-8"
        )
        d = escalation.should_escalate(_task(), VERIFY_FAILED, "x")
        assert d.go


class TestRecordEscalation:
    def test_record_increments_count_and_tracks_ids(self, esc_home):
        escalation.record_escalation("t-1", "t-1-child")
        escalation.record_escalation("t-2", "t-2-child")
        data = json.loads(
            (escalation._ESCALATIONS_DIR / escalation._day_filename()).read_text(
                encoding="utf-8"
            )
        )
        assert data["count"] == 2
        assert {"task_id": "t-1", "child_id": "t-1-child"} in data["escalations"]


class TestBuildEscalationPrompt:
    def test_pack_includes_all_sections(self, esc_home, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "RESULT.md").write_text("# result\nqwen tried hard", encoding="utf-8")
        (ws / "QWEN_REVISION_TRAIL.json").write_text(
            json.dumps({"rounds": [{"n": 1, "verdict": "fail"}]}), encoding="utf-8"
        )
        prompt = escalation.build_escalation_prompt(
            _task(), ws, VERIFY_FAILED, reason="verify-failed"
        )
        assert "Fix the widget so the check passes." in prompt
        assert "qwen tried hard" in prompt
        assert "verify-failed" in prompt
        assert "exit_code" in prompt or "exit code" in prompt.lower()
        assert str(ws) in prompt  # tells the stronger agent where the workspace is

    def test_pack_survives_missing_artifacts(self, esc_home, tmp_path):
        ws = tmp_path / "empty-ws"
        ws.mkdir()
        prompt = escalation.build_escalation_prompt(_task(), ws, None, reason="blocked")
        assert "Fix the widget" in prompt
        assert "not available" in prompt.lower() or "missing" in prompt.lower()

    def test_pack_truncates_huge_result(self, esc_home, tmp_path):
        ws = tmp_path / "big-ws"
        ws.mkdir()
        (ws / "RESULT.md").write_text("x" * 50_000, encoding="utf-8")
        prompt = escalation.build_escalation_prompt(
            _task(), ws, VERIFY_FAILED, reason="verify-failed"
        )
        assert len(prompt) < 30_000
        assert "truncated" in prompt.lower()

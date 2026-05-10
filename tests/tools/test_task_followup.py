"""Tests for task_followup: message formatting + push gating.

The formatter is a pure function — given a Task-like object, produce a
chat-panel string OR None if the task shouldn't surface. Push-side
integration (writing to _chat_history) is tested separately with mocks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from openjarvis.tools import task_followup


@dataclass
class _FakeTask:
    """Stand-in for the real Task dataclass — only the fields we read."""
    id: str = "t_xxxxxxxx"
    agent_id: str = "browser-pilot"
    title: str = "Watch video and brief Qwen optimization"
    priority: int = 20
    status: str = "done"
    started_at: float = 1000.0
    ended_at: float = 1030.4
    error: Optional[str] = None


def test_formats_done_task_with_duration():
    task = _FakeTask()
    msg = task_followup.format_followup(task)
    assert msg is not None
    # Must contain key identifying fields
    assert "browser-pilot" in msg
    assert "Watch video and brief Qwen optimization" in msg
    # 30s duration shows up rounded
    assert "30" in msg
    # Status indicator (visual cue for done)
    assert "✓" in msg or "done" in msg.lower()


def test_formats_failed_task_with_error():
    task = _FakeTask(status="failed", error="yt-dlp 403 Forbidden")
    msg = task_followup.format_followup(task)
    assert msg is not None
    assert "✗" in msg or "failed" in msg.lower()
    assert "yt-dlp 403 Forbidden" in msg


def test_returns_none_for_scheduled_task():
    """priority >=30 means scheduled / verifier — don't spam the operator."""
    task = _FakeTask(priority=50, agent_id="ai-researcher", title="Daily AI pulse")
    assert task_followup.format_followup(task) is None


def test_returns_none_for_verifier_priority():
    """The verification loop runs at priority=50 by convention."""
    task = _FakeTask(priority=50, agent_id="code-reviewer", title="Verify backend-dev output")
    assert task_followup.format_followup(task) is None


def test_returns_none_for_internal_reviewer_even_at_priority_20():
    """Even if a reviewer is mistakenly dispatched at p=20, suppress —
    its result isn't operator-relevant in the same way."""
    task = _FakeTask(agent_id="code-reviewer", title="Review PR")
    assert task_followup.format_followup(task) is None


def test_truncates_very_long_titles():
    """A 500-char title would blow up the chat bubble; truncate to ≤100 chars."""
    long_title = "x" * 500
    task = _FakeTask(title=long_title)
    msg = task_followup.format_followup(task)
    assert msg is not None
    # Allow some prefix overhead but assert the title-segment is bounded
    assert len(msg) < 250


def test_handles_missing_timestamps_gracefully():
    """A task that somehow lacks ended_at shouldn't crash the formatter."""
    task = _FakeTask(started_at=0.0, ended_at=0.0)
    msg = task_followup.format_followup(task)
    assert msg is not None  # Still surface — duration just shows '?'


def test_notify_pushes_to_chat_for_operator_task(monkeypatch):
    captured = []
    class _FakeChatHistory:
        def append_pair(self, op_text, jv_text):
            captured.append((op_text, jv_text))
    fake_module = type("M", (), {"_chat_history": _FakeChatHistory()})
    # The function does `from openjarvis.cli.brain_server import _chat_history`
    # so we patch the attribute on that module.
    import sys
    monkeypatch.setitem(sys.modules, "openjarvis.cli.brain_server", fake_module)

    task = _FakeTask()
    pushed = task_followup.notify_if_operator_task(task)
    assert pushed is True
    assert len(captured) == 1
    op_text, jv_text = captured[0]
    assert op_text == ""
    assert "browser-pilot" in jv_text


def test_notify_suppresses_scheduled_task(monkeypatch):
    captured = []
    class _FakeChatHistory:
        def append_pair(self, op_text, jv_text):
            captured.append((op_text, jv_text))
    fake_module = type("M", (), {"_chat_history": _FakeChatHistory()})
    import sys
    monkeypatch.setitem(sys.modules, "openjarvis.cli.brain_server", fake_module)

    task = _FakeTask(priority=50, agent_id="ai-researcher", title="Daily pulse")
    pushed = task_followup.notify_if_operator_task(task)
    assert pushed is False
    assert captured == []


def test_notify_swallows_chat_history_failure(monkeypatch):
    """If _chat_history is broken (broadcast bus down), notify must NOT
    raise — mark_finished is on the task-completion critical path."""
    class _BrokenChatHistory:
        def append_pair(self, *a, **kw):
            raise RuntimeError("bus broke")
    fake_module = type("M", (), {"_chat_history": _BrokenChatHistory()})
    import sys
    monkeypatch.setitem(sys.modules, "openjarvis.cli.brain_server", fake_module)

    task = _FakeTask()
    # No exception should escape.
    pushed = task_followup.notify_if_operator_task(task)
    assert pushed is False

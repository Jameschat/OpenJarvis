"""Tests for the channel-agnostic notification layer (#1c)."""

from __future__ import annotations

import json

import pytest

from openjarvis.tools import notify


@pytest.fixture()
def notif_home(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "_LOG_PATH", tmp_path / "notifications.jsonl")
    monkeypatch.delenv("OPENJARVIS_NOTIFY_WHATSAPP", raising=False)
    return tmp_path


class TestNotify:
    def test_pushes_to_chat_and_logs(self, notif_home, monkeypatch):
        pushed = []
        monkeypatch.setattr(notify, "_send_chat", lambda m: pushed.append(m) or True)
        result = notify.notify("lane restarted", level="warn")
        assert result["chat"] is True
        assert pushed == ["lane restarted"]
        rows = [
            json.loads(l)
            for l in notify._LOG_PATH.read_text(encoding="utf-8").splitlines()
        ]
        assert rows[-1]["message"] == "lane restarted"
        assert rows[-1]["level"] == "warn"
        assert rows[-1]["delivered"]["chat"] is True

    def test_chat_failure_still_logs(self, notif_home, monkeypatch):
        monkeypatch.setattr(notify, "_send_chat", lambda m: False)
        result = notify.notify("stack down")
        assert result["chat"] is False
        assert "stack down" in notify._LOG_PATH.read_text(encoding="utf-8")

    def test_whatsapp_skipped_unless_enabled(self, notif_home, monkeypatch):
        called = []
        monkeypatch.setattr(notify, "_send_chat", lambda m: True)
        monkeypatch.setattr(notify, "_send_whatsapp", lambda m: called.append(m) or True)
        result = notify.notify("hello")
        assert "whatsapp" not in result
        assert called == []

    def test_whatsapp_attempted_when_enabled(self, notif_home, monkeypatch):
        called = []
        monkeypatch.setenv("OPENJARVIS_NOTIFY_WHATSAPP", "1")
        monkeypatch.setattr(notify, "_send_chat", lambda m: True)
        monkeypatch.setattr(notify, "_send_whatsapp", lambda m: called.append(m) or True)
        result = notify.notify("hello")
        assert result["whatsapp"] is True
        assert called == ["hello"]

    def test_never_raises(self, notif_home, monkeypatch):
        monkeypatch.setattr(
            notify, "_send_chat",
            lambda m: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        result = notify.notify("anything")
        assert result["chat"] is False


class TestWatchdogNotifies:
    def _kwargs(self, tmp_path, monkeypatch, *, probe_result=None, healthy=True):
        import time as _time
        from openjarvis.tools import runtime_watchdog

        health = (
            {"ok": True, "summary": "ready", "required_down": [], "services": []}
            if healthy
            else {"ok": False, "summary": "blocked: qwen", "required_down": ["qwen_fast_lane"], "services": []}
        )
        monkeypatch.setenv("OPENJARVIS_WATCHDOG_PROBE_MINUTES", "30")
        return dict(
            check_health=lambda: health,
            app_running=lambda: True,
            restart_stack=lambda: None,
            restart_lane=lambda: None,
            report_path=tmp_path / "watchdog.json",
            probe_state_path=tmp_path / "probe_state.json",
            probe_lane=(lambda: probe_result) if probe_result else None,
        )

    def test_stack_restart_sends_notification(self, tmp_path, monkeypatch):
        from openjarvis.tools import runtime_watchdog

        sent = []
        kwargs = self._kwargs(tmp_path, monkeypatch, healthy=False)
        runtime_watchdog.run_watchdog(
            notifier=lambda msg, **kw: sent.append(msg), **kwargs
        )
        assert len(sent) == 1
        assert "restart" in sent[0].lower()

    def test_lane_selfheal_sends_notification(self, tmp_path, monkeypatch):
        from openjarvis.tools import runtime_watchdog

        sent = []
        kwargs = self._kwargs(
            tmp_path, monkeypatch,
            probe_result={"ok": False, "garbled": True, "content": "3333", "error": None},
        )
        runtime_watchdog.run_watchdog(
            notifier=lambda msg, **kw: sent.append(msg), **kwargs
        )
        assert len(sent) == 1
        assert "garb" in sent[0].lower() or "lane" in sent[0].lower()

    def test_healthy_cycle_sends_nothing(self, tmp_path, monkeypatch):
        from openjarvis.tools import runtime_watchdog

        sent = []
        kwargs = self._kwargs(
            tmp_path, monkeypatch,
            probe_result={"ok": True, "garbled": False, "content": "size-ok", "error": None},
        )
        runtime_watchdog.run_watchdog(
            notifier=lambda msg, **kw: sent.append(msg), **kwargs
        )
        assert sent == []


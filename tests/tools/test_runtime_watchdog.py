import json
import subprocess
import sys
from pathlib import Path

import pytest

from openjarvis.tools import runtime_watchdog


def test_subprocess_calls_suppress_console_window(monkeypatch):
    # The watchdog runs every 5 min via Task Scheduler; each child process
    # (tasklist, wsl, schtasks, powershell) must launch WITHOUT a console or it
    # flashes a window on the operator's screen mid-game. Assert every
    # subprocess.run goes out with creationflags=CREATE_NO_WINDOW on Windows.
    captured: list[dict] = []

    def fake_run(*args, **kwargs):
        captured.append(kwargs)

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    monkeypatch.setattr(runtime_watchdog.subprocess, "run", fake_run)

    runtime_watchdog.desktop_app_running()  # calls tasklist every cycle
    try:
        runtime_watchdog.restart_jarvis_stack()
    except Exception:
        pass

    assert captured, "expected subprocess.run to be invoked"
    expected = runtime_watchdog._NO_WINDOW
    if sys.platform == "win32":
        assert expected == subprocess.CREATE_NO_WINDOW
        assert expected != 0
    for kwargs in captured:
        assert kwargs.get("creationflags", 0) == expected


def test_watchdog_does_nothing_when_runtime_is_healthy(tmp_path):
    calls: list[str] = []
    status = {
        "ok": True,
        "summary": "Jarvis runtime ready: required services are online.",
        "required_down": [],
        "services": [],
    }

    result = runtime_watchdog.run_watchdog(
        check_health=lambda: status,
        restart_stack=lambda: calls.append("restart"),
        report_path=tmp_path / "watchdog.json",
        app_running=lambda: True,
    )

    assert result["action"] == "none"
    assert result["restart_attempted"] is False
    assert calls == []
    saved = json.loads((tmp_path / "watchdog.json").read_text(encoding="utf-8"))
    assert saved["action"] == "none"


def test_watchdog_restarts_when_required_services_are_down(tmp_path):
    calls: list[str] = []
    status = {
        "ok": False,
        "summary": "Jarvis runtime blocked: Qwen Fast Lane",
        "required_down": ["qwen_fast_lane"],
        "services": [],
    }

    result = runtime_watchdog.run_watchdog(
        check_health=lambda: status,
        restart_stack=lambda: calls.append("restart"),
        report_path=tmp_path / "watchdog.json",
        app_running=lambda: True,
    )

    assert result["action"] == "restart_stack"
    assert result["restart_attempted"] is True
    assert calls == ["restart"]
    assert result["required_down"] == ["qwen_fast_lane"]


def test_watchdog_dry_run_reports_without_restart(tmp_path):
    calls: list[str] = []
    status = {
        "ok": False,
        "summary": "Jarvis runtime blocked: LiteLLM Proxy",
        "required_down": ["litellm_proxy"],
        "services": [],
    }

    result = runtime_watchdog.run_watchdog(
        check_health=lambda: status,
        restart_stack=lambda: calls.append("restart"),
        report_path=tmp_path / "watchdog.json",
        app_running=lambda: True,
        dry_run=True,
    )

    assert result["action"] == "would_restart_stack"
    assert result["restart_attempted"] is False
    assert calls == []


def test_watchdog_wrapper_uses_direct_venv_python():
    script = (Path(__file__).resolve().parents[2] / "scripts" / "watch-jarvis-runtime.ps1").read_text(
        encoding="utf-8"
    )

    assert ".venv\\Scripts\\python.exe" in script
    assert "openjarvis.tools.runtime_watchdog" in script
    assert "uv " not in script


def test_watchdog_install_script_registers_minute_task():
    script = (Path(__file__).resolve().parents[2] / "scripts" / "install-runtime-watchdog-task.ps1").read_text(
        encoding="utf-8"
    )

    assert "JarvisRuntimeWatchdog" in script
    assert "/SC MINUTE" in script
    assert "/MO" in script
    # Windowless launch (2026-06-14): the task runs the venv pythonw host on the
    # watchdog module directly, NOT powershell+watch-jarvis-runtime.ps1, so it
    # never flashes a console mid-game.
    assert "pythonw.exe" in script
    assert "openjarvis.tools.runtime_watchdog" in script


def test_watchdog_uses_runtime_timeout_that_tolerates_slow_backend():
    source = Path(runtime_watchdog.__file__).read_text(encoding="utf-8")

    assert "check_runtime_health(timeout_s=5.0)" in source


HEALTHY = {
    "ok": True,
    "summary": "Jarvis runtime ready: required services are online.",
    "required_down": [],
    "services": [],
}


def _probe_kwargs(tmp_path, monkeypatch, *, probe_result, last_probe_age_min=None):
    """Common run_watchdog kwargs for correctness-probe tests."""
    import time as _time

    state = tmp_path / "probe_state.json"
    if last_probe_age_min is not None:
        state.write_text(
            json.dumps({"last_probe_at": _time.time() - last_probe_age_min * 60}),
            encoding="utf-8",
        )
    monkeypatch.setenv("OPENJARVIS_WATCHDOG_PROBE_MINUTES", "30")
    return dict(
        check_health=lambda: HEALTHY,
        app_running=lambda: True,
        restart_stack=lambda: None,
        report_path=tmp_path / "watchdog.json",
        probe_lane=lambda: probe_result,
        probe_state_path=state,
    )


def test_probe_garbled_restarts_qwen_lane(tmp_path, monkeypatch):
    lane_calls: list[str] = []
    kwargs = _probe_kwargs(
        tmp_path, monkeypatch,
        probe_result={"ok": False, "garbled": True, "content": "3333", "error": None},
    )
    result = runtime_watchdog.run_watchdog(
        restart_lane=lambda: lane_calls.append("lane"), **kwargs
    )
    assert result["action"] == "restart_qwen_lane"
    assert lane_calls == ["lane"]
    assert result["probe"]["garbled"] is True


def test_probe_ok_no_restart_and_state_updated(tmp_path, monkeypatch):
    lane_calls: list[str] = []
    kwargs = _probe_kwargs(
        tmp_path, monkeypatch,
        probe_result={"ok": True, "garbled": False, "content": "size-ok", "error": None},
    )
    result = runtime_watchdog.run_watchdog(
        restart_lane=lambda: lane_calls.append("lane"), **kwargs
    )
    assert result["action"] == "none"
    assert lane_calls == []
    state = json.loads((tmp_path / "probe_state.json").read_text(encoding="utf-8"))
    assert state["last_probe_at"] > 0


def test_probe_error_does_not_restart(tmp_path, monkeypatch):
    # busy/timeout lane is NOT corruption - health path owns down lanes
    lane_calls: list[str] = []
    kwargs = _probe_kwargs(
        tmp_path, monkeypatch,
        probe_result={"ok": False, "garbled": False, "content": "", "error": "timeout"},
    )
    result = runtime_watchdog.run_watchdog(
        restart_lane=lambda: lane_calls.append("lane"), **kwargs
    )
    assert result["action"] == "none"
    assert lane_calls == []


def test_probe_not_due_is_skipped(tmp_path, monkeypatch):
    probed: list[str] = []
    kwargs = _probe_kwargs(
        tmp_path, monkeypatch,
        probe_result={"ok": True, "garbled": False, "content": "size-ok", "error": None},
        last_probe_age_min=5,
    )
    kwargs["probe_lane"] = lambda: probed.append("probe") or {"ok": True, "garbled": False}
    result = runtime_watchdog.run_watchdog(restart_lane=lambda: None, **kwargs)
    assert probed == []
    assert result["probe"] is None


def test_probe_disabled_via_env(tmp_path, monkeypatch):
    probed: list[str] = []
    kwargs = _probe_kwargs(
        tmp_path, monkeypatch,
        probe_result={"ok": True, "garbled": False, "content": "size-ok", "error": None},
    )
    monkeypatch.setenv("OPENJARVIS_WATCHDOG_PROBE_MINUTES", "0")
    kwargs["probe_lane"] = lambda: probed.append("probe") or {"ok": True, "garbled": False}
    runtime_watchdog.run_watchdog(restart_lane=lambda: None, **kwargs)
    assert probed == []


def test_probe_skipped_when_stack_unhealthy(tmp_path, monkeypatch):
    probed: list[str] = []
    kwargs = _probe_kwargs(
        tmp_path, monkeypatch,
        probe_result={"ok": True, "garbled": False, "content": "size-ok", "error": None},
    )
    kwargs["check_health"] = lambda: {
        "ok": False, "summary": "blocked", "required_down": ["qwen_fast_lane"], "services": [],
    }
    kwargs["probe_lane"] = lambda: probed.append("probe") or {"ok": True, "garbled": False}
    runtime_watchdog.run_watchdog(restart_lane=lambda: None, **kwargs)
    assert probed == []


def test_probe_garbled_dry_run_reports_without_restart(tmp_path, monkeypatch):
    lane_calls: list[str] = []
    kwargs = _probe_kwargs(
        tmp_path, monkeypatch,
        probe_result={"ok": False, "garbled": True, "content": "////", "error": None},
    )
    result = runtime_watchdog.run_watchdog(
        restart_lane=lambda: lane_calls.append("lane"), dry_run=True, **kwargs
    )
    assert result["action"] == "would_restart_qwen_lane"
    assert lane_calls == []


class TestDesktopAppGate:
    """Self-heal only while the operator has the desktop app open (2026-06-13)."""

    def _down_health(self):
        return {"ok": False, "summary": "blocked", "required_down": ["qwen_fast_lane"], "services": []}

    def test_skips_everything_when_app_not_running(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENJARVIS_WATCHDOG_ALWAYS", raising=False)
        probed: list[str] = []
        restarted: list[str] = []
        result = runtime_watchdog.run_watchdog(
            check_health=lambda: probed.append("health") or self._down_health(),
            restart_stack=lambda: restarted.append("stack"),
            restart_lane=lambda: restarted.append("lane"),
            report_path=tmp_path / "watchdog.json",
            probe_state_path=tmp_path / "probe.json",
            app_running=lambda: False,
        )
        assert result["action"] == "skipped_no_app"
        assert probed == []          # no health probe at all
        assert restarted == []
        assert result["ok"] is True  # not-an-error: operator parked Jarvis

    def test_heals_when_app_running(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENJARVIS_WATCHDOG_ALWAYS", raising=False)
        restarted: list[str] = []
        result = runtime_watchdog.run_watchdog(
            check_health=self._down_health,
            restart_stack=lambda: restarted.append("stack"),
            report_path=tmp_path / "watchdog.json",
            probe_state_path=tmp_path / "probe.json",
            app_running=lambda: True,
        )
        assert result["action"] == "restart_stack"
        assert restarted == ["stack"]

    def test_always_env_bypasses_gate(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENJARVIS_WATCHDOG_ALWAYS", "1")
        restarted: list[str] = []
        result = runtime_watchdog.run_watchdog(
            check_health=self._down_health,
            restart_stack=lambda: restarted.append("stack"),
            report_path=tmp_path / "watchdog.json",
            probe_state_path=tmp_path / "probe.json",
            app_running=lambda: False,
        )
        assert result["action"] == "restart_stack"
        assert restarted == ["stack"]

    def test_detection_failure_fails_closed(self, tmp_path, monkeypatch):
        # broken detection must NOT heal (operator priority: never disturb
        # the machine when Jarvis is parked) - it reports the failure instead
        monkeypatch.delenv("OPENJARVIS_WATCHDOG_ALWAYS", raising=False)
        result = runtime_watchdog.run_watchdog(
            check_health=self._down_health,
            restart_stack=lambda: None,
            report_path=tmp_path / "watchdog.json",
            probe_state_path=tmp_path / "probe.json",
            app_running=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert result["action"] == "skipped_no_app"


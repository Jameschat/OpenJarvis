import json
from pathlib import Path

from openjarvis.tools import runtime_watchdog


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
    assert "watch-jarvis-runtime.ps1" in script

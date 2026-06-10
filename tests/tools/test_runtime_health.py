import json
from pathlib import Path

from openjarvis.tools.runtime_health import (
    check_runtime_health,
    main,
    mark_runtime_service_ready,
    summarize_runtime_health,
)

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_health_reports_core_services_with_actions(tmp_path):
    def probe(url: str, timeout_s: float = 1.5):
        del timeout_s
        if "7710/studio/ping" in url:
            return True, 200, "ok"
        if "4000/health/liveliness" in url:
            return False, None, "connection refused"
        if "8084/health" in url:
            return True, 200, "ok"
        if "8082/health" in url:
            return False, None, "connection refused"
        if "11434/api/tags" in url:
            return True, 200, "ok"
        raise AssertionError(url)

    status = check_runtime_health(
        probe=probe,
        qwen_status_path=tmp_path / "missing-runtime-status.json",
    )

    services = {service["id"]: service for service in status["services"]}
    assert status["ok"] is False
    assert services["jarvis_backend"]["ok"] is True
    assert services["litellm_proxy"]["ok"] is False
    assert services["qwen_fast_lane"]["ok"] is True
    assert services["qwen_beellama_lane"]["ok"] is False
    assert services["qwen_beellama_lane"]["required"] is False
    assert services["ollama"]["ok"] is True
    assert services["qwen_runtime_status"]["ok"] is False
    assert "Start LiteLLM" in services["litellm_proxy"]["action"]
    assert "Run a benchmark" in services["qwen_runtime_status"]["action"]


def test_runtime_health_summary_names_down_services(tmp_path):
    status = check_runtime_health(
        probe=lambda *_args, **_kwargs: (False, None, "down"),
        qwen_status_path=tmp_path / "missing.json",
    )

    summary = summarize_runtime_health(status)

    assert "blocked" in summary.lower()
    assert "Jarvis Backend" in summary
    assert "LiteLLM Proxy" in summary


def test_runtime_health_uses_studio_ping_for_backend_probe(tmp_path):
    seen: list[str] = []

    def probe(url: str, timeout_s: float = 1.5):
        del timeout_s
        seen.append(url)
        return True, 200, "ok"

    status = check_runtime_health(
        probe=probe,
        qwen_status_path=tmp_path / "runtime.json",
    )

    services = {service["id"]: service for service in status["services"]}
    assert services["jarvis_backend"]["ok"] is True
    assert "jarvis_backend" not in status["required_down"]
    assert "http://127.0.0.1:7710/studio/ping" in seen
    assert not any("/auth/check" in url for url in seen)


def test_studio_state_marks_self_backend_probe_ready():
    status = {
        "ok": False,
        "required_down": ["jarvis_backend", "qwen_fast_lane"],
        "summary": "Jarvis runtime blocked: Jarvis Backend, Qwen Fast Lane",
        "services": [
            {
                "id": "jarvis_backend",
                "label": "Jarvis Backend",
                "required": True,
                "ok": False,
                "status_code": None,
                "detail": "timed out",
                "action": "restart",
            },
            {
                "id": "qwen_fast_lane",
                "label": "Qwen Fast Lane",
                "required": True,
                "ok": False,
            },
        ],
    }

    mark_runtime_service_ready(
        status,
        "jarvis_backend",
        detail="studio state request succeeded",
    )

    services = {service["id"]: service for service in status["services"]}
    assert services["jarvis_backend"]["ok"] is True
    assert services["jarvis_backend"]["detail"] == "studio state request succeeded"
    assert services["jarvis_backend"]["action"] == ""
    assert status["required_down"] == ["qwen_fast_lane"]
    assert status["ok"] is False
    assert status["summary"] == "Jarvis runtime blocked: Qwen Fast Lane"


def test_runtime_health_cli_outputs_json(tmp_path, capsys):
    status_path = tmp_path / "runtime.json"
    status_path.write_text(json.dumps({"active_lane": "x", "lanes": []}), encoding="utf-8")

    exit_code = main(["--json", "--qwen-status-path", str(status_path)])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code in {0, 1}
    assert "services" in payload
    assert any(service["id"] == "jarvis_backend" for service in payload["services"])


def test_runtime_health_script_invokes_module():
    script = (ROOT / "scripts" / "check-jarvis-runtime.ps1").read_text(
        encoding="utf-8"
    )

    assert "openjarvis.tools.runtime_health" in script
    assert "--json" in script
    assert "UV_CACHE_DIR" in script


def test_gitignore_keeps_runtime_traces_package_packagable():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "\n/traces/\n" in f"\n{gitignore}\n"
    assert "\ntraces/\n" not in f"\n{gitignore}\n"

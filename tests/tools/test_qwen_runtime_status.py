import json
from pathlib import Path

from openjarvis.tools.qwen_runtime_status import (
    load_qwen_runtime_status,
    main,
    qwen_runtime_status_from_benchmark_results,
    save_qwen_runtime_status,
    write_qwen_runtime_status_from_benchmark_file,
)

ROOT = Path(__file__).resolve().parents[2]


def test_qwen_runtime_status_defaults_include_current_active_lane():
    status = load_qwen_runtime_status(port_checker=lambda _port: False)

    assert status["active_lane"] == "wsl-mtp-froggeric"
    assert status["active_alias"] == "qwen3.6-27b-local"
    assert status["active_online"] is False


def test_qwen_runtime_status_includes_node_identity(monkeypatch):
    monkeypatch.setenv("JARVIS_NODE_ROLE", "worker")
    monkeypatch.setenv("JARVIS_NODE_ID", "worker-gpu")
    monkeypatch.setenv("JARVIS_WORKER_MODEL", "qwen3.6-35b-a3b-rotorquant")
    monkeypatch.setenv("JARVIS_WORKER_REPO", "Jameschat/OpenJarvis3090")

    status = load_qwen_runtime_status(port_checker=lambda _port: False)

    assert status["node"] == {
        "role": "worker",
        "node_id": "worker-gpu",
        "is_worker": True,
        "worker_model": "qwen3.6-35b-a3b-rotorquant",
        "worker_repo": "Jameschat/OpenJarvis3090",
    }


def test_qwen_runtime_status_defaults_include_remote_35b_worker():
    seen = []

    def checker(port, host="127.0.0.1"):
        seen.append((host, port))
        return host == "192.168.1.191" and port == 4000

    status = load_qwen_runtime_status(
        path=Path("definitely-missing-qwen-status.json"),
        port_checker=checker,
    )
    remote = next(lane for lane in status["lanes"] if lane["id"] == "remote-35b-a3b")

    assert remote["alias"] == "qwen3.6-35b-a3b-remote"
    assert remote["host"] == "192.168.1.191"
    assert remote["port"] == 4000
    assert remote["online"] is True
    assert ("192.168.1.191", 4000) in seen


def test_qwen_runtime_status_defaults_include_current_lane_metadata():
    status = load_qwen_runtime_status(
        path=Path("definitely-missing-qwen-status.json"),
        port_checker=lambda _port: False,
    )

    assert "do not promote vLLM" in status["promotion_verdict"]
    active = next(lane for lane in status["lanes"] if lane["id"] == "wsl-mtp-froggeric")
    assert active["context_tokens"] == 16384
    assert {lane["id"] for lane in status["lanes"]} >= {
        "wsl-mtp-froggeric",
        "vllm-int4-mtp",
        "rotorquant-35b-a3b",
        "remote-35b-a3b",
    }


def test_qwen_runtime_status_loads_json_override(tmp_path):
    path = tmp_path / "runtime-status.json"
    path.write_text(
        json.dumps(
            {
                "active_lane": "rotorquant-35b-a3b",
            "promotion_verdict": "Promote after verified 128K context benchmark.",
                "lanes": [
                    {
                        "id": "rotorquant-35b-a3b",
                        "label": "35B-A3B RotorQuant",
                        "alias": "qwen3.6-35b-a3b-rotorquant",
                        "port": 8085,
                        "role": "active",
                        "context_tokens": 128000,
                        "benchmark": {"short_tok_s": 142.0},
                        "verdict": "candidate",
                        "notes": "Updated by benchmark harness.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    status = load_qwen_runtime_status(path=path, port_checker=lambda port: port == 8085)

    assert status["active_lane"] == "rotorquant-35b-a3b"
    assert status["active_alias"] == "qwen3.6-35b-a3b-rotorquant"
    assert status["active_online"] is True
    assert status["promotion_verdict"] == "Promote after verified 128K context benchmark."
    assert status["lanes"][0]["online"] is True
    assert status["lanes"][0]["benchmark"]["short_tok_s"] == 142.0
    assert any(lane["id"] == "remote-35b-a3b" for lane in status["lanes"])


def test_qwen_runtime_status_falls_back_on_bad_json(tmp_path):
    path = tmp_path / "runtime-status.json"
    path.write_text("{not json", encoding="utf-8")

    status = load_qwen_runtime_status(path=path, port_checker=lambda _port: False)

    assert status["active_lane"] == "wsl-mtp-froggeric"
    assert status["lanes"][0]["id"] == "wsl-mtp-froggeric"


def test_save_qwen_runtime_status_writes_loadable_json(tmp_path):
    path = tmp_path / "nested" / "runtime-status.json"
    saved = save_qwen_runtime_status(
        {
            "active_lane": "test-lane",
            "promotion_verdict": "Use test lane.",
            "lanes": [
                {
                    "id": "test-lane",
                    "label": "Test Lane",
                    "alias": "qwen-test",
                    "port": 8123,
                    "role": "active",
                    "context_tokens": 8192,
                    "benchmark": {"short_tok_s": 99.9},
                    "verdict": "keep",
                    "notes": "Saved from benchmark.",
                }
            ],
        },
        path=path,
    )

    assert saved == path
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["active_lane"] == "test-lane"
    assert loaded["lanes"][0]["benchmark"]["short_tok_s"] == 99.9


def test_runtime_status_from_benchmark_results_updates_known_lanes():
    status = qwen_runtime_status_from_benchmark_results(
        [
            {
                "runtime": "wsl-turboq-mtp:8084",
                "ok": True,
                "seconds": 2.2,
                "tokens": 168,
                "tokens_per_second": 76.31,
            },
            {
                "runtime": "wsl-rotorquant-35b-a3b:8085",
                "ok": False,
                "seconds": 900,
                "tokens": 0,
                "tokens_per_second": 0,
                "error": "timeout",
            },
        ]
    )

    lanes = {lane["id"]: lane for lane in status["lanes"]}
    assert status["active_lane"] == "wsl-mtp-froggeric"
    assert lanes["wsl-mtp-froggeric"]["benchmark"]["latest_tok_s"] == 76.31
    assert lanes["wsl-mtp-froggeric"]["last_result_ok"] is True
    assert lanes["rotorquant-35b-a3b"]["last_result_ok"] is False
    assert lanes["rotorquant-35b-a3b"]["last_error"] == "timeout"


def test_write_runtime_status_from_benchmark_file(tmp_path):
    results_path = tmp_path / "benchmark.json"
    status_path = tmp_path / "qwen-runtime-status.json"
    results_path.write_text(
        json.dumps(
            [
                {
                    "runtime": "vllm-int4-mtp:8086",
                    "ok": True,
                    "seconds": 3.0,
                    "tokens": 120,
                    "tokens_per_second": 40.0,
                }
            ]
        ),
        encoding="utf-8",
    )

    written = write_qwen_runtime_status_from_benchmark_file(
        results_path,
        status_path=status_path,
    )

    assert written == status_path
    status = load_qwen_runtime_status(path=status_path, port_checker=lambda _port: False)
    lane = next(lane for lane in status["lanes"] if lane["id"] == "vllm-int4-mtp")
    assert lane["benchmark"]["latest_tok_s"] == 40.0


def test_write_runtime_status_accepts_powershell_utf8_bom(tmp_path):
    results_path = tmp_path / "benchmark-bom.json"
    status_path = tmp_path / "qwen-runtime-status.json"
    results_path.write_text(
        "\ufeff"
        + json.dumps(
            [
                {
                    "runtime": "wsl-turboq-mtp:8084",
                    "ok": True,
                    "seconds": 1.87,
                    "tokens": 143,
                    "tokens_per_second": 76.47,
                }
            ]
        ),
        encoding="utf-8",
    )

    write_qwen_runtime_status_from_benchmark_file(
        results_path,
        status_path=status_path,
    )

    status = load_qwen_runtime_status(path=status_path, port_checker=lambda _port: False)
    lane = next(lane for lane in status["lanes"] if lane["id"] == "wsl-mtp-froggeric")
    assert lane["benchmark"]["latest_tok_s"] == 76.47


def test_write_runtime_status_accepts_single_benchmark_object(tmp_path):
    results_path = tmp_path / "single-benchmark.json"
    status_path = tmp_path / "qwen-runtime-status.json"
    results_path.write_text(
        json.dumps(
            {
                "runtime": "wsl-turboq-mtp:8084",
                "ok": True,
                "seconds": 1.87,
                "tokens": 143,
                "tokens_per_second": 76.47,
            }
        ),
        encoding="utf-8",
    )

    write_qwen_runtime_status_from_benchmark_file(
        results_path,
        status_path=status_path,
    )

    status = load_qwen_runtime_status(path=status_path, port_checker=lambda _port: False)
    lane = next(lane for lane in status["lanes"] if lane["id"] == "wsl-mtp-froggeric")
    assert lane["benchmark"]["latest_tok_s"] == 76.47


def test_qwen_runtime_status_cli_writes_status_file(tmp_path):
    results_path = tmp_path / "benchmark.json"
    status_path = tmp_path / "qwen-runtime-status.json"
    results_path.write_text(
        json.dumps(
            [
                {
                    "runtime": "wsl-turboq-mtp:8084",
                    "ok": True,
                    "seconds": 1.5,
                    "tokens": 90,
                    "tokens_per_second": 60.0,
                }
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--benchmark-results",
            str(results_path),
            "--status-path",
            str(status_path),
        ]
    )

    assert exit_code == 0
    status = json.loads(status_path.read_text(encoding="utf-8"))
    lane = next(lane for lane in status["lanes"] if lane["id"] == "wsl-mtp-froggeric")
    assert lane["benchmark"]["latest_tok_s"] == 60.0


def test_benchmark_script_can_update_studio_runtime_status():
    script = (ROOT / "scripts" / "benchmark-qwen-runtimes.ps1").read_text(
        encoding="utf-8"
    )

    assert "UpdateStudioStatus" in script
    assert "openjarvis.tools.qwen_runtime_status" in script
    assert "--benchmark-results" in script

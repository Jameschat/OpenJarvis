import json

from openjarvis.tools.qwen_runtime_status import (
    load_qwen_runtime_status,
    save_qwen_runtime_status,
)


def test_qwen_runtime_status_defaults_include_current_active_lane():
    status = load_qwen_runtime_status(port_checker=lambda _port: False)

    assert status["active_lane"] == "wsl-mtp-froggeric"
    assert status["active_alias"] == "qwen3.6-27b-local"
    assert status["active_online"] is False
    assert "do not promote vLLM" in status["promotion_verdict"]
    assert {lane["id"] for lane in status["lanes"]} >= {
        "wsl-mtp-froggeric",
        "vllm-int4-mtp",
        "rotorquant-35b-a3b",
    }


def test_qwen_runtime_status_loads_json_override(tmp_path):
    path = tmp_path / "runtime-status.json"
    path.write_text(
        json.dumps(
            {
                "active_lane": "rotorquant-35b-a3b",
                "promotion_verdict": "Promote after verified 200K context benchmark.",
                "lanes": [
                    {
                        "id": "rotorquant-35b-a3b",
                        "label": "35B-A3B RotorQuant",
                        "alias": "qwen3.6-35b-a3b-rotorquant",
                        "port": 8085,
                        "role": "active",
                        "context_tokens": 200000,
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
    assert status["promotion_verdict"] == "Promote after verified 200K context benchmark."
    assert status["lanes"][0]["online"] is True
    assert status["lanes"][0]["benchmark"]["short_tok_s"] == 142.0


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

"""Qwen runtime benchmark status for Jarvis Studio."""

from __future__ import annotations

import json
import os
import socket
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


DEFAULT_QWEN_RUNTIME_STATUS_PATH = (
    Path.home() / ".openjarvis" / "qwen_runtime_status.json"
)


_DEFAULT_STATUS: dict[str, Any] = {
    "active_lane": "wsl-mtp-froggeric",
    "promotion_verdict": "Keep WSL MTP/Froggeric on 8084; do not promote vLLM yet.",
    "lanes": [
        {
            "id": "wsl-mtp-froggeric",
            "label": "WSL MTP Froggeric",
            "alias": "qwen3.6-27b-local",
            "port": 8084,
            "role": "active",
            "context_tokens": 16384,
            "benchmark": {
                "short_tok_s": 58.34,
                "studio_json_tok_s": 76.31,
                "tool_xml_tok_s": 58.44,
            },
            "verdict": "keep",
            "notes": "Current Jarvis lane; best Studio planning result in latest run.",
        },
        {
            "id": "vllm-int4-mtp",
            "label": "vLLM INT4 MTP",
            "alias": "qwen3.6-27b-vllm",
            "port": 8086,
            "role": "experimental",
            "context_tokens": 32768,
            "benchmark": {
                "short_tok_s": 40.03,
                "studio_json_tok_s": 34.26,
                "tool_xml_tok_s": 61.04,
            },
            "verdict": "reject",
            "notes": "32K works only with CUDA graphs disabled; 200K did not fit on 24GB.",
        },
        {
            "id": "rotorquant-35b-a3b",
            "label": "35B-A3B RotorQuant",
            "alias": "qwen3.6-35b-a3b-rotorquant",
            "port": 8085,
            "role": "prototype",
            "context_tokens": 200000,
            "benchmark": {
                "short_tok_s": 154.69,
                "long_completion_tok_s": 11.46,
                "long_total_tok_s": 6223.0,
            },
            "verdict": "hold",
            "notes": "Very fast short output, but speculative decoding was not active.",
        },
    ],
}

_BENCHMARK_RUNTIME_TO_LANE_ID = {
    "wsl-turboq-mtp:8084": "wsl-mtp-froggeric",
    "wsl-mtp-froggeric": "wsl-mtp-froggeric",
    "vllm-int4-mtp:8086": "vllm-int4-mtp",
    "vllm-int4-mtp": "vllm-int4-mtp",
    "wsl-rotorquant-35b-a3b:8085": "rotorquant-35b-a3b",
    "rotorquant-35b-a3b": "rotorquant-35b-a3b",
}


def qwen_runtime_status_path() -> Path:
    configured = os.environ.get("OPENJARVIS_QWEN_RUNTIME_STATUS_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_QWEN_RUNTIME_STATUS_PATH


def port_is_open(port: int, host: str = "127.0.0.1", timeout_s: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def default_qwen_runtime_status() -> dict[str, Any]:
    return deepcopy(_DEFAULT_STATUS)


def save_qwen_runtime_status(
    status: dict[str, Any],
    *,
    path: Path | str | None = None,
) -> Path:
    target = Path(path) if path is not None else qwen_runtime_status_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(status)
    payload.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
    temp_path = target.with_name(f"{target.name}.tmp")
    temp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(target)
    return target


def qwen_runtime_status_from_benchmark_results(
    results: list[dict[str, Any]],
    *,
    base_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = deepcopy(base_status) if base_status is not None else default_qwen_runtime_status()
    lanes = status.get("lanes")
    if not isinstance(lanes, list):
        status = default_qwen_runtime_status()
        lanes = status["lanes"]

    lanes_by_id = {
        str(lane.get("id")): lane
        for lane in lanes
        if isinstance(lane, dict) and lane.get("id")
    }
    for result in results:
        if not isinstance(result, dict):
            continue
        runtime = str(result.get("runtime") or "")
        lane_id = _BENCHMARK_RUNTIME_TO_LANE_ID.get(runtime)
        if not lane_id:
            continue
        lane = lanes_by_id.get(lane_id)
        if not lane:
            continue
        benchmark = lane.setdefault("benchmark", {})
        if not isinstance(benchmark, dict):
            benchmark = {}
            lane["benchmark"] = benchmark
        ok = bool(result.get("ok"))
        benchmark["latest_tok_s"] = _round_float(result.get("tokens_per_second"))
        benchmark["latest_seconds"] = _round_float(result.get("seconds"))
        benchmark["latest_tokens"] = int(result.get("tokens") or 0)
        lane["last_result_ok"] = ok
        lane["last_benchmarked_at"] = datetime.now(timezone.utc).isoformat()
        if ok:
            lane.pop("last_error", None)
        else:
            lane["last_error"] = str(result.get("error") or "benchmark failed")[:500]

    status["updated_at"] = datetime.now(timezone.utc).isoformat()
    return status


def write_qwen_runtime_status_from_benchmark_file(
    benchmark_results_path: Path | str,
    *,
    status_path: Path | str | None = None,
) -> Path:
    results_path = Path(benchmark_results_path)
    data = json.loads(results_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("benchmark results must be a JSON list")
    status = qwen_runtime_status_from_benchmark_results(data)
    return save_qwen_runtime_status(status, path=status_path)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Update Jarvis Studio Qwen runtime status from benchmark JSON."
    )
    parser.add_argument("--benchmark-results", required=True)
    parser.add_argument("--status-path", default="")
    args = parser.parse_args(argv)

    written = write_qwen_runtime_status_from_benchmark_file(
        args.benchmark_results,
        status_path=args.status_path or None,
    )
    print(str(written))
    return 0


def load_qwen_runtime_status(
    *,
    path: Path | str | None = None,
    port_checker: Callable[[int], bool] | None = None,
) -> dict[str, Any]:
    status = _load_status_file(Path(path) if path is not None else qwen_runtime_status_path())
    return load_qwen_runtime_status_from_data(status, port_checker=port_checker)


def _round_float(value: Any) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def load_qwen_runtime_status_from_data(
    status: dict[str, Any],
    *,
    port_checker: Callable[[int], bool] | None = None,
) -> dict[str, Any]:
    checker = port_checker or port_is_open
    lanes = status.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        status = default_qwen_runtime_status()
        lanes = status["lanes"]

    normalized_lanes: list[dict[str, Any]] = []
    for raw_lane in lanes:
        if not isinstance(raw_lane, dict):
            continue
        lane = dict(raw_lane)
        try:
            port = int(lane.get("port", 0))
        except (TypeError, ValueError):
            port = 0
        lane["online"] = checker(port) if port > 0 else False
        normalized_lanes.append(lane)

    if not normalized_lanes:
        return load_qwen_runtime_status_from_data(
            default_qwen_runtime_status(),
            port_checker=port_checker,
        )

    active_lane_id = str(status.get("active_lane") or "")
    active = next(
        (lane for lane in normalized_lanes if lane.get("id") == active_lane_id),
        None,
    )
    if active is None:
        active = next(
            (lane for lane in normalized_lanes if lane.get("role") == "active"),
            normalized_lanes[0],
        )
        active_lane_id = str(active.get("id") or "")

    return {
        "active_lane": active_lane_id,
        "active_alias": active.get("alias", ""),
        "active_online": bool(active.get("online")),
        "promotion_verdict": status.get(
            "promotion_verdict",
            _DEFAULT_STATUS["promotion_verdict"],
        ),
        "lanes": normalized_lanes,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _load_status_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return default_qwen_runtime_status()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_qwen_runtime_status()
    if not isinstance(data, dict):
        return default_qwen_runtime_status()
    return data


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

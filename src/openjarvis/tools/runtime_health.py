"""Runtime health checks for the local Jarvis Studio stack."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from openjarvis.tools.qwen_runtime_status import qwen_runtime_status_path

Probe = Callable[[str, float], tuple[bool, int | None, str]]


_SERVICES = [
    {
        "id": "jarvis_backend",
        "label": "Jarvis Backend",
        "url": "http://127.0.0.1:7710/auth/check",
        "required": True,
        "ok_status_codes": [200, 401],
        "action": "Start or restart E:\\Claude\\OpenJarvis\\jarvis.bat.",
    },
    {
        "id": "litellm_proxy",
        "label": "LiteLLM Proxy",
        "url": "http://127.0.0.1:4000/health/liveliness",
        "required": True,
        "action": "Start LiteLLM with configs\\litellm.yaml or restart jarvis.bat.",
    },
    {
        "id": "qwen_fast_lane",
        "label": "Qwen Fast Lane",
        "url": "http://127.0.0.1:8084/health",
        "required": True,
        "action": "Run scripts\\start-qwen-mtp-froggeric-wsl.ps1.",
    },
    {
        "id": "ollama",
        "label": "Ollama Fallback",
        "url": "http://127.0.0.1:11434/api/tags",
        "required": False,
        "action": "Start Ollama if local fallback is needed.",
    },
]


def check_runtime_health(
    *,
    probe: Probe | None = None,
    qwen_status_path: Path | str | None = None,
    timeout_s: float = 1.5,
) -> dict[str, Any]:
    probe_fn = probe or _probe_url
    services: list[dict[str, Any]] = []
    for service in _SERVICES:
        ok, status_code, detail = probe_fn(str(service["url"]), timeout_s)
        if status_code in service.get("ok_status_codes", [200]):
            ok = True
        services.append(
            {
                "id": service["id"],
                "label": service["label"],
                "url": service["url"],
                "required": service["required"],
                "ok": ok,
                "status_code": status_code,
                "detail": detail,
                "action": "" if ok else service["action"],
            }
        )

    status_path = Path(qwen_status_path) if qwen_status_path is not None else qwen_runtime_status_path()
    runtime_status_ok = status_path.exists()
    services.append(
        {
            "id": "qwen_runtime_status",
            "label": "Qwen Runtime Status",
            "url": str(status_path),
            "required": False,
            "ok": runtime_status_ok,
            "status_code": None,
            "detail": "status file present" if runtime_status_ok else "status file missing",
            "action": ""
            if runtime_status_ok
            else "Run a benchmark with scripts\\benchmark-qwen-runtimes.ps1 -UpdateStudioStatus.",
        }
    )

    required_down = [
        service for service in services if service.get("required") and not service.get("ok")
    ]
    return {
        "ok": not required_down,
        "required_down": [service["id"] for service in required_down],
        "summary": summarize_runtime_health({"services": services}),
        "services": services,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def summarize_runtime_health(status: dict[str, Any]) -> str:
    services = status.get("services", [])
    down_required = [
        service.get("label", service.get("id", "service"))
        for service in services
        if service.get("required") and not service.get("ok")
    ]
    if not down_required:
        return "Jarvis runtime ready: required services are online."
    return "Jarvis runtime blocked: " + ", ".join(str(label) for label in down_required)


def _probe_url(url: str, timeout_s: float) -> tuple[bool, int | None, str]:
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            status_code = int(getattr(response, "status", 200))
            body = response.read(512).decode("utf-8", errors="replace")
            return 200 <= status_code < 400, status_code, body[:160]
    except urllib.error.HTTPError as exc:
        return False, exc.code, str(exc)[:160]
    except Exception as exc:
        return False, None, str(exc)[:160]


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Check the local Jarvis runtime stack.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--qwen-status-path", default="")
    args = parser.parse_args(argv)

    status = check_runtime_health(
        qwen_status_path=args.qwen_status_path or None,
    )
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        print(status["summary"])
        for service in status["services"]:
            state = "ok" if service["ok"] else "down"
            print(f"- {service['label']}: {state}")
            if service.get("action"):
                print(f"  action: {service['action']}")
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

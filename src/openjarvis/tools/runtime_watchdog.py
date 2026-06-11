"""Self-healing watchdog for the local Jarvis runtime stack."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from openjarvis.tools.runtime_health import check_runtime_health

HealthCheck = Callable[[], dict[str, Any]]
RestartStack = Callable[[], None]


def default_report_path() -> Path:
    return Path.home() / ".openjarvis" / "runtime_watchdog_last.json"


def restart_jarvis_stack() -> None:
    result = subprocess.run(
        ["schtasks", "/Run", "/TN", "JarvisStudioStack"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "schtasks failed").strip()
        raise RuntimeError(detail)


def run_watchdog(
    *,
    check_health: HealthCheck | None = None,
    restart_stack: RestartStack | None = None,
    report_path: Path | str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    health = (check_health or (lambda: check_runtime_health(timeout_s=1.5)))()
    required_down = list(health.get("required_down") or [])
    unhealthy = bool(required_down)
    action = "none"
    restart_attempted = False
    error = ""

    if unhealthy:
        if dry_run:
            action = "would_restart_stack"
        else:
            action = "restart_stack"
            restart_attempted = True
            try:
                (restart_stack or restart_jarvis_stack)()
            except Exception as exc:  # pragma: no cover - exercised via result shape
                error = str(exc)

    result = {
        "ok": not unhealthy,
        "action": action,
        "restart_attempted": restart_attempted,
        "required_down": required_down,
        "summary": health.get("summary", ""),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "error": error,
        "health": health,
    }

    path = Path(report_path) if report_path is not None else default_report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Watch and repair the Jarvis runtime stack.")
    parser.add_argument("--json", action="store_true", help="print JSON result")
    parser.add_argument("--dry-run", action="store_true", help="report action without restarting")
    parser.add_argument("--report-path", default="", help="path to write the watchdog report")
    args = parser.parse_args(argv)

    result = run_watchdog(
        dry_run=args.dry_run,
        report_path=args.report_path or None,
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result["summary"] or "Jarvis runtime watchdog completed.")
        print(f"action: {result['action']}")
        if result.get("error"):
            print(f"error: {result['error']}")
    return 0 if not result.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""Self-healing watchdog for the local Jarvis runtime stack."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from openjarvis.tools.runtime_health import check_runtime_health

HealthCheck = Callable[[], dict[str, Any]]
RestartStack = Callable[[], None]

# Correctness probe (roadmap #1d, 2026-06-12 incident): the MTP lane can
# silently degrade into ////-style garbage on long prompts while /health
# stays green and clocks are stock. A periodic long-prompt probe catches
# it; a lane restart (~10s from the ext4 model copy) fully recovers it.
_DEFAULT_PROBE_MINUTES = 30
_QWEN_LANE_URL = "http://127.0.0.1:8084/v1"


def default_report_path() -> Path:
    return Path.home() / ".openjarvis" / "runtime_watchdog_last.json"


def default_probe_state_path() -> Path:
    return Path.home() / ".openjarvis" / "runtime_watchdog_probe.json"


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


def probe_qwen_lane(base_url: str = _QWEN_LANE_URL) -> dict[str, Any]:
    """One 700-word long-prompt correctness probe against the fast lane.
    Returns {ok, garbled, content, error}. An error (busy/timeout/refused)
    is NOT corruption — only garbled output is; down lanes are the health
    path's job."""
    from openjarvis.tools import lane_promotion

    try:
        results = lane_promotion.probe_long_prompts(
            base_url, "qwen", sizes=(700,), timeout=120
        )
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "garbled": False, "content": "", "error": str(exc)}
    p = results[0]
    garbled = bool(p.error is None and not p.ok)
    return {"ok": p.ok, "garbled": garbled, "content": p.content, "error": p.error}


def restart_qwen_lane() -> None:
    """Kill the WSL llama-server and relaunch via the canonical lane script.
    Cheap since the ext4 model move (~10s to healthy)."""
    subprocess.run(
        ["wsl", "-d", "JarvisUbuntu", "--", "pkill", "-f", "llama-server"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    script = Path(__file__).resolve().parents[3] / "scripts" / "start-qwen-mtp-froggeric-wsl.ps1"
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "lane start script failed").strip()
        raise RuntimeError(detail)


def _watchdog_always() -> bool:
    return os.environ.get("OPENJARVIS_WATCHDOG_ALWAYS", "0").strip().lower() in (
        "1",
        "true",
        "on",
    )


def desktop_app_running() -> bool:
    """True when the Jarvis desktop app is open (operator gate, 2026-06-13).

    Self-healing only runs while the operator actually has the app open —
    when it's closed (gaming, away), the watchdog must leave the machine
    alone. Detection failure fails CLOSED for the same reason. Headless
    deployments opt back into always-heal via OPENJARVIS_WATCHDOG_ALWAYS=1.
    """
    # NOTE: do NOT add "jarvis.exe" here — it substring-matches the backend's
    # own .venv\Scripts\jarvis.exe serve process in tasklist, making the stack
    # keep itself alive forever (false positive found live 2026-06-13).
    names = os.environ.get(
        "OPENJARVIS_WATCHDOG_APP_NAMES",
        "openjarvis-desktop.exe",
    )
    targets = [n.strip().lower() for n in names.split(",") if n.strip()]
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout.lower()
    except Exception:
        return False
    return any(t in out for t in targets)


def _probe_interval_minutes() -> int:
    try:
        raw = int(os.environ.get("OPENJARVIS_WATCHDOG_PROBE_MINUTES", str(_DEFAULT_PROBE_MINUTES)))
    except ValueError:
        raw = _DEFAULT_PROBE_MINUTES
    return max(0, raw)


def _probe_due(state_path: Path, interval_min: int) -> bool:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        last = float(state.get("last_probe_at") or 0)
    except (OSError, ValueError):
        last = 0.0
    return (time.time() - last) >= interval_min * 60


def _record_probe(state_path: Path, probe: dict[str, Any]) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({"last_probe_at": time.time(), "last_result": probe}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def run_watchdog(
    *,
    check_health: HealthCheck | None = None,
    restart_stack: RestartStack | None = None,
    report_path: Path | str | None = None,
    dry_run: bool = False,
    probe_lane: Callable[[], dict[str, Any]] | None = None,
    restart_lane: RestartStack | None = None,
    probe_state_path: Path | str | None = None,
    notifier: Callable[..., Any] | None = None,
    app_running: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    def _notify(message: str, level: str = "warn") -> None:
        """Operator notice for self-heal actions (#1c). Best-effort."""
        try:
            if notifier is not None:
                notifier(message, level=level)
            else:
                from openjarvis.tools import notify

                notify.notify(message, level=level)
        except Exception:  # pragma: no cover - notification must never break healing
            pass

    # Desktop-app gate (2026-06-13): self-heal ONLY while the operator has
    # the Jarvis desktop app open. Closed app = parked Jarvis = hands off.
    if not _watchdog_always():
        gated = False
        try:
            gated = not (app_running or desktop_app_running)()
        except Exception:
            gated = True  # fail closed — never disturb a parked machine
        if gated:
            result = {
                "ok": True,
                "action": "skipped_no_app",
                "restart_attempted": False,
                "required_down": [],
                "summary": "Jarvis desktop app not running — watchdog parked.",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "error": "",
                "probe": None,
                "health": None,
            }
            path = Path(report_path) if report_path is not None else default_report_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
            return result

    health = (check_health or (lambda: check_runtime_health(timeout_s=5.0)))()
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
                _notify(
                    f"⚠ Jarvis stack down ({', '.join(required_down)}) — watchdog triggered a restart."
                )
            except Exception as exc:  # pragma: no cover - exercised via result shape
                error = str(exc)
                _notify(f"✗ Watchdog stack restart FAILED: {error[:140]}", level="error")

    # Correctness probe (#1d): only when the stack is otherwise healthy —
    # a down/restarting lane is the health path's job, not corruption.
    probe: Optional[dict[str, Any]] = None
    interval_min = _probe_interval_minutes()
    state_path = (
        Path(probe_state_path) if probe_state_path is not None else default_probe_state_path()
    )
    if not unhealthy and interval_min > 0 and _probe_due(state_path, interval_min):
        probe = (probe_lane or probe_qwen_lane)()
        _record_probe(state_path, probe)
        if probe.get("garbled"):
            if dry_run:
                action = "would_restart_qwen_lane"
            else:
                action = "restart_qwen_lane"
                restart_attempted = True
                try:
                    (restart_lane or restart_qwen_lane)()
                    _notify(
                        "♻ Qwen lane returned garbled output on the correctness probe — "
                        "watchdog restarted the lane (~10s)."
                    )
                except Exception as exc:  # pragma: no cover - exercised via result shape
                    error = str(exc)
                    _notify(f"✗ Watchdog lane restart FAILED: {error[:140]}", level="error")

    result = {
        "ok": not unhealthy and not (probe or {}).get("garbled"),
        "action": action,
        "restart_attempted": restart_attempted,
        "required_down": required_down,
        "summary": health.get("summary", ""),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "error": error,
        "probe": probe,
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

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

# The watchdog runs every 5 min via Task Scheduler in the operator's interactive
# session. Console-subsystem children (tasklist/wsl/schtasks/powershell) would
# each pop a visible window — a flash on screen mid-game. CREATE_NO_WINDOW (no-op
# off Windows) suppresses it. Pair with a windowless host (pythonw) in the task.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

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
        creationflags=_NO_WINDOW,
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


_COHERENCE_PROMPT = "Say hello in five words."


def coherence_probe_qwen_lane(base_url: str = _QWEN_LANE_URL) -> dict[str, Any]:
    """Cheap per-cycle coherence probe: a tiny fixed prompt to the lane, checked
    for the degenerate signature (a long repeated-character run like '333…', empty
    output, or single-char-dominated). The lane can degrade at runtime into pure
    garbage for ANY prompt while /health and /v1/models still return 200 — the
    700-word correctness probe above catches the long-prompt MTP degradation but
    only runs every ~30 min and is too costly to run each cycle. This one is cheap
    enough to run EVERY watchdog cycle, so that failure self-heals within one cycle
    instead of lingering until manual reload. An error (down/busy/timeout) is NOT
    corruption — a down lane is the health path's job."""
    import urllib.request

    from openjarvis.tools.lane_promotion import looks_garbled

    body = json.dumps(
        {
            "model": "qwen",
            "messages": [{"role": "user", "content": _COHERENCE_PROMPT}],
            "max_tokens": 24,
            "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False},
        }
    ).encode("utf-8")
    try:
        req = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": "Bearer sk-noop"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        choices = data.get("choices") or []
        msg = (choices[0].get("message") or {}) if choices else {}
        content = (msg.get("content") or msg.get("reasoning_content") or "").strip()
    except Exception as exc:  # down/busy/timeout — not corruption
        return {"ok": False, "garbled": False, "content": "", "error": str(exc)}
    garbled = looks_garbled(content)
    return {
        "ok": (not garbled) and bool(content),
        "garbled": garbled,
        "content": content,
        "error": None,
    }


def _coherence_enabled() -> bool:
    return os.environ.get("OPENJARVIS_WATCHDOG_COHERENCE", "1").strip().lower() not in (
        "0",
        "false",
        "off",
    )


def restart_qwen_lane() -> None:
    """Kill the WSL llama-server and relaunch the DEFAULT 35B-A3B lane.
    Cheap from the ext4 model copy (~10s to healthy).

    Uses the Qwen3.6-35B-A3B lane (plain MoE, 256K) — the default local lane:
    fastest local decode + best reasoner, stable (no MTP; MTP is net-negative on
    A3B). pkill -9 because a gentle pkill can't clear a hung llama-server.
    """
    subprocess.run(
        ["wsl", "-d", "JarvisUbuntu", "--", "pkill", "-9", "-f", "llama-server"],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=_NO_WINDOW,
    )
    script = Path(__file__).resolve().parents[3] / "scripts" / "start-qwen3.6-35b-a3b-wsl.ps1"
    result = subprocess.run(
        [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
            "-Port", "8084", "-ContextTokens", "262144",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        creationflags=_NO_WINDOW,
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
            creationflags=_NO_WINDOW,
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
    coherence_probe: Callable[[], dict[str, Any]] | None = None,
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

    # Cheap coherence probe — runs EVERY cycle (when the stack is otherwise
    # healthy and we haven't already restarted). Catches the runtime degeneration
    # where the lane emits repeated-character garbage ('333…') for ANY prompt
    # while /health stays green; the 30-min long-prompt probe below is too slow
    # and costly for this. Garbled output here → restart the lane now.
    coherence: Optional[dict[str, Any]] = None
    if not unhealthy and not restart_attempted and _coherence_enabled():
        coherence = (coherence_probe or coherence_probe_qwen_lane)()
        if coherence.get("garbled"):
            if dry_run:
                action = "would_restart_qwen_lane"
            else:
                action = "restart_qwen_lane"
                restart_attempted = True
                try:
                    (restart_lane or restart_qwen_lane)()
                    _notify(
                        "♻ Qwen lane emitted degenerate output on the coherence probe "
                        "(HTTP 200 but garbage) — watchdog restarted the lane (~10s)."
                    )
                except Exception as exc:  # pragma: no cover - exercised via result shape
                    error = str(exc)
                    _notify(f"✗ Watchdog lane restart FAILED: {error[:140]}", level="error")

    # Correctness probe (#1d): only when the stack is otherwise healthy —
    # a down/restarting lane is the health path's job, not corruption.
    probe: Optional[dict[str, Any]] = None
    interval_min = _probe_interval_minutes()
    state_path = (
        Path(probe_state_path) if probe_state_path is not None else default_probe_state_path()
    )
    if not unhealthy and not restart_attempted and interval_min > 0 and _probe_due(state_path, interval_min):
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
        "ok": not unhealthy
        and not (probe or {}).get("garbled")
        and not (coherence or {}).get("garbled"),
        "action": action,
        "restart_attempted": restart_attempted,
        "required_down": required_down,
        "summary": health.get("summary", ""),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "error": error,
        "probe": probe,
        "coherence": coherence,
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

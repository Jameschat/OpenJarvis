"""CodeGraph live-sync trigger.

Keeps the project-local CodeGraph index fresh during Jarvis sessions without
blocking vault writes, voice turns, or agent task completion. Best-effort:
sync failures are logged and must never propagate to the caller.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_last_fired_at: float = 0.0
_state_lock = threading.Lock()


def reset_for_tests() -> None:
    global _last_fired_at
    with _state_lock:
        _last_fired_at = 0.0


def _repo_root() -> Path:
    return Path(os.environ.get("OPENJARVIS_REPO_ROOT", r"E:\Claude\OpenJarvis"))


def _codegraph_cmd() -> Path:
    env = os.environ.get("OPENJARVIS_CODEGRAPH_CMD", "").strip()
    if env:
        return Path(env)
    name = "codegraph.cmd" if os.name == "nt" else "codegraph"
    return Path.home() / ".openjarvis" / "tools" / "codegraph-0.8.0" / "node_modules" / ".bin" / name


def _call_sync() -> dict:
    repo = _repo_root()
    cmd = _codegraph_cmd()
    if not cmd.exists():
        return {"started": False, "reason": f"codegraph command not found: {cmd}"}
    env = os.environ.copy()
    env["CODEGRAPH_NO_WATCH"] = "1"
    proc = subprocess.run(
        [str(cmd), "sync", str(repo)],
        cwd=str(repo),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    return {
        "started": True,
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-1000:],
        "stderr_tail": proc.stderr[-1000:],
    }


def _run_sync_quietly() -> None:
    try:
        result = _call_sync()
        if result.get("ok") or result.get("started") is False:
            logger.debug("codegraph_trigger: sync result=%s", result)
        else:
            logger.warning("codegraph_trigger: sync failed result=%s", result)
    except Exception:
        logger.exception("codegraph_trigger: sync failed (non-fatal)")


def maybe_sync_after_change(
    *,
    now: Optional[float] = None,
    cooldown_seconds: float = 120.0,
) -> bool:
    """Queue a CodeGraph sync if the cooldown has elapsed.

    Async via daemon thread; returns True if a sync was queued.
    """
    global _last_fired_at
    if now is None:
        now = time.time()
    with _state_lock:
        if now - _last_fired_at < cooldown_seconds:
            return False
        _last_fired_at = now
    t = threading.Thread(target=_run_sync_quietly, daemon=True)
    t.start()
    return True


__all__ = ["maybe_sync_after_change", "reset_for_tests"]

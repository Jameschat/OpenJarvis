"""Graphify-on-demand trigger.

Hooked into agent_runner.mark_finished — fires `graphify_bridge.refresh()`
in a daemon thread with a 60-second debounce so department-dispatch
bursts don't thrash. Best-effort: failures must NEVER propagate to the
caller because mark_finished is on the task-completion critical path.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


# Module-level state — survives across agent tasks within one process.
# Reset on jarvis restart, which is itself an implicit "rebuild graph"
# moment so we don't lose anything.
_last_fired_at: float = 0.0
_state_lock = threading.Lock()


def reset_for_tests() -> None:
    """Reset module state — called by autouse pytest fixture."""
    global _last_fired_at
    with _state_lock:
        _last_fired_at = 0.0


def _call_refresh():
    """Indirection layer so tests can monkey-patch the actual refresh."""
    from openjarvis.cli.graphify_bridge import refresh
    return refresh()


def _run_refresh_quietly() -> None:
    """Body of the daemon thread. Swallow all exceptions — the caller
    (mark_finished) must never see them."""
    try:
        result = _call_refresh()
        logger.debug("graphify_trigger: refresh result=%s", result)
    except Exception:
        logger.exception("graphify_trigger: refresh failed (non-fatal)")


def maybe_refresh_after_task(
    *,
    now: Optional[float] = None,
    cooldown_seconds: float = 60.0,
) -> bool:
    """Trigger a graphify rebuild if we haven't fired within the cooldown.

    Returns True if a refresh was queued, False if skipped.
    Async — the actual graphify call runs in a daemon thread so this
    function returns within milliseconds.
    """
    global _last_fired_at
    if now is None:
        now = time.time()
    with _state_lock:
        if now - _last_fired_at < cooldown_seconds:
            return False
        _last_fired_at = now
    t = threading.Thread(target=_run_refresh_quietly, daemon=True)
    t.start()
    return True


__all__ = ["maybe_refresh_after_task", "reset_for_tests"]

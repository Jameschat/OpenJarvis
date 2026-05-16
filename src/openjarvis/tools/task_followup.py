"""Operator-task completion follow-up.

When an operator-voice-spawned agent task finishes, push a one-line
notice to the HUD chat panel so the operator knows the task completed
and where to find the result.

Hooked into agent_runner.mark_finished. Filters on task.priority == 20
(operator-voice; scheduled tasks at priority ≥30 stay silent). Uses
_chat_history.append_pair (the same path voice_cmd uses for chat-bubble
broadcasts).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Operator-voice fast-paths dispatch with this priority. Anything higher
# is scheduled/verifier work that runs without operator awareness — those
# completions go in the L-1 outcome log, not the chat panel.
_OPERATOR_PRIORITY = 20

# Internal-loop agents whose completion isn't useful operator-feedback.
# Even if mistakenly dispatched at operator priority, suppress their
# follow-up — they're noise.
_SUPPRESSED_AGENTS = frozenset({
    "code-reviewer",
    "qa-engineer",
    "verifier",
})

# Maximum title length before truncation.
_MAX_TITLE_CHARS = 100


def _duration_str(task: Any) -> str:
    """Format duration like '30s' or '4m 12s'. Returns '?' for missing data."""
    started = getattr(task, "started_at", None) or 0.0
    ended = getattr(task, "ended_at", None) or 0.0
    if started <= 0 or ended <= 0 or ended < started:
        return "?"
    secs = int(ended - started)
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m {secs % 60:02d}s"


def format_followup(task: Any) -> Optional[str]:
    """Return a HUD-ready chat-panel message, or None to suppress.

    Suppressed when:
      - task.priority != 20 (scheduled / verifier — operator wasn't waiting)
      - task.agent_id is an internal loop agent (code-reviewer etc.)
    """
    if getattr(task, "priority", None) != _OPERATOR_PRIORITY:
        return None
    agent_id = str(getattr(task, "agent_id", "") or "")
    if agent_id in _SUPPRESSED_AGENTS:
        return None
    title = str(getattr(task, "title", "") or "(untitled task)")
    if len(title) > _MAX_TITLE_CHARS:
        title = title[: _MAX_TITLE_CHARS - 1] + "…"
    duration = _duration_str(task)
    status = str(getattr(task, "status", "") or "").lower()
    error = getattr(task, "error", None)
    if status == "done":
        return f"✓ {agent_id}: {title} — done in {duration}."
    err_str = (str(error) if error else "no error captured")[:140]
    return f"✗ {agent_id}: {title} — failed in {duration}: {err_str}"


def push_to_chat(message: str) -> bool:
    """Push the message to the HUD chat panel via _chat_history.

    Returns True on push, False on any failure. Best-effort — never raises.
    Lazy-imports brain_server so unit tests can monkey-patch without
    pulling in HTTP-server side effects.
    """
    if not message:
        return False
    try:
        from openjarvis.cli.brain_server import _chat_history
    except Exception:
        logger.debug("task_followup: _chat_history import failed (non-fatal)", exc_info=True)
        return False
    try:
        _chat_history.append_pair("", message)
    except Exception:
        logger.debug("task_followup: append_pair failed (non-fatal)", exc_info=True)
        return False
    return True


def notify_if_operator_task(task: Any) -> bool:
    """One-shot helper for the mark_finished hook. Returns True if a
    message was pushed, False otherwise (suppressed or failed)."""
    msg = format_followup(task)
    if msg is None:
        return False
    return push_to_chat(msg)


__all__ = ["format_followup", "push_to_chat", "notify_if_operator_task"]

"""Self-improvement approval inbox (Phase 7 #6).

The nightly pipeline already runs capability-gap recording → scout →
queue, producing advisory reports nobody acts on. This inbox makes the
queue actionable while keeping the operator as the approval key:

- ``list_inbox()``      — latest queue items merged with operator decisions
- ``approve(cap)``      — queues a sandboxed qwen-builder PROTOTYPE task
- ``dismiss(cap)``      — marks the item handled without action
- ``briefing_section()``— pending items for the morning briefing

Approval grants a *prototype in the existing sandbox/verify lane*, not an
install: the queued task goes to the local qwen-builder whose tool bridge
already blocks shell/install/direct edits. Decisions persist per
capability at ``~/.openjarvis/learning/capability_inbox.json``, so the
same capability reappearing in tomorrow's queue keeps its status.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_STATE_PATH = Path.home() / ".openjarvis" / "learning" / "capability_inbox.json"

_PROTOTYPE_AGENT = "qwen-builder"


def _queue_root() -> Path:
    from openjarvis.tools import capability_queue

    return capability_queue._queue_root()


def _registry():
    from openjarvis.tools import agent_runner

    return agent_runner._reg


def _load_state() -> Dict[str, Any]:
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {"decisions": {}}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        logger.exception("capability_inbox: state write failed")


def _latest_queue() -> Optional[Dict[str, Any]]:
    root = _queue_root()
    if not root.exists():
        return None
    for day in sorted((d for d in root.iterdir() if d.is_dir()), reverse=True):
        path = day / "queue.json"
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload.setdefault("date", day.name)
                return payload
            except (OSError, ValueError):
                continue
    return None


def list_inbox(limit: int = 10) -> Dict[str, Any]:
    """Latest queue items, each annotated with the operator's decision
    status: pending | approved | dismissed (+ task_id when approved)."""
    queue = _latest_queue()
    if not queue:
        return {"date": None, "items": []}
    decisions = _load_state().get("decisions", {})
    items: List[Dict[str, Any]] = []
    for raw in (queue.get("items") or [])[: max(1, limit)]:
        cap = raw.get("capability") or ""
        decision = decisions.get(cap, {})
        item = dict(raw)
        item["status"] = decision.get("status", "pending")
        if decision.get("task_id"):
            item["task_id"] = decision["task_id"]
        items.append(item)
    return {"date": queue.get("date"), "items": items}


def _prototype_prompt(item: Dict[str, Any]) -> str:
    scout = item.get("scout") or {}
    rec = scout.get("recommendation") or "no scout recommendation"
    return (
        f"PROTOTYPE TASK (operator-approved from the capability inbox).\n\n"
        f"Capability gap: {item.get('capability')}\n"
        f"Trigger: {item.get('trigger') or 'unknown'}\n"
        f"Why now: {item.get('reason') or ''}\n"
        f"Scout recommendation: {rec} (score {scout.get('score', 'n/a')})\n\n"
        "Build a SMALL working prototype inside your isolated task workspace "
        "that demonstrates this capability, using the scout recommendation as "
        "the starting point. Use the sandbox/verify lane for any check runs. "
        "You have no shell/install/direct-edit authority — propose files via "
        "the normal bridge tools and end with DONE plus a short evaluation of "
        "whether the approach is worth adopting."
    )


def approve(capability: str) -> Dict[str, Any]:
    """Queue a sandboxed prototype task for the item. Idempotent — a second
    approve returns the existing task id instead of queueing again."""
    state = _load_state()
    existing = state["decisions"].get(capability, {})
    if existing.get("status") == "approved" and existing.get("task_id"):
        return {"ok": True, "task_id": existing["task_id"], "already": True}

    inbox = list_inbox(limit=50)
    item = next(
        (i for i in inbox["items"] if i.get("capability") == capability), None
    )
    if item is None:
        return {"ok": False, "error": f"unknown capability: {capability!r}"}

    try:
        task_id = _registry().add_task(
            f"[inbox] prototype: {capability}"[:120],
            _PROTOTYPE_AGENT,
            _prototype_prompt(item),
        )
    except Exception as exc:
        logger.exception("capability_inbox: approve failed to queue task")
        return {"ok": False, "error": str(exc)}

    state["decisions"][capability] = {
        "status": "approved",
        "task_id": task_id,
        "ts": time.time(),
    }
    _save_state(state)
    return {"ok": True, "task_id": task_id}


def dismiss(capability: str) -> Dict[str, Any]:
    state = _load_state()
    state["decisions"][capability] = {"status": "dismissed", "ts": time.time()}
    _save_state(state)
    return {"ok": True}


def briefing_section(limit: int = 5) -> Optional[str]:
    """Pending inbox items for the morning briefing. None when empty."""
    inbox = list_inbox(limit=50)
    pending = [i for i in inbox["items"] if i.get("status") == "pending"]
    if not pending:
        return None
    lines = [
        f"{len(pending)} capability prototype(s) pending approval "
        f"(queue {inbox.get('date')}):"
    ]
    for i in pending[:limit]:
        lines.append(
            f"- **{i.get('capability')}** (priority {i.get('priority')}, "
            f"{i.get('occurrences', '?')}x) — {i.get('reason', '')}"
        )
    lines.append(
        "Approve: `POST /capability/inbox/approve {\"capability\": \"<name>\"}` "
        "— queues a sandboxed qwen-builder prototype."
    )
    return "\n".join(lines)


__all__ = ["list_inbox", "approve", "dismiss", "briefing_section"]

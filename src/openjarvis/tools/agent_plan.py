"""Multi-step plan persistence for agent-team projects.

Autonomy-improvement #2 (2026-04-27).

Background: when the planner LLM (gpt-4o-mini in tool_use.py) decomposes
a user request into multiple ``dispatch_agent`` calls under one
``project_id``, today there's no record that those dispatches belong to
a coordinated plan — each is a fire-and-forget. This module adds an
optional persistent plan-graph per project so:

    * Future user turns can ask "where are we on project X?" and get a
      real answer (current step, completed steps, what's blocked).
    * The planner can call ``get_plan`` at the start of a turn and
      ``advance_plan`` to dispatch the next ready step instead of
      re-deciding the breakdown from scratch.
    * Any agent (or the operator) can read ``plan.json`` directly to
      understand its place in the larger workflow.

Storage: one JSON file per project at
``~/.openjarvis/agents/projects/<project_id>/plan.json``. A
human-readable ``plan.md`` mirror is regenerated on every mutation.
Both live alongside the existing project workspace so an Obsidian
user (or anyone with `ls`) sees them naturally.

Concurrency: per-project filesystem lock + in-process lock cache + atomic
read-modify-write. No mutable shared in-memory plan state — the chat-
memory revert taught us that lesson. Every mutator reads fresh from
disk, mutates, atomic-replaces.

Backwards compatibility: if no ``plan.json`` exists for a project, every
helper returns None / no-op. Existing single-shot ``dispatch_agent``
calls (without a plan) continue to work unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Paths + constants
# ---------------------------------------------------------------------------

# Defer import of agent_runner.PROJECTS_DIR so this module can be loaded
# in isolation (e.g. from tests). Match the same default location.
def _projects_dir() -> Path:
    try:
        from openjarvis.tools.agent_runner import PROJECTS_DIR
        return PROJECTS_DIR
    except Exception:
        return Path.home() / ".openjarvis" / "agents" / "projects"


SCHEMA_VERSION = 1
MAX_STEPS = 50              # soft limit — long-running projects should split
PLAN_FILENAME = "plan.json"
PLAN_MD_FILENAME = "plan.md"
PLAN_LOCK_FILENAME = "plan.lock"

VALID_PROJECT_STATUSES = ("pending", "in_progress", "done", "failed", "abandoned")
VALID_STEP_STATUSES = ("pending", "running", "done", "failed", "skipped", "blocked")

# project_id sanitisation pattern — same shape as tool_use.py uses for
# dispatch_agent's project_id arg. Single source of truth.
_PROJECT_ID_RE = re.compile(r"^[a-z0-9._-]{1,60}$")


class PlanValidationError(ValueError):
    """Raised when plan input fails validation. Caller should surface
    the message to the LLM/operator so they can fix the input."""


# ---------------------------------------------------------------------------
# Per-plan locking
# ---------------------------------------------------------------------------

# In-process lock keyed by project_id. Threads in the same process
# serialise here BEFORE contending for the FS lock; avoids flock spin.
_proc_locks: Dict[str, threading.Lock] = {}
_proc_locks_master = threading.Lock()


def _proc_lock(project_id: str) -> threading.Lock:
    with _proc_locks_master:
        lk = _proc_locks.get(project_id)
        if lk is None:
            lk = threading.Lock()
            _proc_locks[project_id] = lk
        return lk


class _PlanLock:
    """Cross-process file lock + in-process lock for a single project's
    plan file. Use as a context manager.

    Falls back to in-process-only if the FS doesn't support flock
    (rare — e.g. some network mounts) and logs a single warning."""
    def __init__(self, project_id: str, timeout_s: float = 5.0) -> None:
        self.project_id = project_id
        self.timeout_s = timeout_s
        self._proc_lk = _proc_lock(project_id)
        self._fp = None
        self._fs_locked = False

    def __enter__(self) -> "_PlanLock":
        self._proc_lk.acquire()
        try:
            lock_path = _plan_dir(self.project_id) / PLAN_LOCK_FILENAME
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            self._fp = open(lock_path, "a+b")
            deadline = time.monotonic() + self.timeout_s
            while True:
                try:
                    if sys.platform == "win32":
                        import msvcrt
                        # Windows non-blocking lock attempt
                        msvcrt.locking(self._fp.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(self._fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._fs_locked = True
                    break
                except (OSError, BlockingIOError):
                    if time.monotonic() >= deadline:
                        # Couldn't acquire — fall through to in-process-only.
                        # Safe because no other thread in this process can
                        # hold the proc lock; only risk is another process
                        # also writing concurrently.
                        logger.warning(
                            "agent_plan: FS lock timeout for %s — "
                            "proceeding with in-process lock only",
                            self.project_id,
                        )
                        break
                    time.sleep(0.05)
        except Exception:
            # FS unsupported / weird path — log once and proceed in-proc only
            logger.warning(
                "agent_plan: FS lock unavailable for %s — in-process only",
                self.project_id, exc_info=True,
            )
            self._fs_locked = False
            if self._fp is not None:
                try: self._fp.close()
                except Exception: pass
                self._fp = None
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._fp is not None:
                try:
                    if self._fs_locked:
                        if sys.platform == "win32":
                            import msvcrt
                            msvcrt.locking(self._fp.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(self._fp.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                try: self._fp.close()
                except Exception: pass
        finally:
            self._proc_lk.release()


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _validate_pid(project_id: str) -> str:
    pid = (project_id or "").strip().lower()
    if not _PROJECT_ID_RE.match(pid):
        raise PlanValidationError(
            f"invalid project_id {project_id!r} — must match {_PROJECT_ID_RE.pattern}"
        )
    if pid in (".", "..") or set(pid) == {"."}:
        raise PlanValidationError(f"invalid project_id {project_id!r}")
    return pid


def _plan_dir(project_id: str) -> Path:
    pid = _validate_pid(project_id)
    return _projects_dir() / pid


def plan_path(project_id: str) -> Path:
    """Return the canonical path to a project's plan.json (whether or
    not it exists). Validates the project_id."""
    return _plan_dir(project_id) / PLAN_FILENAME


def plan_md_path(project_id: str) -> Path:
    return _plan_dir(project_id) / PLAN_MD_FILENAME


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _detect_cycles(steps: List[Dict[str, Any]]) -> List[str]:
    """Return step ids participating in a dependency cycle, [] if none.
    Simple DFS with white/grey/black colouring."""
    by_id = {s["id"]: s for s in steps}
    WHITE, GREY, BLACK = 0, 1, 2
    color = {sid: WHITE for sid in by_id}
    cycle_members: set = set()

    def dfs(sid: str, stack: List[str]) -> None:
        color[sid] = GREY
        for dep in by_id[sid].get("depends_on") or []:
            if dep not in by_id:
                continue
            if color[dep] == GREY:
                # Found cycle — everything from dep to end of stack
                if dep in stack:
                    idx = stack.index(dep)
                    cycle_members.update(stack[idx:])
                else:
                    cycle_members.add(dep)
                cycle_members.add(sid)
                continue
            if color[dep] == WHITE:
                dfs(dep, stack + [sid])
        color[sid] = BLACK

    for sid in list(by_id.keys()):
        if color[sid] == WHITE:
            dfs(sid, [])
    return sorted(cycle_members)


def _known_agent_ids() -> set:
    try:
        from openjarvis.tools.agent_runner import DEFAULT_AGENTS
        return {a["id"] for a in DEFAULT_AGENTS}
    except Exception:
        return set()


def _known_departments() -> Dict[str, str]:
    """Pull the department -> head_id table from agent_runner. Lazy
    import so agent_plan stays importable in environments where
    agent_runner is unavailable (returns empty dict and the dept
    feature is silently disabled)."""
    try:
        from openjarvis.tools.agent_runner import DEPT_TO_HEAD
        return dict(DEPT_TO_HEAD)
    except Exception:
        return {}


def _validate_steps(steps: List[Dict[str, Any]]) -> None:
    if not isinstance(steps, list) or not steps:
        raise PlanValidationError("steps must be a non-empty list")
    if len(steps) > MAX_STEPS:
        raise PlanValidationError(f"too many steps ({len(steps)}); soft cap is {MAX_STEPS}")
    seen_ids: set = set()
    valid_agents = _known_agent_ids()
    dept_to_head = _known_departments()
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            raise PlanValidationError(f"step {i}: must be a dict")
        for required in ("id", "title", "prompt"):
            if not s.get(required):
                raise PlanValidationError(f"step {i}: missing required field {required!r}")
        sid = s["id"]
        if sid in seen_ids:
            raise PlanValidationError(f"duplicate step id {sid!r}")
        seen_ids.add(sid)
        # Phase-3 plan-aware orchestration (2026-04-28): a step may
        # specify EITHER `agent` (a registered agent_id) OR `department`
        # (a slug from agent_runner.DEPT_TO_HEAD). When `department` is
        # set we auto-fill `agent` with the head's id so downstream
        # dispatch is unchanged. The `department` field is preserved on
        # disk for display + intent — plan.md mirrors it, and
        # plan_summary surfaces it.
        has_agent = bool(s.get("agent"))
        has_dept = bool(s.get("department"))
        if not has_agent and not has_dept:
            raise PlanValidationError(
                f"step {sid!r}: must specify either 'agent' (a registered "
                f"agent_id) or 'department' (one of: "
                f"{sorted(dept_to_head.keys()) if dept_to_head else 'unavailable'})"
            )
        if has_dept:
            dep = s["department"]
            if dept_to_head and dep not in dept_to_head:
                raise PlanValidationError(
                    f"step {sid!r}: unknown department {dep!r}; "
                    f"valid: {sorted(dept_to_head.keys())}"
                )
            # Auto-resolve to the head's agent_id if `agent` not set.
            # If both are set, `agent` wins (operator was explicit) and
            # `department` becomes display-only metadata.
            if not has_agent and dept_to_head:
                s["agent"] = dept_to_head[dep]
                has_agent = True
        if has_agent and valid_agents and s["agent"] not in valid_agents:
            raise PlanValidationError(
                f"step {sid!r}: unknown agent {s['agent']!r}; "
                f"valid: {sorted(valid_agents)}"
            )
        deps = s.get("depends_on") or []
        if not isinstance(deps, list):
            raise PlanValidationError(f"step {sid!r}: depends_on must be a list")
    # Cross-step: every depends_on must reference a real step
    for s in steps:
        for dep in s.get("depends_on") or []:
            if dep not in seen_ids:
                raise PlanValidationError(
                    f"step {s['id']!r}: depends_on references unknown step {dep!r}"
                )
    cycles = _detect_cycles(steps)
    if cycles:
        raise PlanValidationError(f"depends_on cycle detected involving steps: {cycles}")


# ---------------------------------------------------------------------------
# Atomic JSON write
# ---------------------------------------------------------------------------

def _write_plan_atomic(project_id: str, plan: Dict[str, Any]) -> Path:
    """Write plan.json atomically (tmp + replace). Caller must hold the
    plan lock."""
    p = plan_path(project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    tmp.replace(p)
    # Best-effort markdown mirror — failures logged but never raise
    try:
        _write_plan_md(project_id, plan)
    except Exception:
        logger.exception("agent_plan: plan.md regeneration failed for %s", project_id)
    return p


def _write_plan_md(project_id: str, plan: Dict[str, Any]) -> None:
    """Regenerate plan.md from plan.json. Best-effort, non-authoritative."""
    lines = [
        f"# Plan: {project_id}",
        "",
        f"**Goal:** {plan.get('goal','')}",
        f"**Status:** `{plan.get('status','?')}`",
        f"**Created:** {datetime.fromtimestamp(plan.get('created_at',0)).isoformat(timespec='seconds')}",
        f"**Updated:** {datetime.fromtimestamp(plan.get('updated_at',0)).isoformat(timespec='seconds')}",
        "",
        "## Steps",
        "",
    ]
    status_glyph = {"pending":"☐", "running":"◐", "done":"✅",
                    "failed":"❌", "skipped":"⊘", "blocked":"⛔"}
    for s in plan.get("steps") or []:
        glyph = status_glyph.get(s.get("status","pending"), "?")
        deps = ", ".join(s.get("depends_on") or []) or "-"
        task_id = s.get("task_id") or "-"
        # Show department prefix when the step was routed via a dept
        # head (Phase 3 plan-aware orchestration). Reads as e.g.
        # "marketing → marketing-head" rather than just the head id.
        dep = s.get("department")
        agent_label = (
            f"{dep} → {s.get('agent','?')}"
            if dep else s.get('agent','?')
        )
        lines.append(
            f"- [{glyph}] **{s.get('id','?')}** · {agent_label} · "
            f"{s.get('title','')}  (deps: {deps}, task: `{task_id}`)"
        )
    plan_md_path(project_id).write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_plan(project_id: str, *, goal: str, steps: List[Dict[str, Any]],
                created_by: str = "unknown",
                overwrite: bool = False) -> Path:
    """Validate + persist a new plan. Raises PlanValidationError on bad
    input. Refuses to overwrite an existing plan unless overwrite=True."""
    pid = _validate_pid(project_id)
    if not goal or not isinstance(goal, str):
        raise PlanValidationError("goal must be a non-empty string")
    _validate_steps(steps)

    # Normalise: fill in defaults
    now = time.time()
    norm_steps = []
    for s in steps:
        norm_steps.append({
            "id":           str(s["id"]),
            "agent":        str(s["agent"]),
            # Department is optional — preserved when set so plan.md / HUD
            # can show "marketing — Draft hooks" rather than just the
            # head's id. Display-only metadata; dispatch always uses .agent.
            "department":   (str(s["department"]) if s.get("department") else None),
            "title":        str(s.get("title", ""))[:120],
            "prompt":       str(s.get("prompt", "")),
            "depends_on":   [str(d) for d in (s.get("depends_on") or [])],
            "status":       "pending",
            "task_id":      None,
            "started_at":   None,
            "ended_at":     None,
            "exit_code":    None,
            "deliverables": [],
            "notes":        None,
        })

    plan = {
        "schema_version": SCHEMA_VERSION,
        "project_id":     pid,
        "goal":           goal.strip(),
        "created_at":     now,
        "updated_at":     now,
        "status":         "pending",
        "created_by":     created_by,
        "steps":          norm_steps,
    }

    with _PlanLock(pid):
        target = plan_path(pid)
        if target.exists() and not overwrite:
            raise PlanValidationError(
                f"plan already exists for project_id {pid!r}; "
                "pass overwrite=True to replace, or use get_plan to inspect"
            )
        _write_plan_atomic(pid, plan)
    logger.info("agent_plan: created plan for %s with %d steps", pid, len(norm_steps))
    return target


def get_plan(project_id: str) -> Optional[Dict[str, Any]]:
    """Load plan.json for the given project. Returns None if missing,
    unreadable, or wrong schema version. Never raises."""
    try:
        pid = _validate_pid(project_id)
    except PlanValidationError:
        return None
    p = plan_path(pid)
    if not p.exists():
        return None
    try:
        with _PlanLock(pid):
            data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("agent_plan: plan.json corrupted for %s: %s", pid, exc)
        return None
    except Exception:
        logger.exception("agent_plan: failed to read plan for %s", pid)
        return None
    sv = data.get("schema_version", 0)
    if sv > SCHEMA_VERSION:
        logger.warning("agent_plan: plan for %s is schema_version %s, we support up to %s",
                       pid, sv, SCHEMA_VERSION)
        return None
    return data


def list_plans() -> List[Dict[str, Any]]:
    """Return summary records for every plan on disk."""
    out = []
    root = _projects_dir()
    if not root.exists():
        return out
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            pid = _validate_pid(child.name)
        except PlanValidationError:
            continue
        plan = get_plan(pid)
        if plan is None:
            continue
        steps = plan.get("steps") or []
        counts: Dict[str, int] = {}
        for s in steps:
            counts[s.get("status", "pending")] = counts.get(s.get("status", "pending"), 0) + 1
        out.append({
            "project_id": pid,
            "goal": plan.get("goal", ""),
            "status": plan.get("status", ""),
            "step_counts": counts,
            "total_steps": len(steps),
            "updated_at": plan.get("updated_at", 0),
        })
    return out


def mark_step(project_id: str, step_id: str, *,
              status: Optional[str] = None,
              task_id: Optional[str] = None,
              exit_code: Optional[int] = None,
              deliverables: Optional[List[str]] = None,
              notes: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Read-modify-write update of a single step. Returns the updated
    step dict or None if plan/step missing. Auto-stamps started_at /
    ended_at on status transitions and refreshes the project-level
    aggregate status."""
    try:
        pid = _validate_pid(project_id)
    except PlanValidationError:
        return None
    if status is not None and status not in VALID_STEP_STATUSES:
        logger.warning("agent_plan: invalid step status %r — ignoring", status)
        status = None

    with _PlanLock(pid):
        p = plan_path(pid)
        if not p.exists():
            return None
        try:
            plan = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("agent_plan: read failed in mark_step for %s", pid)
            return None

        step = next((s for s in plan.get("steps") or [] if s.get("id") == step_id), None)
        if step is None:
            return None

        now = time.time()
        if status is not None:
            old = step.get("status")
            step["status"] = status
            if status == "running" and step.get("started_at") is None:
                step["started_at"] = now
            if status in ("done", "failed", "skipped") and step.get("ended_at") is None:
                step["ended_at"] = now
            logger.info("agent_plan: %s/%s %s -> %s", pid, step_id, old, status)
        if task_id is not None:
            step["task_id"] = task_id
        if exit_code is not None:
            step["exit_code"] = int(exit_code)
        if deliverables is not None:
            step["deliverables"] = list(deliverables)[:50]
        if notes is not None:
            step["notes"] = str(notes)[:2000]

        plan["updated_at"] = now
        plan["status"] = _derive_project_status(plan["steps"])
        _write_plan_atomic(pid, plan)
        return step


def next_pending_step(project_id: str) -> Optional[Dict[str, Any]]:
    """Return the first pending step whose dependencies are all done /
    skipped. Returns None if nothing is runnable."""
    plan = get_plan(project_id)
    if not plan:
        return None
    steps = plan.get("steps") or []
    by_id = {s.get("id"): s for s in steps}
    for s in steps:
        if s.get("status") != "pending":
            continue
        deps = s.get("depends_on") or []
        if all(by_id.get(d, {}).get("status") in ("done", "skipped") for d in deps):
            return s
    return None


def plan_summary(project_id: str) -> str:
    """Spoken-friendly one-liner summarising plan progress."""
    plan = get_plan(project_id)
    if not plan:
        return f"no plan for {project_id}"
    steps = plan.get("steps") or []
    counts: Dict[str, int] = {}
    for s in steps:
        counts[s.get("status", "pending")] = counts.get(s.get("status", "pending"), 0) + 1
    total = len(steps)
    done = counts.get("done", 0)
    running = counts.get("running", 0)
    failed = counts.get("failed", 0)
    pending = counts.get("pending", 0)
    parts = [f"{project_id} — {done}/{total} done"]
    if running:
        running_step = next((s for s in steps if s.get("status") == "running"), None)
        if running_step:
            parts.append(f"running: {running_step['agent']} ({running_step.get('title','')[:40]})")
    if pending:
        nxt = next_pending_step(project_id)
        if nxt:
            parts.append(f"next: {nxt['agent']} ({nxt.get('title','')[:40]})")
    if failed:
        parts.append(f"{failed} failed")
    return ", ".join(parts)


def _derive_project_status(steps: List[Dict[str, Any]]) -> str:
    """Pure: compute project-level status from step statuses."""
    if not steps:
        return "pending"
    statuses = [s.get("status", "pending") for s in steps]
    terminal_success = ("done", "skipped")
    if all(s in terminal_success for s in statuses):
        return "done"
    if any(s == "failed" for s in statuses):
        # If anything failed but pending+runnable steps remain, in_progress
        # else failed
        if any(s == "pending" for s in statuses) or any(s == "running" for s in statuses):
            return "in_progress"
        return "failed"
    if any(s in ("running", "done") for s in statuses):
        return "in_progress"
    return "pending"


# ---------------------------------------------------------------------------
# In-process task_id -> (project_id, step_id) reverse map
# ---------------------------------------------------------------------------

_task_to_step: Dict[str, Tuple[str, str]] = {}
_task_to_step_lock = threading.Lock()


def link_task_to_step(task_id: str, project_id: str, step_id: str) -> None:
    with _task_to_step_lock:
        _task_to_step[task_id] = (project_id, step_id)


def step_for_task(task_id: str) -> Optional[Tuple[str, str]]:
    with _task_to_step_lock:
        return _task_to_step.get(task_id)


def rebuild_task_map_from_disk() -> int:
    """Walk every plan on disk and rebuild the in-process reverse map.
    Useful at startup so post-restart task completions still update plans.
    Returns the number of mappings restored."""
    n = 0
    for summary in list_plans():
        plan = get_plan(summary["project_id"])
        if not plan:
            continue
        for step in plan.get("steps") or []:
            tid = step.get("task_id")
            if tid:
                link_task_to_step(tid, plan["project_id"], step["id"])
                n += 1
    if n:
        logger.info("agent_plan: rebuilt %d task->step mappings from disk", n)
    return n


__all__ = [
    "create_plan", "get_plan", "list_plans", "mark_step",
    "next_pending_step", "plan_summary", "plan_path", "plan_md_path",
    "link_task_to_step", "step_for_task", "rebuild_task_map_from_disk",
    "PlanValidationError", "SCHEMA_VERSION", "MAX_STEPS",
]

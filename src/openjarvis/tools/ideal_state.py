"""Ideal State Criteria (ISC) persistence for Jarvis projects.

Inspired by PAI's Ideal State Criteria primitive, but implemented as a
thin layer above Jarvis' existing agent_plan module. Plans describe what
to do; ideal state criteria describe what must be true when the project
is done.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_CRITERIA = 80
IDEAL_STATE_FILENAME = "ideal_state.json"
IDEAL_STATE_MD_FILENAME = "ideal_state.md"
IDEAL_STATE_LOCK_FILENAME = "ideal_state.lock"
VALID_STATUSES = ("pending", "pass", "fail", "unknown", "skipped")
_PROJECT_ID_RE = re.compile(r"^[a-z0-9._-]{1,60}$")


class IdealStateValidationError(ValueError):
    pass


def _projects_dir() -> Path:
    try:
        from openjarvis.tools.agent_runner import PROJECTS_DIR
        return PROJECTS_DIR
    except Exception:
        return Path.home() / ".openjarvis" / "agents" / "projects"


def _validate_pid(project_id: str) -> str:
    pid = (project_id or "").strip().lower()
    if not _PROJECT_ID_RE.match(pid) or pid in (".", "..") or set(pid) == {"."}:
        raise IdealStateValidationError(f"invalid project_id {project_id!r}")
    return pid


def _plan_dir(project_id: str) -> Path:
    return _projects_dir() / _validate_pid(project_id)


def ideal_state_path(project_id: str) -> Path:
    return _plan_dir(project_id) / IDEAL_STATE_FILENAME


def ideal_state_md_path(project_id: str) -> Path:
    return _plan_dir(project_id) / IDEAL_STATE_MD_FILENAME


_proc_locks: Dict[str, threading.RLock] = {}
_proc_locks_master = threading.Lock()


def _proc_lock(project_id: str) -> threading.RLock:
    with _proc_locks_master:
        lk = _proc_locks.get(project_id)
        if lk is None:
            lk = threading.RLock()
            _proc_locks[project_id] = lk
        return lk


class _ISCLock:
    def __init__(self, project_id: str, timeout_s: float = 5.0) -> None:
        self.project_id = project_id
        self.timeout_s = timeout_s
        self._proc_lk = _proc_lock(project_id)
        self._fp = None
        self._fs_locked = False

    def __enter__(self) -> "_ISCLock":
        self._proc_lk.acquire()
        try:
            lock_path = _plan_dir(self.project_id) / IDEAL_STATE_LOCK_FILENAME
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            self._fp = open(lock_path, "a+b")
            deadline = time.monotonic() + self.timeout_s
            while True:
                try:
                    if __import__("sys").platform == "win32":
                        import msvcrt
                        msvcrt.locking(self._fp.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(self._fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._fs_locked = True
                    break
                except (OSError, BlockingIOError):
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.05)
        except Exception:
            logger.debug("ideal_state: filesystem lock unavailable", exc_info=True)
            self._fs_locked = False
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._fp is not None:
                try:
                    if self._fs_locked:
                        if __import__("sys").platform == "win32":
                            import msvcrt
                            msvcrt.locking(self._fp.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(self._fp.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                try:
                    self._fp.close()
                except Exception:
                    pass
        finally:
            self._proc_lk.release()


def _validate_criteria(criteria: List[Dict[str, Any]]) -> None:
    if not isinstance(criteria, list) or not criteria:
        raise IdealStateValidationError("criteria must be a non-empty list")
    if len(criteria) > MAX_CRITERIA:
        raise IdealStateValidationError(f"too many criteria ({len(criteria)}); cap is {MAX_CRITERIA}")
    seen = set()
    for i, c in enumerate(criteria):
        if not isinstance(c, dict):
            raise IdealStateValidationError(f"criterion {i}: must be a dict")
        cid = str(c.get("id") or "").strip()
        stmt = str(c.get("statement") or "").strip()
        if not cid:
            raise IdealStateValidationError(f"criterion {i}: missing id")
        if cid in seen:
            raise IdealStateValidationError(f"duplicate criterion id {cid!r}")
        if not re.match(r"^[a-zA-Z0-9._-]{1,40}$", cid):
            raise IdealStateValidationError(f"criterion id {cid!r} must be short and slug-like")
        if not stmt:
            raise IdealStateValidationError(f"criterion {cid!r}: missing statement")
        seen.add(cid)


def _normalise_criteria(criteria: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for c in criteria:
        out.append({
            "id": str(c["id"]).strip(),
            "statement": str(c["statement"]).strip()[:500],
            "verification_method": str(c.get("verification_method") or "").strip()[:500],
            "status": c.get("status") if c.get("status") in VALID_STATUSES else "pending",
            "evidence": str(c.get("evidence") or "").strip()[:2000],
            "linked_step_ids": [str(x) for x in (c.get("linked_step_ids") or [])][:20],
            "created_at": time.time(),
            "updated_at": time.time(),
        })
    return out


def _derive_status(criteria: List[Dict[str, Any]]) -> str:
    if not criteria:
        return "pending"
    statuses = [c.get("status", "pending") for c in criteria]
    if all(s in ("pass", "skipped") for s in statuses):
        return "pass"
    if any(s == "fail" for s in statuses):
        return "fail"
    if any(s == "unknown" for s in statuses):
        return "unknown"
    return "pending"


def _write_md(project_id: str, data: Dict[str, Any]) -> None:
    status_glyph = {"pending": "[ ]", "pass": "[x]", "fail": "[!]", "unknown": "[?]", "skipped": "[-]"}
    lines = [
        f"# Ideal State: {project_id}",
        "",
        f"**Objective:** {data.get('objective','')}",
        f"**Status:** `{data.get('status','?')}`",
        f"**Created:** {datetime.fromtimestamp(data.get('created_at',0)).isoformat(timespec='seconds')}",
        f"**Updated:** {datetime.fromtimestamp(data.get('updated_at',0)).isoformat(timespec='seconds')}",
        "",
        "## Criteria",
        "",
    ]
    for c in data.get("criteria") or []:
        glyph = status_glyph.get(c.get("status", "pending"), "[?]")
        links = ", ".join(c.get("linked_step_ids") or []) or "-"
        lines.append(f"- {glyph} **{c.get('id','?')}** - {c.get('statement','')}")
        lines.append(f"  - Verify: {c.get('verification_method') or '-'}")
        lines.append(f"  - Steps: {links}")
        if c.get("evidence"):
            lines.append(f"  - Evidence: {c.get('evidence')}")
    ideal_state_md_path(project_id).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_atomic(project_id: str, data: Dict[str, Any]) -> Path:
    p = ideal_state_path(project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(p)
    try:
        _write_md(project_id, data)
    except Exception:
        logger.exception("ideal_state: markdown mirror failed for %s", project_id)
    return p


def create_ideal_state(project_id: str, *, objective: str,
                       criteria: List[Dict[str, Any]],
                       created_by: str = "unknown",
                       overwrite: bool = False) -> Path:
    pid = _validate_pid(project_id)
    if not objective or not isinstance(objective, str):
        raise IdealStateValidationError("objective must be a non-empty string")
    _validate_criteria(criteria)
    now = time.time()
    data = {
        "schema_version": SCHEMA_VERSION,
        "project_id": pid,
        "objective": objective.strip()[:1000],
        "status": "pending",
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
        "criteria": _normalise_criteria(criteria),
    }
    data["status"] = _derive_status(data["criteria"])
    with _ISCLock(pid):
        target = ideal_state_path(pid)
        if target.exists() and not overwrite:
            raise IdealStateValidationError(
                f"ideal state already exists for project_id {pid!r}; pass overwrite=True to replace"
            )
        return _write_atomic(pid, data)


def get_ideal_state(project_id: str) -> Optional[Dict[str, Any]]:
    try:
        pid = _validate_pid(project_id)
    except IdealStateValidationError:
        return None
    p = ideal_state_path(pid)
    if not p.exists():
        return None
    try:
        with _ISCLock(pid):
            data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("ideal_state: failed to read %s", pid)
        return None
    if data.get("schema_version", 0) > SCHEMA_VERSION:
        return None
    return data


def update_criterion(project_id: str, criterion_id: str, *,
                     status: Optional[str] = None,
                     evidence: Optional[str] = None,
                     verification_method: Optional[str] = None,
                     linked_step_ids: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    pid = _validate_pid(project_id)
    if status is not None and status not in VALID_STATUSES:
        raise IdealStateValidationError(f"invalid status {status!r}; valid: {VALID_STATUSES}")
    with _ISCLock(pid):
        data = get_ideal_state(pid)
        if not data:
            return None
        crit = next((c for c in data.get("criteria") or [] if c.get("id") == criterion_id), None)
        if not crit:
            return None
        if status is not None:
            crit["status"] = status
        if evidence is not None:
            crit["evidence"] = str(evidence)[:2000]
        if verification_method is not None:
            crit["verification_method"] = str(verification_method)[:500]
        if linked_step_ids is not None:
            crit["linked_step_ids"] = [str(x) for x in linked_step_ids][:20]
        crit["updated_at"] = time.time()
        data["updated_at"] = time.time()
        data["status"] = _derive_status(data.get("criteria") or [])
        _write_atomic(pid, data)
        return crit


def sync_plan_links(project_id: str, plan: Optional[Dict[str, Any]] = None) -> int:
    """Read criterion_ids from plan steps and reflect them into ISC links."""
    pid = _validate_pid(project_id)
    if plan is None:
        try:
            from openjarvis.tools import agent_plan
            plan = agent_plan.get_plan(pid)
        except Exception:
            plan = None
    if not plan:
        return 0
    with _ISCLock(pid):
        data = get_ideal_state(pid)
        if not data:
            return 0
        criteria = {c.get("id"): c for c in data.get("criteria") or []}
        changed = 0
        for step in plan.get("steps") or []:
            sid = step.get("id")
            for cid in step.get("criterion_ids") or []:
                crit = criteria.get(cid)
                if not crit or not sid:
                    continue
                links = crit.setdefault("linked_step_ids", [])
                if sid not in links:
                    links.append(sid)
                    changed += 1
        if changed:
            data["updated_at"] = time.time()
            _write_atomic(pid, data)
        return changed


def summary(project_id: str) -> Dict[str, Any]:
    data = get_ideal_state(project_id)
    if not data:
        return {"exists": False}
    counts: Dict[str, int] = {}
    for c in data.get("criteria") or []:
        k = c.get("status", "pending")
        counts[k] = counts.get(k, 0) + 1
    total = len(data.get("criteria") or [])
    passed = counts.get("pass", 0) + counts.get("skipped", 0)
    return {
        "exists": True,
        "project_id": data.get("project_id"),
        "objective": data.get("objective"),
        "status": data.get("status"),
        "counts": counts,
        "total": total,
        "passed": passed,
        "summary": f"{passed}/{total} criteria satisfied",
    }


__all__ = [
    "create_ideal_state", "get_ideal_state", "update_criterion",
    "sync_plan_links", "summary", "ideal_state_path", "ideal_state_md_path",
    "IdealStateValidationError", "SCHEMA_VERSION", "MAX_CRITERIA",
]

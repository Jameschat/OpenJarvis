"""outcomes.py — structured event log for agent tasks + fast-path fires.

Phase L-1 of the learning loop (2026-04-29). Every completed agent task
and every matched fast-path produces a JSON record at:

    ~/.openjarvis/outcomes/<YYYY-MM-DD>/<id>.json

Two record types:
  - agent-task   — written from agent_runner.mark_finished()
  - fast-path    — written from voice_cmd._try_* when they match

The point: today, outcomes live as in-memory state.json fields and
stdout logs that get rotated. There's no queryable history of "how
often did marketing-head pass verification this month?" or "which
fast-paths fire wrong?" L-1 makes that data exist on disk in a
machine-readable shape.

L-2 (retrospective) reads these to surface patterns. L-3 (template
library) reads success patterns. L-4 (adaptive prompts) reads failure
patterns. None of those phases are built yet — but they all need this.

Best-effort by design: every write is wrapped so an outcome failure
NEVER affects the upstream operation. Loss of an outcome record is
preferable to a task that fails because logging failed.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Storage layout
# ---------------------------------------------------------------------------

def _outcomes_root() -> Path:
    """~/.openjarvis/outcomes/ — sibling to the agents/ dir agent_runner uses."""
    base = Path(os.environ.get(
        "OPENJARVIS_OUTCOMES_HOME",
        str(Path.home() / ".openjarvis" / "outcomes"),
    ))
    return base


def _day_dir(ts: Optional[float] = None) -> Path:
    """Date-bucketed sub-directory for a given timestamp (default now).
    Operator-local time so the bucket boundary feels natural."""
    if ts is None:
        ts = time.time()
    d = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    return _outcomes_root() / d


def _ensure_day_dir(ts: Optional[float] = None) -> Path:
    p = _day_dir(ts)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        logger.exception("outcomes: failed to mkdir %s", p)
    return p


# ---------------------------------------------------------------------------
# Write — best-effort, never raises
# ---------------------------------------------------------------------------


_write_lock = threading.Lock()


def _write_record(record: Dict[str, Any]) -> Optional[Path]:
    """Atomic write of a single outcome record. Returns the file path
    on success, None on failure. Never raises — outcome capture must
    never affect the upstream operation."""
    try:
        ts = record.get("ts") or record.get("ended_at") or time.time()
        day = _ensure_day_dir(ts)
        # ID is type-prefixed so listings are scannable: t_xxx for agent-task,
        # f_xxx for fast-path. Includes a ts component for natural ordering.
        rec_id = record.get("_id") or (
            ("t_" if record.get("type") == "agent-task" else "f_")
            + datetime.fromtimestamp(ts).strftime("%H%M%S") + "-"
            + uuid.uuid4().hex[:6]
        )
        record.setdefault("_id", rec_id)
        target = day / f"{rec_id}.json"
        with _write_lock:
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
            tmp.replace(target)
        return target
    except Exception:
        logger.exception("outcomes: write failed (record=%r)", record.get("_id"))
        return None


# ---------------------------------------------------------------------------
# Public — record_agent_task
# ---------------------------------------------------------------------------


def record_agent_task(task: Any, agent_spec: Optional[Dict[str, Any]] = None) -> None:
    """Capture a finished agent task. Called from
    agent_runner.mark_finished after the registry has updated.

    `task` is the Task dataclass (or any object with the same fields).
    `agent_spec` is the matching DEFAULT_AGENTS entry if available — we
    extract the department slug from there so outcome aggregation can
    group by department without re-resolving each time."""
    try:
        # Pull fields defensively — Task dataclass may evolve and we
        # don't want a missing attr to crash the write.
        def _g(name: str, default: Any = None) -> Any:
            v = getattr(task, name, default)
            return v if v is not None else default

        started = _g("started_at", 0) or 0
        ended = _g("ended_at", time.time()) or time.time()
        duration = max(0.0, (ended - started) if (started and ended) else 0.0)

        prompt = (_g("prompt", "") or "")[:240]

        # Failure-mode flags from autonomy #4 — these live as separate
        # AgentStats counters but the SHAPE of failure is per-task. Best
        # we can do here without a structured field on Task is infer
        # from error/exit_code patterns.
        err = (_g("error", "") or "")
        err_l = err.lower()
        quota_hit = ("monthly usage limit" in err_l or "quota" in err_l or
                     "rate limit" in err_l or "rate-limit" in err_l)
        refusal = ("i can't" in err_l or "i cannot" in err_l or
                   "refuse" in err_l)

        # Department resolution from agent_spec or by id pattern
        department = None
        if agent_spec and isinstance(agent_spec, dict):
            # Heads have id ending -head; map to dept slug
            aid = agent_spec.get("id", "")
            if aid.endswith("-head"):
                department = aid[:-len("-head")]

        record = {
            "type":            "agent-task",
            "task_id":         _g("id", ""),
            "agent_id":        _g("agent_id", ""),
            "project_id":      _g("project_id", None),
            "title":           (_g("title", "") or "")[:120],
            "status":          _g("status", "?"),
            "exit_code":       _g("exit_code", None),
            "error":           err[:500] if err else None,
            "duration_seconds": round(duration, 2),
            "verifier_grade":  _g("verifier_grade", None),
            "verifier_notes":  (_g("verifier_notes", "") or "")[:500] or None,
            "retry_count":     _g("retry_count", 0),
            "parent_task_id":  _g("parent_task_id", None),
            "plan_step_id":    None,   # populated below if present
            "department":      department,
            "quota_hit":       quota_hit,
            "refusal":         refusal,
            "prompt_summary":  prompt,
            "created_at":      _g("created_at", 0) or 0,
            "started_at":      started or None,
            "ended_at":        ended,
            "ended_iso":       datetime.fromtimestamp(ended).isoformat(timespec="seconds"),
        }

        # plan_step_id lookup — defensive (this map lives in agent_plan
        # and may not be importable in every context)
        try:
            from openjarvis.tools import agent_plan
            link = agent_plan.step_for_task(record["task_id"])
            if link:
                record["plan_step_id"] = link[1]   # (project_id, step_id)
        except Exception:
            pass

        _write_record(record)
    except Exception:
        logger.exception("outcomes: record_agent_task crashed (task=%r)",
                         getattr(task, "id", "?"))


# ---------------------------------------------------------------------------
# Public — record_fast_path
# ---------------------------------------------------------------------------


def record_fast_path(name: str, input_text: str, result: Optional[str]) -> None:
    """Capture a fast-path fire. Called from each _try_* immediately
    before returning the spoken response, so we capture the (input,
    output) pair and the operator can see exactly what triggered.

    Only fires that MATCHED (returned non-None) get recorded. No-match
    fast-paths flood the log — we'd write thousands of records per
    voice turn since every fast-path checks every utterance."""
    if not result:
        return
    try:
        record = {
            "type":       "fast-path",
            "fast_path":  name,
            "input_text": (input_text or "")[:500],
            "result":     (result or "")[:500],
            "matched":    True,
            "ts":         time.time(),
            "ts_iso":     datetime.now().isoformat(timespec="seconds"),
        }
        _write_record(record)
    except Exception:
        logger.exception("outcomes: record_fast_path crashed (name=%r)", name)


# ---------------------------------------------------------------------------
# Public — aggregate / query
# ---------------------------------------------------------------------------


def _walk_recent_files(window_days: int) -> List[Path]:
    """Return all outcome JSON files in the last `window_days` days."""
    out: List[Path] = []
    root = _outcomes_root()
    if not root.exists():
        return out
    cutoff = datetime.now() - _td(days=window_days)
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        try:
            day = datetime.strptime(d.name, "%Y-%m-%d")
        except ValueError:
            continue
        if day < cutoff:
            continue
        for f in d.glob("*.json"):
            out.append(f)
    return out


def _td(*, days: int):
    """timedelta(days=N) without importing timedelta at module top."""
    from datetime import timedelta
    return timedelta(days=days)


def _read_record(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("outcomes: read failed for %s", path, exc_info=True)
        return None


def agent_stats(agent_id: str, window_days: int = 30) -> Dict[str, Any]:
    """Aggregate per-agent stats over the recent window. Returns counts
    + averages. Only considers `agent-task` records, not fast-paths."""
    out = {
        "agent_id":        agent_id,
        "window_days":     window_days,
        "total":           0,
        "passes":          0,
        "needs_work":      0,
        "fails":           0,
        "errors":          0,
        "graded_total":    0,    # tasks that received a verifier_grade
        "salvages":        0,
        "no_files":        0,
        "refusals":        0,
        "quota_hits":      0,
        "avg_duration_s":  0.0,
        "last_outcome_at": None,
    }
    durations: List[float] = []
    last_ts = 0.0
    for f in _walk_recent_files(window_days):
        rec = _read_record(f)
        if not rec or rec.get("type") != "agent-task":
            continue
        if rec.get("agent_id") != agent_id:
            continue
        out["total"] += 1
        grade = rec.get("verifier_grade")
        if grade:
            out["graded_total"] += 1
            if grade == "pass":     out["passes"] += 1
            elif grade == "needs-work": out["needs_work"] += 1
            elif grade == "fail":   out["fails"] += 1
            else:                   out["errors"] += 1
        if rec.get("status") == "failed":
            out["fails"] += (1 if not grade else 0)
        if rec.get("quota_hit"):
            out["quota_hits"] += 1
        if rec.get("refusal"):
            out["refusals"] += 1
        d = rec.get("duration_seconds") or 0
        if d > 0:
            durations.append(float(d))
        ended = rec.get("ended_at") or 0
        if ended > last_ts:
            last_ts = ended
    if durations:
        out["avg_duration_s"] = round(sum(durations) / len(durations), 2)
    if last_ts:
        out["last_outcome_at"] = datetime.fromtimestamp(last_ts).isoformat(timespec="seconds")
    return out


def all_agent_stats(window_days: int = 30) -> Dict[str, Dict[str, Any]]:
    """Aggregate stats for every agent that produced an outcome in the
    window. Returns {agent_id: stats_dict}. Useful for the HUD-side
    "grade badges per agent card" follow-up build."""
    by_agent: Dict[str, Dict[str, Any]] = {}
    for f in _walk_recent_files(window_days):
        rec = _read_record(f)
        if not rec or rec.get("type") != "agent-task":
            continue
        aid = rec.get("agent_id") or ""
        if not aid:
            continue
        if aid not in by_agent:
            by_agent[aid] = agent_stats(aid, window_days=window_days)
    return by_agent


def recent_outcomes(
    window_days: int = 7,
    kind: Optional[str] = None,
    agent_id: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """List recent outcome records, optionally filtered. Newest first.
    Used by the upcoming /agent_stats endpoint and by L-2 retrospective."""
    out: List[Dict[str, Any]] = []
    for f in _walk_recent_files(window_days):
        rec = _read_record(f)
        if not rec:
            continue
        if kind and rec.get("type") != kind:
            continue
        if agent_id and rec.get("agent_id") != agent_id:
            continue
        out.append(rec)
    # Sort by timestamp desc — agent-task records have ended_at, fast-path have ts
    out.sort(key=lambda r: r.get("ended_at") or r.get("ts") or 0, reverse=True)
    return out[:limit]


__all__ = [
    "record_agent_task",
    "record_fast_path",
    "agent_stats",
    "all_agent_stats",
    "recent_outcomes",
]

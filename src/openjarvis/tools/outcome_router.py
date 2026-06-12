"""Outcome-driven routing — SHADOW MODE (Phase 7 #2).

Reads the L-1 outcome index to recommend which agent should get a task,
based on real historical success rates. v1 is shadow-only: it logs what
the router *would* have picked next to what was actually configured, and
changes nothing. Enforcement comes only after the shadow log shows the
recommendations are sane over a real window (plan: review after ~a week).

Shadow log: ``~/.openjarvis/learning/routing_shadow/<YYYY-MM-DD>.jsonl``
one JSON row per decision: {ts, task_id, configured, recommended, reason,
would_differ, mode: "shadow"}.

First call site: the escalation ladder hook — every escalation logs
whether the outcome data agrees with the configured target agent.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SHADOW_DIR = Path.home() / ".openjarvis" / "learning" / "routing_shadow"

# An agent needs at least this many recorded tasks in the window before
# its success rate is trusted over the configured default.
_MIN_SAMPLES = 3
_WINDOW_DAYS = 30


@dataclass
class RoutingRecommendation:
    agent_id: str
    reason: str
    stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def candidate_stats(
    candidates: List[str], window_days: int = _WINDOW_DAYS
) -> Dict[str, Dict[str, Any]]:
    """Per-candidate {total, succeeded, success_rate, avg_duration_s} from
    the L-1 outcome index. Agents with no records get total=0."""
    from openjarvis.tools import outcomes

    stats: Dict[str, Dict[str, Any]] = {
        c: {"total": 0, "succeeded": 0, "success_rate": 0.0, "avg_duration_s": 0.0}
        for c in candidates
    }
    durations: Dict[str, List[float]] = {c: [] for c in candidates}
    for rec in outcomes.recent_outcomes(
        window_days=window_days, kind="agent-task", limit=10_000
    ):
        aid = rec.get("agent_id") or ""
        if aid not in stats:
            continue
        s = stats[aid]
        s["total"] += 1
        if rec.get("status") == "done":
            s["succeeded"] += 1
        d = rec.get("duration_seconds") or 0
        if d > 0:
            durations[aid].append(float(d))
    for aid, s in stats.items():
        if s["total"]:
            s["success_rate"] = round(s["succeeded"] / s["total"], 4)
        if durations[aid]:
            s["avg_duration_s"] = round(sum(durations[aid]) / len(durations[aid]), 2)
    return stats


def recommend_agent(
    candidates: List[str],
    window_days: int = _WINDOW_DAYS,
    min_samples: int = _MIN_SAMPLES,
) -> RoutingRecommendation:
    """Pick the candidate with the best historical success rate (ties go to
    the faster agent). Candidates below `min_samples` recorded tasks are not
    trusted; if none qualify, the first candidate (the configured default)
    is kept with an 'insufficient data' reason."""
    if not candidates:
        raise ValueError("no candidates")
    stats = candidate_stats(candidates, window_days=window_days)
    qualified = [c for c in candidates if stats[c]["total"] >= min_samples]
    if not qualified:
        return RoutingRecommendation(
            candidates[0],
            f"insufficient data (<{min_samples} samples for every candidate)",
            stats,
        )
    best = sorted(
        qualified,
        key=lambda c: (-stats[c]["success_rate"], stats[c]["avg_duration_s"]),
    )[0]
    s = stats[best]
    return RoutingRecommendation(
        best,
        f"best success rate {s['success_rate']:.0%} over {s['total']} tasks "
        f"in {window_days}d (avg {s['avg_duration_s']}s)",
        stats,
    )


def _log_shadow(row: Dict[str, Any]) -> None:
    try:
        _SHADOW_DIR.mkdir(parents=True, exist_ok=True)
        path = _SHADOW_DIR / (time.strftime("%Y-%m-%d") + ".jsonl")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        logger.debug("outcome_router: shadow log write failed (non-fatal)", exc_info=True)


def shadow_route_escalation(
    task: Any, configured_target: str, alternates: Optional[List[str]] = None
) -> Optional[RoutingRecommendation]:
    """Shadow-mode call site for the escalation ladder: compute what the
    router would pick for this escalation and log it next to the configured
    target. Changes nothing; never raises; returns the recommendation (or
    None on any failure) for optional display."""
    try:
        candidates = [configured_target] + [
            a for a in (alternates or ["backend-dev", "gpt-backend"])
            if a != configured_target
        ]
        rec = recommend_agent(candidates)
        _log_shadow(
            {
                "ts": time.time(),
                "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "task_id": getattr(task, "id", None),
                "configured": configured_target,
                "recommended": rec.agent_id,
                "reason": rec.reason,
                "would_differ": rec.agent_id != configured_target,
                "mode": "shadow",
            }
        )
        return rec
    except Exception:
        logger.exception("outcome_router: shadow escalation routing failed (non-fatal)")
        return None


__all__ = [
    "RoutingRecommendation",
    "candidate_stats",
    "recommend_agent",
    "shadow_route_escalation",
]

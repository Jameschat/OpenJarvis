"""Daily Jarvis learning review.

Reads structured outcomes and capability gaps, then writes a compact digest
to the Brain so future sessions can adapt from evidence rather than vibes.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _group_repeated_failures(failed: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group failed agent-tasks by agent_id; return any with count >= 2.

    A single failure is a hiccup; two or more in the review window means
    a systemic issue — wrong prompt, missing data prereq, broken upstream.
    The operator should see this even when no formal capability gap was
    recorded (e.g. data-prereq failures don't trip capability_gaps).
    Sorted by count desc, then agent_id asc for stable rendering.
    """
    by_agent: Dict[str, List[Dict[str, Any]]] = {}
    for rec in failed:
        aid = rec.get("agent_id") or "unknown"
        by_agent.setdefault(aid, []).append(rec)
    repeated_failures: List[Dict[str, Any]] = []
    for aid, recs in by_agent.items():
        if len(recs) < 2:
            continue
        # Take the most recent error string as the representative — the
        # earliest may be stale if the operator already started fixing it.
        last_error = next(
            (r.get("error") for r in reversed(recs) if r.get("error")),
            "no error captured",
        )
        repeated_failures.append({
            "agent_id": aid,
            "count": len(recs),
            "last_error": last_error,
        })
    repeated_failures.sort(key=lambda x: (-x["count"], x["agent_id"]))
    return repeated_failures


def build_learning_digest(
    date_str: str,
    outcomes: List[Dict[str, Any]],
    gap_summary: Dict[str, Any],
) -> str:
    failed = [
        r for r in outcomes
        if r.get("type") == "agent-task" and r.get("status") == "failed"
    ]
    quota = [r for r in outcomes if r.get("quota_hit")]
    refusals = [r for r in outcomes if r.get("refusal")]
    recent_gaps = gap_summary.get("recent") or []
    repeated = gap_summary.get("repeated") or []
    repeated_failures = _group_repeated_failures(failed)

    lines = [
        "---",
        "type: knowledge",
        f"date: {date_str}",
        "tags: [jarvis, learning, digest, autonomy]",
        "parent: [[00 Session Handoff]]",
        "---",
        "",
        f"# Jarvis learning digest - {date_str}",
        "",
        "## Summary",
        "",
        f"- Outcomes reviewed: {len(outcomes)}",
        f"- Failed tasks: {len(failed)}",
        f"- Capability gaps open in window: {gap_summary.get('total', 0)}",
        f"- Quota/rate issues: {len(quota)}",
        f"- Refusal-shaped failures: {len(refusals)}",
        "",
        "## Patterns",
        "",
    ]
    if failed:
        for rec in failed[:10]:
            lines.append(
                f"- **{rec.get('agent_id', 'unknown')}** failed on "
                f"`{rec.get('prompt_summary', '')}`: "
                f"{rec.get('error') or 'no error captured'}"
            )
    else:
        lines.append("- No failed agent tasks in the review window.")
    lines.extend(["", "## Capability Gaps", ""])
    if recent_gaps:
        for gap in recent_gaps[:10]:
            lines.append(
                f"- **{gap.get('capability')}** ({gap.get('severity', 'medium')}): "
                f"{gap.get('trigger', '')}"
            )
    else:
        lines.append("- No capability gaps recorded.")
    lines.extend(["", "## Repeated Gaps", ""])
    if repeated:
        for item in repeated[:10]:
            lines.append(f"- **{item['capability']}** - {item['count']} occurrences")
    else:
        lines.append("- No repeated gaps yet.")
    lines.extend(["", "## Repeated Failures", ""])
    if repeated_failures:
        for item in repeated_failures[:10]:
            lines.append(
                f"- **{item['agent_id']}** failed {item['count']}x in window — "
                f"last error: {item['last_error']}"
            )
    else:
        lines.append("- No agent failed twice or more in the review window.")
    lines.extend(["", "## Recommended Next Actions", ""])
    if repeated:
        for item in repeated[:5]:
            lines.append(
                f"- Queue `capability-scout` for **{item['capability']}** "
                f"({item['count']} occurrences) to search GitHub/web and rank candidate tools."
            )
        lines.append("- Run `capability-queue` after scouting to rank prototype/watch/reject next steps.")
    else:
        lines.append("- No capability-scout queue needed yet; wait for repeated or high-severity gaps.")
        if recent_gaps:
            lines.append("- Run `capability-queue` if the operator wants a prioritized evolution backlog now.")
    if repeated_failures:
        for item in repeated_failures[:5]:
            lines.append(
                f"- **Investigate `{item['agent_id']}`** — failing {item['count']}x with "
                f"`{item['last_error']}`. Fix the underlying cause (data prereq, env var, "
                f"prompt) before next scheduled run."
            )
    lines.append("- For failed task patterns, update agent prompts only after a concrete repeated pattern appears.")
    lines.extend([
        "",
        "## Capability Scout Guardrail",
        "",
        "`capability-scout` may research, rank, and write recommendations. It must not install tools or mutate external systems without explicit operator approval.",
        "`capability-queue` may prioritize and write backlog notes. It must not install tools or mutate external systems without explicit operator approval.",
        "",
        "_Compiled by `learning-reviewer`._",
    ])
    return "\n".join(lines) + "\n"


def write_learning_digest(markdown: str, date_str: Optional[str] = None) -> Optional[Path]:
    try:
        from openjarvis.tools import obsidian_brain as ob
    except Exception:
        return None
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    target = ob.BRAIN_ROOT / "Knowledge" / f"{date_str} - Jarvis learning digest.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown, encoding="utf-8")
    return target


def run(window_days: int = 7) -> Dict[str, Any]:
    from openjarvis.tools import capability_gaps, outcomes

    date_str = datetime.now().strftime("%Y-%m-%d")
    recent = outcomes.recent_outcomes(window_days=window_days, limit=300)
    gaps = capability_gaps.summarize_gaps(window_days=window_days)
    md = build_learning_digest(date_str=date_str, outcomes=recent, gap_summary=gaps)
    path = write_learning_digest(md, date_str=date_str)
    return {
        "ok": path is not None,
        "path": str(path) if path else None,
        "outcomes": len(recent),
        "gaps": gaps.get("total", 0),
    }


def run_as_agent_task(prompt: str = "") -> Dict[str, Any]:
    return run(window_days=7)


__all__ = ["build_learning_digest", "write_learning_digest", "run", "run_as_agent_task"]

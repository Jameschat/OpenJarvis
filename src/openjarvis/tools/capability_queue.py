"""Capability Queue for Jarvis.

Turns recorded capability gaps and scout recommendations into a ranked,
recommendation-only evolution backlog. The queue is deliberately passive:
it writes JSON and Brain notes, but never installs tools or mutates systems.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SEVERITY_WEIGHT = {"high": 35, "medium": 22, "low": 10}


def _learning_root() -> Path:
    return Path(os.environ.get(
        "OPENJARVIS_LEARNING_HOME",
        str(Path.home() / ".openjarvis" / "learning"),
    ))


def _queue_root() -> Path:
    return _learning_root() / "capability_queue"


def _capability_counts(gap_summary: Dict[str, Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in gap_summary.get("repeated") or []:
        capability = str(item.get("capability") or "").strip()
        if capability:
            counts[capability] = max(1, int(item.get("count") or 1))
    for gap in gap_summary.get("recent") or []:
        capability = str(gap.get("capability") or "").strip()
        if capability:
            counts.setdefault(capability, 1)
    return counts


def _latest_gap_by_capability(gap_summary: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for gap in gap_summary.get("recent") or []:
        capability = str(gap.get("capability") or "").strip()
        if capability and capability not in latest:
            latest[capability] = gap
    return latest


def _action_for_scout(scout: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not scout:
        return {"action": "scout", "next_agent": "capability-scout"}
    recommendation = str(scout.get("recommendation") or "").lower()
    score = int(scout.get("score") or 0)
    if "prototype" in recommendation and score >= 70:
        return {"action": "prototype", "next_agent": "architect"}
    if "watch" in recommendation or score >= 50:
        return {"action": "watch", "next_agent": "learning-reviewer"}
    return {"action": "reject", "next_agent": "learning-reviewer"}


def build_queue(
    gap_summary: Dict[str, Any],
    scout_results: Optional[Dict[str, Dict[str, Any]]] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    counts = _capability_counts(gap_summary)
    latest = _latest_gap_by_capability(gap_summary)
    scout_results = scout_results or {}
    items: List[Dict[str, Any]] = []
    for capability, count in counts.items():
        gap = latest.get(capability, {"capability": capability})
        severity = str(gap.get("severity") or "medium")
        severity_weight = _SEVERITY_WEIGHT.get(severity, _SEVERITY_WEIGHT["medium"])
        repeat_weight = min(40, max(0, count - 1) * 18)
        priority = min(100, severity_weight + repeat_weight + 15)
        scout = scout_results.get(capability)
        action = _action_for_scout(scout)
        reason = f"{severity} severity; {count} occurrence"
        if count != 1:
            reason += "s"
        if scout:
            reason += (
                f"; scout recommends {scout.get('recommendation')} "
                f"at score {scout.get('score')}"
            )
        items.append({
            "capability": capability,
            "priority": priority,
            "severity": severity,
            "occurrences": count,
            "trigger": gap.get("trigger") or "",
            "action": action["action"],
            "next_agent": action["next_agent"],
            "reason": reason,
            "scout": scout,
        })
    items.sort(
        key=lambda item: (
            item["priority"],
            1 if item["action"] == "prototype" else 0,
            item["occurrences"],
        ),
        reverse=True,
    )
    return items[: max(1, int(limit or 20))]


def write_queue_json(items: List[Dict[str, Any]], date_str: Optional[str] = None) -> Path:
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    target_dir = _queue_root() / date_str
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "queue.json"
    payload = {
        "type": "capability-queue",
        "date": date_str,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "items": items,
    }
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(target)
    return target


def build_queue_report(items: List[Dict[str, Any]], date_str: Optional[str] = None) -> str:
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    lines = [
        "---",
        "type: knowledge",
        f"date: {date_str}",
        "tags: [jarvis, capability-queue, autonomy, learning]",
        "parent: [[2026-05-07 - Learning Core v1 implemented]]",
        "related:",
        "  - [[2026-05-07 - Capability Scout v1 implemented]]",
        "---",
        "",
        f"# Jarvis capability queue - {date_str}",
        "",
        "## Ranked Queue",
        "",
    ]
    if not items:
        lines.append("- No open capability gaps need queueing.")
    for i, item in enumerate(items, start=1):
        lines.extend([
            f"### {i}. {item.get('capability')}",
            f"- **Priority:** {item.get('priority')}/100",
            f"- **Action:** {item.get('action')}",
            f"- **Next agent:** `{item.get('next_agent')}`",
            f"- **Reason:** {item.get('reason')}",
            f"- **Trigger:** {item.get('trigger') or ''}",
            "",
        ])
        scout = item.get("scout") or {}
        if scout:
            lines.extend([
                f"- **Best scout candidate:** {scout.get('best_name') or 'unknown'}",
                f"- **Source:** {scout.get('source') or ''}",
                "",
            ])
    lines.extend([
        "## Guardrail",
        "",
        "This queue is advisory. Jarvis must not install packages, edit `jarvis.bat`, spend money, connect accounts, trade, delete data, or make irreversible external changes without explicit operator approval.",
    ])
    return "\n".join(lines) + "\n"


def write_queue_report(markdown: str, date_str: Optional[str] = None) -> Optional[Path]:
    try:
        from openjarvis.tools import obsidian_brain as ob
    except Exception:
        return None
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    target = ob.BRAIN_ROOT / "Knowledge" / f"{date_str} - Jarvis capability queue.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown, encoding="utf-8")
    return target


def run(window_days: int = 14) -> Dict[str, Any]:
    from openjarvis.tools import capability_gaps

    date_str = datetime.now().strftime("%Y-%m-%d")
    gaps = capability_gaps.summarize_gaps(window_days=window_days)
    items = build_queue(gaps)
    queue_path = write_queue_json(items, date_str=date_str)
    report = build_queue_report(items, date_str=date_str)
    report_path = write_queue_report(report, date_str=date_str)
    return {
        "ok": True,
        "queue_path": str(queue_path),
        "report_path": str(report_path) if report_path else None,
        "items": len(items),
    }


def run_as_agent_task(prompt: str = "") -> Dict[str, Any]:
    return run(window_days=14)


__all__ = [
    "build_queue",
    "write_queue_json",
    "build_queue_report",
    "write_queue_report",
    "run",
    "run_as_agent_task",
]

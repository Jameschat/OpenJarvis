"""Qwen escalation ladder — policy + context pack (Phase 7 #1).

When a local Qwen task fails its sandbox verification or self-reports
``BLOCKED`` (Odysseus run-contract state), this module decides whether to
queue a follow-up task on a stronger provider (Claude/Codex) and builds the
failure-context prompt for it.

Pure logic only — the actual ``add_task()`` wiring lives in
``agent_runner._run_qwen_task`` (plan Task C). Invariants:

- Default OFF: ``OPENJARVIS_ESCALATION`` must be ``1``/``true``/``on``.
- One hop max: a task that is itself an escalation never re-escalates.
- Day cap: ``OPENJARVIS_ESCALATION_MAX_PER_DAY`` (default 5, clamp 0..50)
  so a broken verify check can't burn the Claude/Codex quota overnight.
- The escalation child gets *context*, not new authority — production edits
  still go through the existing approval gates.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

_ESCALATIONS_DIR = Path.home() / ".openjarvis" / "learning" / "escalations"

_DEFAULT_TARGET_AGENT = "backend-dev"
_DEFAULT_MAX_PER_DAY = 5

# Uppercase run-contract state at a line start, optionally prefixed with
# "state:"/"state -". Never matches lowercase prose like "the drain was blocked".
_BLOCKED_RE = re.compile(r"(?m)^\s*(?:state\s*[:\-]\s*)?BLOCKED\b")

# Per-section budget for the context pack so the escalation prompt stays bounded.
_SECTION_CHAR_BUDGET = 4_000


@dataclass
class EscalationDecision:
    go: bool
    reason: str
    target_agent: Optional[str] = None


def _enabled() -> bool:
    return os.environ.get("OPENJARVIS_ESCALATION", "0").strip().lower() in (
        "1",
        "true",
        "on",
    )


def _target_agent() -> str:
    return os.environ.get("OPENJARVIS_ESCALATION_AGENT", "").strip() or _DEFAULT_TARGET_AGENT


def _max_per_day() -> int:
    try:
        raw = int(os.environ.get("OPENJARVIS_ESCALATION_MAX_PER_DAY", str(_DEFAULT_MAX_PER_DAY)))
    except ValueError:
        raw = _DEFAULT_MAX_PER_DAY
    return max(0, min(raw, 50))


def _day_filename(ts: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts or time.time())) + ".json"


def _read_day_record(ts: Optional[float] = None) -> Dict[str, Any]:
    path = _ESCALATIONS_DIR / _day_filename(ts)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("count"), int):
            return data
    except (OSError, ValueError):
        pass
    return {"count": 0, "escalations": []}


def record_escalation(task_id: str, child_id: str, ts: Optional[float] = None) -> None:
    """Append one escalation to today's day-cap record. Best-effort: never raises."""
    try:
        _ESCALATIONS_DIR.mkdir(parents=True, exist_ok=True)
        data = _read_day_record(ts)
        data["count"] = int(data.get("count", 0)) + 1
        data.setdefault("escalations", []).append(
            {"task_id": task_id, "child_id": child_id}
        )
        path = _ESCALATIONS_DIR / _day_filename(ts)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def _content_reports_blocked(content: Optional[str]) -> bool:
    return bool(content) and bool(_BLOCKED_RE.search(content))


def should_escalate(
    task: Any,
    verify_verdict: Optional[Dict[str, Any]],
    content: Optional[str],
    *,
    now: Optional[float] = None,
) -> EscalationDecision:
    """Decide whether a finished Qwen task should escalate to a stronger provider."""
    if not _enabled():
        return EscalationDecision(False, "escalation disabled (OPENJARVIS_ESCALATION!=1)")
    if getattr(task, "escalation_of", None):
        return EscalationDecision(False, "task is already an escalation (one hop max)")

    reason: Optional[str] = None
    if verify_verdict and verify_verdict.get("available") and not verify_verdict.get("verified"):
        reason = "verify-failed"
    elif _content_reports_blocked(content):
        reason = "blocked"
    if reason is None:
        return EscalationDecision(False, "no failure signal (verify ok / no BLOCKED state)")

    cap = _max_per_day()
    if _read_day_record(now)["count"] >= cap:
        return EscalationDecision(False, f"daily escalation cap reached ({cap})")

    return EscalationDecision(True, reason, target_agent=_target_agent())


def _read_section(path: Path, label: str) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return f"({label} not available)"
    if not text:
        return f"({label} not available)"
    if len(text) > _SECTION_CHAR_BUDGET:
        text = text[:_SECTION_CHAR_BUDGET] + f"\n... ({label} truncated)"
    return text


def build_escalation_prompt(
    task: Any,
    workspace: Path,
    verify_verdict: Optional[Dict[str, Any]],
    *,
    reason: str,
) -> str:
    """Build the failure-context prompt handed to the stronger provider."""
    workspace = Path(workspace)
    result_md = _read_section(workspace / "RESULT.md", "RESULT.md")
    trail = _read_section(workspace / "QWEN_REVISION_TRAIL.json", "revision trail")
    if verify_verdict:
        verdict_text = json.dumps(verify_verdict, indent=2)
    else:
        verdict_text = "(sandbox verify verdict not available)"

    original_prompt = (getattr(task, "prompt", "") or "").strip()
    if len(original_prompt) > _SECTION_CHAR_BUDGET:
        original_prompt = original_prompt[:_SECTION_CHAR_BUDGET] + "\n... (original prompt truncated)"

    return (
        "ESCALATED TASK — local Qwen attempted this and failed "
        f"(reason: {reason}). You are the stronger provider on the ladder; "
        "your job is to finish it properly.\n\n"
        "## Original request\n\n"
        f"{original_prompt}\n\n"
        "## What Qwen produced (RESULT.md)\n\n"
        f"{result_md}\n\n"
        "## Qwen revision trail\n\n"
        f"{trail}\n\n"
        "## Sandbox verify verdict (exit_code = last check run)\n\n"
        f"{verdict_text}\n\n"
        "## Instructions\n\n"
        f"- The shared workspace is at `{workspace}` — inspect Qwen's partial "
        "work there before starting; reuse what is correct.\n"
        "- Diagnose why the attempt failed (the verify verdict and trail above "
        "are real outcomes, not summaries), then complete the original request.\n"
        "- Follow the normal approval gates for production edits; escalation "
        "grants context, not extra authority.\n"
    )

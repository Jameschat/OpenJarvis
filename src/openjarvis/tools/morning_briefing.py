"""Proactive morning briefing (Phase 7 #4).

Composes what Jarvis already knows into one operator-facing brief at 07:00:
runtime/watchdog state, overnight task outcomes (+ escalations), shadow-routing
disagreements, the latest study-agent note, and vault activity. Writes
``Brain/Daily/<date> - morning briefing.md`` and pushes a one-line notice to
the HUD chat panel.

Every section gatherer is best-effort: a broken source becomes a missing
section, never a failed brief. Registered as the ``briefing-agent`` python
provider; scheduled daily via ``Brain/Scheduled``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_WATCHDOG_REPORT = Path.home() / ".openjarvis" / "runtime_watchdog_last.json"
_SHADOW_DIR = Path.home() / ".openjarvis" / "learning" / "routing_shadow"

_MAX_FAILURES_LISTED = 5


def _vault_root() -> Path:
    return Path(os.environ.get("BRAIN_ROOT", r"E:\Claude\Obsidian\Claude\Brain"))


def _push_chat(message: str) -> bool:
    from openjarvis.tools import task_followup

    return task_followup.push_to_chat(message)


# ---------------------------------------------------------------------------
# Section gatherers — each returns markdown or None, never raises upstream
# ---------------------------------------------------------------------------


def section_runtime() -> str:
    """Watchdog's last report: stack state, last action, last correctness probe."""
    try:
        report = json.loads(_WATCHDOG_REPORT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "No watchdog report found — check `JarvisRuntimeWatchdog`."
    lines = [report.get("summary") or ("stack ok" if report.get("ok") else "stack NOT ok")]
    action = report.get("action") or "none"
    if action != "none":
        lines.append(f"Last watchdog action: **{action}**")
    probe = report.get("probe")
    if probe:
        state = "passed" if probe.get("ok") else f"FAILED ({probe.get('error') or probe.get('content')!r})"
        lines.append(f"Last correctness probe: {state} => {probe.get('content', '')!r}")
    return "\n".join(lines)


def section_outcomes(window_days: int = 1) -> Optional[str]:
    """Overnight agent-task outcomes: counts, failures, escalations."""
    from openjarvis.tools import outcomes

    recent = outcomes.recent_outcomes(window_days=window_days, kind="agent-task", limit=500)
    if not recent:
        return None
    done = [r for r in recent if r.get("status") == "done"]
    failed = [r for r in recent if r.get("status") == "failed"]
    escalated = [r for r in recent if r.get("escalated_to")]
    lines = [
        f"{len(recent)} task(s) in the last {window_days * 24}h — "
        f"{len(done)} done, {len(failed)} failed."
    ]
    for r in failed[:_MAX_FAILURES_LISTED]:
        err = (r.get("error") or "no error captured")[:120]
        lines.append(f"- ✗ {r.get('agent_id')}: {r.get('title')} — {err}")
    if len(failed) > _MAX_FAILURES_LISTED:
        lines.append(f"- … and {len(failed) - _MAX_FAILURES_LISTED} more failures")
    for r in escalated:
        lines.append(
            f"- ⤴ {r.get('agent_id')}: {r.get('title')} — escalated to task {r.get('escalated_to')}"
        )
    return "\n".join(lines)


def section_shadow_routing() -> Optional[str]:
    """Shadow-router rows from the last day: how often it disagrees."""
    rows: list[dict[str, Any]] = []
    if not _SHADOW_DIR.exists():
        return None
    for f in sorted(_SHADOW_DIR.glob("*.jsonl"))[-2:]:
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        except (OSError, ValueError):
            continue
    if not rows:
        return None
    differ = [r for r in rows if r.get("would_differ")]
    out = f"{len(rows)} shadow routing decision(s); {len(differ)} disagree with the configured target."
    for r in differ[:3]:
        out += f"\n- task {r.get('task_id')}: configured {r.get('configured')} vs recommended {r.get('recommended')}"
    return out


def section_notifications(window_hours: int = 24) -> Optional[str]:
    """Operator notifications from the last day — including any that failed
    to deliver live (e.g. the chat bus was down mid-incident)."""
    from openjarvis.tools import notify as notify_mod

    path = notify_mod._LOG_PATH
    if not path.exists():
        return None
    cutoff = time.time() - window_hours * 3600
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("ts", 0) >= cutoff:
                rows.append(row)
    except (OSError, ValueError):
        return None
    if not rows:
        return None
    lines = [f"{len(rows)} notification(s) in the last {window_hours}h:"]
    for r in rows[-8:]:
        missed = "" if any(r.get("delivered", {}).values()) else " (NOT delivered live)"
        lines.append(f"- {r.get('ts_iso', '')[11:16]} {r.get('message', '')}{missed}")
    return "\n".join(lines)


def section_study() -> Optional[str]:
    """Most recent study-agent note, if any."""
    study = _vault_root() / "Study"
    if not study.exists():
        return None
    notes = sorted(study.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not notes:
        return None
    latest = notes[0]
    rel = latest.relative_to(_vault_root())
    return f"Latest study note: `{rel}` — skim and promote to Knowledge/ if good."


def section_vault() -> Optional[str]:
    """Vault activity summary via the existing stats helper."""
    try:
        from openjarvis.tools import vault_stats

        s = vault_stats.summary(_vault_root(), now=datetime.now())
        return (
            f"{s.get('total_notes', '?')} notes; {s.get('daily_today', 0)} written today; "
            f"last write {s.get('last_write_iso', 'unknown')}."
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Compose + run
# ---------------------------------------------------------------------------


def compose_briefing(sections: Dict[str, Optional[str]], now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    lines = [
        f"# Morning briefing — {now.strftime('%Y-%m-%d')}",
        "",
        f"Generated {now.strftime('%H:%M')} by briefing-agent (zero cloud tokens).",
        "",
    ]
    for title, body in sections.items():
        if not body:
            continue
        lines += [f"## {title}", "", body, ""]
    return "\n".join(lines)


def run_as_agent_task(task: Any = None) -> Dict[str, Any]:
    """Entry point for agent_runner's python provider. Never raises."""
    try:
        gatherers = {
            "Runtime": section_runtime,
            "Overnight tasks": section_outcomes,
            "Routing shadow log": section_shadow_routing,
            "Notifications": section_notifications,
            "Self-study": section_study,
            "Vault": section_vault,
        }
        sections: Dict[str, Optional[str]] = {}
        for title, fn in gatherers.items():
            try:
                sections[title] = fn()
            except Exception:
                logger.exception("morning_briefing: section %s failed (skipped)", title)
                sections[title] = None

        now = datetime.now()
        md = compose_briefing(sections, now)
        daily = _vault_root() / "Daily"
        daily.mkdir(parents=True, exist_ok=True)
        note_path = daily / f"{now.strftime('%Y-%m-%d')} - morning briefing.md"
        note_path.write_text(md, encoding="utf-8")

        summary_bits = [t for t, b in sections.items() if b]
        try:
            _push_chat(
                f"☀️ Morning briefing ready — {', '.join(summary_bits).lower()} — "
                f"Brain/Daily/{note_path.name}"
            )
        except Exception:
            logger.exception("morning_briefing: chat push failed (non-fatal)")

        return {"ok": True, "note_path": str(note_path), "sections": summary_bits}
    except Exception as exc:
        logger.exception("morning_briefing: run failed")
        return {"ok": False, "error": str(exc)}


__all__ = [
    "compose_briefing",
    "run_as_agent_task",
    "section_outcomes",
    "section_runtime",
    "section_shadow_routing",
    "section_study",
    "section_vault",
]

"""Capability Scout for Jarvis.

Given a recorded capability gap, search/rank candidate tools and write a
Brain note recommending adopt/prototype/watch/reject. The module keeps the
scoring deterministic so learning reviews remain explainable.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _keywords(gap: Dict[str, Any]) -> List[str]:
    text = " ".join(str(gap.get(k) or "") for k in ("capability", "trigger", "context")).lower()
    words = []
    for raw in text.replace("/", " ").replace("-", " ").split():
        word = "".join(ch for ch in raw if ch.isalnum())
        if len(word) >= 4 and word not in {"operator", "asked", "jarvis", "with", "that", "this"}:
            words.append(word)
    return sorted(set(words))


def score_candidate(
    gap: Dict[str, Any],
    candidate: Dict[str, Any],
    now_iso: Optional[str] = None,
) -> Dict[str, Any]:
    now = _parse_iso(now_iso or datetime.now(timezone.utc).isoformat()) or datetime.now(timezone.utc)
    stars = int(candidate.get("stars") or candidate.get("stargazers_count") or 0)
    pushed = _parse_iso(str(candidate.get("pushed_at") or ""))
    haystack = " ".join(
        str(candidate.get(k) or "")
        for k in ("name", "full_name", "description", "topics", "language")
    ).lower()
    keywords = _keywords(gap)
    matches = [kw for kw in keywords if kw in haystack]

    score = 0
    reasons: List[str] = []
    if matches:
        score += min(35, 10 + len(matches) * 8)
        reasons.append("matches gap keywords: " + ", ".join(matches[:5]))
    if stars >= 10000:
        score += 25
        reasons.append("strong community signal")
    elif stars >= 1000:
        score += 18
        reasons.append("healthy community signal")
    elif stars >= 100:
        score += 10
        reasons.append("some community signal")
    else:
        reasons.append("weak community signal")
    if pushed:
        days = max(0, (now - pushed).days)
        if days <= 30:
            score += 25
            reasons.append("recent activity")
        elif days <= 180:
            score += 15
            reasons.append("active within six months")
        elif days <= 365:
            score += 8
            reasons.append("active within a year")
        else:
            score -= 10
            reasons.append("stale activity")
    else:
        reasons.append("unknown activity")
    if candidate.get("html_url") or candidate.get("url"):
        score += 5
        reasons.append("source URL available")
    score = max(0, min(score, 100))
    if score >= 75:
        recommendation = "prototype first"
    elif score >= 55:
        recommendation = "watch"
    else:
        recommendation = "reject"
    out = dict(candidate)
    out.update({
        "score": score,
        "recommendation": recommendation,
        "reasons": reasons,
    })
    return out


def search_github_candidates(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    url = (
        "https://api.github.com/search/repositories?q="
        + quote_plus(query)
        + "&sort=stars&order=desc&per_page="
        + str(max(1, min(int(limit or 8), 10)))
    )
    req = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "OpenJarvis-CapabilityScout/1.0",
    })
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urlopen(req, timeout=12) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    out: List[Dict[str, Any]] = []
    for item in payload.get("items") or []:
        out.append({
            "name": item.get("name"),
            "full_name": item.get("full_name"),
            "description": item.get("description") or "",
            "stars": item.get("stargazers_count") or 0,
            "language": item.get("language") or "",
            "pushed_at": item.get("pushed_at") or "",
            "html_url": item.get("html_url") or "",
            "topics": item.get("topics") or [],
        })
    return out


def build_scout_report(
    gap: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    date_str: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> str:
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    capability = str(gap.get("capability") or "unknown capability")
    scored = [score_candidate(gap, c, now_iso=now_iso) for c in candidates]
    scored.sort(key=lambda c: c.get("score", 0), reverse=True)
    best = scored[0] if scored else None
    lines = [
        "---",
        "type: knowledge",
        f"date: {date_str}",
        "tags: [jarvis, capability-scout, github, autonomy]",
        "parent: [[2026-05-07 - Learning Core v1 implemented]]",
        "---",
        "",
        f"# Capability scout - {capability}",
        "",
        "## Gap",
        "",
        f"- **Capability:** {capability}",
        f"- **Trigger:** {gap.get('trigger') or ''}",
        f"- **Severity:** {gap.get('severity') or 'medium'}",
        "",
        "## Recommendation",
        "",
    ]
    if best:
        lines.append(
            f"**{best.get('full_name') or best.get('name')}** - "
            f"{best.get('recommendation')} (score {best.get('score')}/100)."
        )
    else:
        lines.append("No candidates found. Re-run with broader GitHub queries.")
    lines.extend(["", "## Candidates", ""])
    for candidate in scored:
        url = candidate.get("html_url") or candidate.get("url") or ""
        reasons = "; ".join(candidate.get("reasons") or [])
        lines.extend([
            f"### {candidate.get('full_name') or candidate.get('name')}",
            f"- **Score:** {candidate.get('score')}/100",
            f"- **Recommendation:** {candidate.get('recommendation')}",
            f"- **Stars:** {candidate.get('stars') or candidate.get('stargazers_count') or 0}",
            f"- **Last push:** {candidate.get('pushed_at') or 'unknown'}",
            f"- **Source:** {url}",
            f"- **Why:** {reasons}",
            "",
        ])
    lines.extend([
        "## Guardrail",
        "",
        "This is a recommendation only. Do not install packages, edit `jarvis.bat`, spend money, connect accounts, trade, delete data, or make irreversible external changes without explicit operator approval.",
    ])
    return "\n".join(lines) + "\n"


def write_scout_report(markdown: str, gap: Dict[str, Any], date_str: Optional[str] = None) -> Optional[Path]:
    try:
        from openjarvis.tools import obsidian_brain as ob
    except Exception:
        return None
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    slug = ob._slugify("capability scout " + str(gap.get("capability") or "gap"))[:80]
    target = ob.BRAIN_ROOT / "Knowledge" / f"{date_str} - {slug}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown, encoding="utf-8")
    return target


def run_for_gap(gap: Dict[str, Any], query: Optional[str] = None, limit: int = 8) -> Dict[str, Any]:
    query = query or str(gap.get("capability") or "")
    try:
        candidates = search_github_candidates(query, limit=limit)
    except Exception as exc:
        logger.exception("capability_scout: github search failed")
        return {"ok": False, "error": str(exc), "gap": gap}
    md = build_scout_report(gap=gap, candidates=candidates)
    path = write_scout_report(md, gap)
    return {"ok": path is not None, "path": str(path) if path else None, "candidate_count": len(candidates)}


def run_as_agent_task(prompt: str = "") -> Dict[str, Any]:
    from openjarvis.tools import capability_gaps

    summary = capability_gaps.summarize_gaps(window_days=14)
    targets = summary.get("repeated") or []
    if targets:
        capability = targets[0]["capability"]
        gap = next((g for g in summary.get("recent") or [] if g.get("capability") == capability), {"capability": capability})
    else:
        recent = summary.get("recent") or []
        if not recent:
            return {"ok": True, "reason": "no capability gaps to scout"}
        gap = recent[0]
    return run_for_gap(gap)


__all__ = [
    "score_candidate",
    "search_github_candidates",
    "build_scout_report",
    "write_scout_report",
    "run_for_gap",
    "run_as_agent_task",
]

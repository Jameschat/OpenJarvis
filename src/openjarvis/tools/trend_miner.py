"""TikTok / short-form trend miner for the AI build-in-public niche.

Pulls hot signals from public sources, filters for relevance to AI tools /
agent / personal-AI content, and writes a daily ``Brain/Content/Trends/<date>.md``
note with ranked candidate hooks the script-writer agent can pick from.

Sources (all public, no auth needed):
- Hacker News front page    (algolia.hn search API)
- Reddit r/LocalLLaMA, r/ChatGPT, r/singularity, r/LangChain   (json endpoints)
- GitHub Trending repos      (no API; scrape with raw HTML parse)

Designed to be runnable on its own:
    uv run python -m openjarvis.tools.trend_miner

…or scheduled via the existing agent_runner.schedule_task layer.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Keywords that signal "this would make a good TikTok hook"
_HOT_KEYWORDS = (
    "agent", "agents", "claude", "gpt", "codex", "openai", "anthropic",
    "obsidian", "vault", "memory", "automate", "automation", "build",
    "side project", "indie", "personal ai", "self-hosted", "local llm",
    "ollama", "rag", "mcp", "tool use", "voice ai", "ai assistant",
    "jarvis", "second brain", "knowledge base", "embed", "stack",
    "open source", "side hustle", "saas", "lifestyle business",
)

_USER_AGENT = "Mozilla/5.0 (compatible; OpenJarvis-trend-miner/1.0)"


def _fetch(url: str, timeout: int = 8) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as exc:
        logger.warning("fetch %s failed: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Source: Hacker News (Algolia search — JSON, public)
# ---------------------------------------------------------------------------


def _hn_top(query: str = "ai", limit: int = 20) -> List[Dict]:
    url = (f"https://hn.algolia.com/api/v1/search?"
           f"tags=story&query={urllib.parse.quote(query)}&hitsPerPage={limit}")
    raw = _fetch(url)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out = []
    for h in data.get("hits", []):
        out.append({
            "source":  "hn",
            "title":   h.get("title") or "",
            "url":     h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
            "points":  h.get("points") or 0,
            "comments": h.get("num_comments") or 0,
            "ts":      h.get("created_at"),
        })
    return out


# ---------------------------------------------------------------------------
# Source: Reddit (anon JSON endpoint, public)
# ---------------------------------------------------------------------------


def _reddit_top(subreddit: str, limit: int = 15) -> List[Dict]:
    url = f"https://www.reddit.com/r/{subreddit}/top.json?t=day&limit={limit}"
    raw = _fetch(url)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out = []
    for child in (data.get("data") or {}).get("children", []):
        d = child.get("data") or {}
        out.append({
            "source":  f"reddit/{subreddit}",
            "title":   d.get("title") or "",
            "url":     "https://reddit.com" + (d.get("permalink") or ""),
            "points":  d.get("ups") or 0,
            "comments": d.get("num_comments") or 0,
            "ts":      d.get("created_utc"),
        })
    return out


# ---------------------------------------------------------------------------
# Scoring: relevance to "AI build-in-public" niche
# ---------------------------------------------------------------------------


def _relevance(title: str) -> Tuple[float, List[str]]:
    """Return (score, matched_keywords). Higher score = more on-niche."""
    low = title.lower()
    matched = [k for k in _HOT_KEYWORDS if k in low]
    return (len(matched), matched)


def _hookable(item: Dict) -> bool:
    """Filter heuristic — only keep items that read like a viral hook."""
    title = item.get("title", "")
    if not title or len(title) < 12:
        return False
    score, matched = _relevance(title)
    if score < 1:
        return False
    # Drop anything that reads like a generic press release / corporate news
    skip = ("announces", "announced", "press release", "stock", "earnings",
            "shares", "ipo")
    if any(s in title.lower() for s in skip):
        return False
    return True


def _hook_proposals(item: Dict) -> List[str]:
    """Generate two or three candidate TikTok hooks for the item."""
    t = item["title"].rstrip(".")
    return [
        f"Wait — {t.lower()}?",
        f"Most people don't realise: {t}",
        f"I tried {t.split(':')[0] if ':' in t else t.split('-')[0]}. Here's what happened.",
    ]


# ---------------------------------------------------------------------------
# Note writer
# ---------------------------------------------------------------------------


def write_trend_note() -> Optional[Path]:
    try:
        from openjarvis.tools import obsidian_brain as ob
    except Exception:
        return None
    if not ob.DEFAULT_VAULT.exists():
        return None
    ob._ensure_layout()

    items: List[Dict] = []
    items += _hn_top("AI agent", limit=15)
    items += _hn_top("Claude", limit=10)
    items += _hn_top("GPT", limit=10)
    items += _reddit_top("LocalLLaMA", limit=15)
    items += _reddit_top("ChatGPT", limit=15)
    items += _reddit_top("singularity", limit=10)
    items += _reddit_top("LangChain", limit=10)

    # Dedupe by URL, score, sort
    seen = set()
    scored: List[Tuple[float, Dict, List[str]]] = []
    for it in items:
        u = it.get("url")
        if not u or u in seen:
            continue
        seen.add(u)
        if not _hookable(it):
            continue
        score, matched = _relevance(it["title"])
        # Boost by engagement (points + 2*comments, log-scaled)
        engagement = (it.get("points", 0) or 0) + 2 * (it.get("comments", 0) or 0)
        weight = score * 10 + min(50, engagement / 5)
        scored.append((weight, {**it, "matched": matched}, _hook_proposals(it)))
    scored.sort(key=lambda r: -r[0])

    today = datetime.now().strftime("%Y-%m-%d")
    target = ob.TRENDS_DIR / f"{today} - AI build-in-public trends.md"

    lines = [
        "---",
        f"date: {today}",
        "type: trend-miner",
        "tags: [trends, content, tiktok, ai-build-in-public]",
        f"sources: hn, reddit/LocalLLaMA, reddit/ChatGPT, reddit/singularity, reddit/LangChain",
        f"items: {len(scored)}",
        "---",
        "",
        f"# Trend pulse — {datetime.now().strftime('%a %d %b %Y')}",
        "",
        f"_{len(scored)} on-niche stories surfaced today, ranked by hookability + engagement._",
        "",
        "Each entry is a CANDIDATE for a 30-60s TikTok script. The script-writer "
        "agent should pick one or two and turn them into hook-driven shorts.",
        "",
    ]
    for i, (w, it, hooks) in enumerate(scored[:25], 1):
        lines.append(f"## {i}. {it['title']}")
        lines.append("")
        lines.append(f"- **Source:** {it['source']} · "
                     f"{it.get('points',0)} pts · {it.get('comments',0)} comments · "
                     f"weight {w:.1f}")
        lines.append(f"- **Link:** {it['url']}")
        lines.append(f"- **Matched keywords:** {', '.join(it.get('matched', []))}")
        lines.append("")
        lines.append("**Hook proposals:**")
        for h in hooks:
            lines.append(f"- _\"{h}\"_")
        lines.append("")

    if not scored:
        lines.append("_No trending items matched the AI build-in-public filter today._")
        lines.append("_Check back tomorrow or broaden the keyword list._")

    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    logger.info("trend miner wrote %s (%d items)", target, len(scored))

    # Pulse the second-brain crystal so Mission Control reflects the activity
    try:
        ob._emit_event("write", f"trends: {len(scored)} on-niche stories",
                       kind="trends", source="agent")
    except Exception:
        pass
    return target


def main() -> None:
    p = write_trend_note()
    print(f"Wrote {p}" if p else "Trend miner could not write — vault unavailable")


if __name__ == "__main__":
    main()

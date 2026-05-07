# src/openjarvis/tiktok/trend_scorer.py
"""TikTok virality scorer — wraps trend_miner, scores 0–100."""
from __future__ import annotations
import datetime, os
from pathlib import Path
from typing import List, Dict, Tuple

from openjarvis.tools.trend_miner import _hn_top, _reddit_top, _relevance
from openjarvis.tiktok.state import save_trends

_VIRAL_WORDS = [
    "everyone", "nobody", "secretly", "actually", "finally", "exposed",
    "real truth", "shocking", "wild", "insane", "unbelievable", "why",
    "you need", "they don't", "wait",
]
_SHORT_FORM_WORDS = [
    "how to", "explained", "in 60 seconds", "vs", "comparison", "review",
    "best", "worst", "top", "ranked", "guide",
]
_REDDIT_SOURCES = ["LocalLLaMA", "ChatGPT", "programming", "sidehustle", "technology"]


def score_for_tiktok(item: Dict) -> int:
    """Score a trend item 0–100 for TikTok virality potential.

    _relevance(title) returns (keyword_count, matched_keywords).
    keyword_count is the number of on-niche hot keywords found in the title.
    We cap at 5 and normalize to a 0.0–1.0 base contributing up to 30 pts.
    The remaining 70 pts come from virality signals, format fit, and engagement.
    """
    title = (item.get("title") or "")
    keyword_count, _ = _relevance(title)
    # Normalize keyword count: 0 → 0 pts, 1 → 6 pts, 5+ → 30 pts
    base_pts = min(int(keyword_count), 5) * 6

    title_lower = title.lower()

    # Viral language: each matching word adds 12 pts up to a cap of 36 pts
    viral_matches = sum(1 for w in _VIRAL_WORDS if w in title_lower)
    viral_bonus = min(viral_matches * 12, 36)

    # Short-form / educational format: each match adds up to 15 pts
    format_matches = sum(1 for w in _SHORT_FORM_WORDS if w in title_lower)
    format_bonus = min(format_matches * 8, 15)

    # Title length sweet spot for short-form video (5–12 words)
    words = len(title_lower.split())
    length_bonus = 8 if 5 <= words <= 12 else 0

    # Social engagement (upvotes / points)
    points = item.get("points", 0) or 0
    if points > 500:
        engagement_bonus = 20
    elif points > 200:
        engagement_bonus = 12
    elif points > 50:
        engagement_bonus = 6
    else:
        engagement_bonus = 0

    total = base_pts + viral_bonus + format_bonus + length_bonus + engagement_bonus
    return min(100, max(0, total))


def fetch_and_score(threshold: int = 70) -> List[Dict]:
    """Fetch from all sources, score, return items >= threshold sorted desc."""
    items = _fetch_all_scored()
    return [i for i in items if i["tiktok_score"] >= threshold]


def _fetch_all_scored() -> List[Dict]:
    items: List[Dict] = []
    for item in _hn_top(query="ai", limit=20):
        item["source"] = "HN"
        item["tiktok_score"] = score_for_tiktok(item)
        items.append(item)
    for sub in _REDDIT_SOURCES:
        for item in _reddit_top(sub, limit=10):
            item["source"] = f"r/{sub}"
            item["tiktok_score"] = score_for_tiktok(item)
            items.append(item)
    items.sort(key=lambda x: x["tiktok_score"], reverse=True)
    return items


def write_tiktok_trends(threshold: int = 70) -> Tuple[List[Dict], str]:
    """Fetch, score, write vault note. Returns (qualified_items, note_path)."""
    scored = _fetch_all_scored()
    items = [i for i in scored if i["tiktok_score"] >= threshold]
    save_trends([
        {
            **item,
            "status": "qualified" if item["tiktok_score"] >= threshold else "below",
        }
        for item in scored[:20]
    ])
    vault = os.environ.get(
        "OPENJARVIS_VAULT_PATH",
        str(Path.home() / "Claude" / "Obsidian" / "Claude" / "Brain"),
    )
    out_dir = Path(vault) / "Content" / "Trends"
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.date.today().isoformat()
    note_path = out_dir / f"{date_str}-tiktok-scores.md"
    lines = [f"# TikTok Trend Scores — {date_str}\n\n",
             f"Threshold: {threshold} | Qualified: {len(items)} | Scanned: {len(scored)}\n\n"]
    for item in scored[:20]:
        s = item["tiktok_score"]
        lines.append(f"- ✅ **{s}** [{item['title']}]({item.get('url','')}) — {item['source']}\n")
    note_path.write_text("".join(lines), encoding="utf-8")
    return items, str(note_path)

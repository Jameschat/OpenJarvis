# src/openjarvis/tiktok/finance.py
"""RPM-based revenue estimation for TikTok Creator Rewards Programme."""
from __future__ import annotations
import datetime
from typing import List, Dict

_RPM_GBP_DEFAULT = 7.5  # conservative midpoint of £2–£8 Creator Rewards range


def estimate_earnings_gbp(views: int, rpm: float = _RPM_GBP_DEFAULT) -> float:
    return round((views / 1000) * rpm, 2)


def monthly_summary(posted: List[Dict], rpm: float = _RPM_GBP_DEFAULT) -> Dict:
    now = datetime.datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    this_month = [p for p in posted if p.get("posted_at", 0) >= month_start]
    total_views = sum(p.get("views", 0) for p in this_month)
    total_likes = sum(p.get("likes", 0) for p in this_month)
    total_comments = sum(p.get("comments", 0) for p in this_month)
    est = estimate_earnings_gbp(total_views, rpm)
    engagement = round((total_likes + total_comments) / total_views * 100, 2) if total_views else 0.0
    return {
        "month": now.strftime("%Y-%m"),
        "video_count": len(this_month),
        "total_views": total_views,
        "total_likes": total_likes,
        "total_comments": total_comments,
        "est_earnings_gbp": est,
        "engagement_rate_pct": engagement,
        "rpm_gbp": rpm,
        "target_5k_pct": round(est / 5000 * 100, 1),
        "target_10k_pct": round(est / 10000 * 100, 1),
    }


def per_video_earnings(posted: List[Dict], rpm: float = _RPM_GBP_DEFAULT) -> List[Dict]:
    result = [
        {
            "video_id": p.get("video_id"),
            "title": p.get("title", ""),
            "views": p.get("views", 0),
            "likes": p.get("likes", 0),
            "est_earnings_gbp": estimate_earnings_gbp(p.get("views", 0), rpm),
        }
        for p in posted
    ]
    result.sort(key=lambda x: x["views"], reverse=True)
    return result

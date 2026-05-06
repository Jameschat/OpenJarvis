# src/openjarvis/tiktok/pipeline.py
"""Pipeline orchestrator — python_entry callables for in-process agents."""
from __future__ import annotations
import datetime, os
from pathlib import Path
from typing import Dict, Any

from openjarvis.tiktok.state import (
    load_settings, get_setting, set_setting,
    load_queue, save_queue, get_pending_queue, get_approved_queue,
    add_to_queue, load_posted, add_posted, update_posted_stats,
    save_finance, get_pending_comments, add_comment_reply,
)
from openjarvis.tiktok.finance import monthly_summary, per_video_earnings, estimate_earnings_gbp


def get_pipeline_state() -> Dict:
    """Full pipeline state dict for /tiktok/state endpoint."""
    settings = load_settings()
    posted = load_posted()
    finance = monthly_summary(posted, settings.get("rpm_gbp", 7.5))
    return {
        "settings": {
            "threshold": settings.get("threshold", 70),
            "kling_connected": bool(settings.get("kling_api_key")),
            "tiktok_connected": bool(settings.get("tiktok_access_token")),
        },
        "queue": {
            "pending": get_pending_queue(),
            "approved": get_approved_queue(),
        },
        "posted": posted,
        "finance": finance,
        "comments": get_pending_comments(),
    }


def _vault_videos_dir() -> Path:
    vault = os.environ.get(
        "OPENJARVIS_VAULT_PATH",
        str(Path.home() / "Claude" / "Obsidian" / "Claude" / "Brain"),
    )
    p = Path(vault) / "Content" / "Videos"
    p.mkdir(parents=True, exist_ok=True)
    return p


def video_generator_entry(task_context: Dict) -> Dict:
    """python_entry for video-generator agent."""
    from openjarvis.tiktok.video_gen import submit_job, poll_job, download_video, KlingError
    settings = load_settings()
    api_key = settings.get("kling_api_key", "")
    api_secret = settings.get("kling_api_secret", "")
    if not api_key or not api_secret:
        return {"status": "error", "message": "Kling API key not configured — add it in Settings tab"}
    title = task_context.get("title", "untitled")
    caption = task_context.get("caption", "")
    hashtags = task_context.get("hashtags", [])
    visual_prompt = task_context.get("visual_prompt", "cinematic vertical video, vibrant colors, professional quality")
    script_path = task_context.get("script_path", "")
    if script_path and Path(script_path).exists():
        script = Path(script_path).read_text()
    else:
        script = task_context.get("script", "")
    try:
        task_id = submit_job(script, visual_prompt, api_key, api_secret)
        video_url = poll_job(task_id, api_key, api_secret)
        slug = title.lower().replace(" ", "-")[:40]
        date_str = datetime.date.today().isoformat()
        video_path = _vault_videos_dir() / f"{date_str}-{slug}.mp4"
        download_video(video_url, video_path)
        entry = add_to_queue(title, script_path, str(video_path), caption, hashtags)
        return {"status": "ok", "queue_id": entry["id"], "video_path": str(video_path)}
    except KlingError as e:
        return {"status": "error", "message": str(e)}


def tiktok_publisher_entry(task_context: Dict) -> Dict:
    """python_entry for tiktok-publisher agent."""
    from openjarvis.tiktok.tiktok_client import upload_video, TikTokError
    settings = load_settings()
    access_token = settings.get("tiktok_access_token", "")
    if not access_token:
        return {"status": "error", "message": "TikTok not connected — link account in Settings tab"}
    queue_id = task_context.get("queue_id", "")
    queue = load_queue()
    entry = next((e for e in queue if e["id"] == queue_id), None)
    if not entry:
        return {"status": "error", "message": f"Queue entry {queue_id} not found"}
    hashtag_str = " ".join(f"#{h}" for h in entry.get("hashtags", []))
    full_caption = f"{entry['caption']} {hashtag_str}".strip()
    try:
        publish_id = upload_video(entry["video_path"], full_caption, access_token)
        add_posted(publish_id, entry["title"], full_caption)
        save_queue([e for e in queue if e["id"] != queue_id])
        return {"status": "ok", "video_id": publish_id}
    except TikTokError as e:
        return {"status": "error", "message": str(e)}


def stats_puller_entry(task_context: Dict) -> Dict:
    """python_entry for stats-puller agent."""
    from openjarvis.tiktok.tiktok_client import fetch_video_stats, fetch_user_info, TikTokError
    settings = load_settings()
    access_token = settings.get("tiktok_access_token", "")
    rpm = settings.get("rpm_gbp", 7.5)
    if not access_token:
        return {"status": "error", "message": "TikTok not connected"}
    posted = load_posted()
    video_ids = [p["video_id"] for p in posted if p.get("video_id")]
    updated = 0
    try:
        if video_ids:
            stats = fetch_video_stats(video_ids[:20], access_token)
            stats_by_id = {s["id"]: s for s in stats}
            for post in posted:
                vid = post.get("video_id")
                if vid and vid in stats_by_id:
                    s = stats_by_id[vid]
                    update_posted_stats(
                        vid,
                        views=s.get("view_count", post["views"]),
                        likes=s.get("like_count", post["likes"]),
                        comments=s.get("comment_count", post["comments"]),
                        est_earnings=estimate_earnings_gbp(s.get("view_count", 0), rpm),
                    )
                    updated += 1
        fresh = load_posted()
        finance = monthly_summary(fresh, rpm)
        finance["per_video"] = per_video_earnings(fresh, rpm)
        save_finance(finance)
        user = fetch_user_info(access_token)
        return {"status": "ok", "updated": updated, "finance": finance, "user": user}
    except TikTokError as e:
        return {"status": "error", "message": str(e)}

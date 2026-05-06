"""All JSON state I/O for the TikTok business module."""
from __future__ import annotations
import json, time, uuid
from pathlib import Path
from typing import Any

_TIKTOK_DIR = Path.home() / ".openjarvis" / "tiktok"


def _dir() -> Path:
    _TIKTOK_DIR.mkdir(parents=True, exist_ok=True)
    return _TIKTOK_DIR


def _load(filename: str) -> Any:
    p = _dir() / filename
    if not p.exists():
        return {} if filename == "settings.json" else []
    with open(p) as f:
        return json.load(f)


def _save(filename: str, data: Any) -> None:
    with open(_dir() / filename, "w") as f:
        json.dump(data, f, indent=2)


# ── Queue (video approval queue) ──────────────────────────────────────────────

def load_queue() -> list:
    return _load("queue.json")


def save_queue(queue: list) -> None:
    _save("queue.json", queue)


def add_to_queue(title: str, script_path: str, video_path: str,
                 caption: str, hashtags: list) -> dict:
    entry = {
        "id": str(uuid.uuid4())[:8],
        "title": title,
        "script_path": script_path,
        "video_path": video_path,
        "caption": caption,
        "hashtags": hashtags,
        "status": "pending",
        "created_at": time.time(),
    }
    queue = load_queue()
    queue.append(entry)
    save_queue(queue)
    return entry


def approve_video(video_id: str) -> bool:
    queue = load_queue()
    for e in queue:
        if e["id"] == video_id:
            e["status"] = "approved"
            save_queue(queue)
            return True
    return False


def reject_video(video_id: str) -> bool:
    queue = [e for e in load_queue() if e["id"] != video_id]
    save_queue(queue)
    return True


def get_pending_queue() -> list:
    return [e for e in load_queue() if e["status"] == "pending"]


def get_approved_queue() -> list:
    return [e for e in load_queue() if e["status"] == "approved"]


# ── Posted videos ─────────────────────────────────────────────────────────────

def load_posted() -> list:
    return _load("posted.json")


def save_posted(posted: list) -> None:
    _save("posted.json", posted)


def add_posted(video_id: str, title: str, caption: str) -> dict:
    entry = {
        "video_id": video_id,
        "title": title,
        "caption": caption,
        "posted_at": time.time(),
        "views": 0,
        "likes": 0,
        "comments": 0,
        "est_earnings": 0.0,
    }
    posted = load_posted()
    posted.append(entry)
    save_posted(posted)
    return entry


def update_posted_stats(video_id: str, views: int, likes: int,
                        comments: int, est_earnings: float) -> bool:
    posted = load_posted()
    for e in posted:
        if e["video_id"] == video_id:
            e["views"] = views
            e["likes"] = likes
            e["comments"] = comments
            e["est_earnings"] = est_earnings
            save_posted(posted)
            return True
    return False


# ── Finance ───────────────────────────────────────────────────────────────────

def load_finance() -> dict:
    return _load("finance.json") or {}


def save_finance(finance: dict) -> None:
    _save("finance.json", finance)


# ── Comments (reply approval queue) ──────────────────────────────────────────

def load_comments() -> list:
    return _load("comments.json")


def save_comments(comments: list) -> None:
    _save("comments.json", comments)


def add_comment_reply(comment_id: str, video_id: str, commenter: str,
                      original_comment: str, draft_reply: str) -> dict:
    comments = load_comments()
    # deduplicate: one pending reply per commenter per video
    for c in comments:
        if c["video_id"] == video_id and c["commenter"] == commenter and c["status"] == "pending":
            return c
    entry = {
        "id": str(uuid.uuid4())[:8],
        "comment_id": comment_id,
        "video_id": video_id,
        "commenter": commenter,
        "original_comment": original_comment,
        "draft_reply": draft_reply,
        "status": "pending",
        "created_at": time.time(),
    }
    comments.append(entry)
    save_comments(comments)
    return entry


def approve_comment(reply_id: str) -> bool:
    comments = load_comments()
    for c in comments:
        if c["id"] == reply_id:
            c["status"] = "approved"
            save_comments(comments)
            return True
    return False


def reject_comment(reply_id: str) -> bool:
    save_comments([c for c in load_comments() if c["id"] != reply_id])
    return True


def get_pending_comments() -> list:
    return [c for c in load_comments() if c["status"] == "pending"]


# ── Settings ──────────────────────────────────────────────────────────────────

def load_settings() -> dict:
    return _load("settings.json")


def save_settings(settings: dict) -> None:
    _save("settings.json", settings)


def get_setting(key: str, default: Any = None) -> Any:
    return load_settings().get(key, default)


def set_setting(key: str, value: Any) -> None:
    s = load_settings()
    s[key] = value
    save_settings(s)

# src/openjarvis/tiktok/tiktok_client.py
"""TikTok Content Posting API v2 — OAuth, upload, stats, comments."""
from __future__ import annotations
import json, os, urllib.error, urllib.parse, urllib.request
from typing import Dict, List

TIKTOK_API = "https://open.tiktokapis.com/v2"
TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"


class TikTokError(Exception):
    pass


def _hdr(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}


def build_auth_url(client_key: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_key": client_key,
        "scope": "user.info.basic,video.publish,video.upload,comment.list,comment.create",
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{TIKTOK_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(client_key: str, client_secret: str,
                  code: str, redirect_uri: str) -> Dict:
    body = urllib.parse.urlencode({
        "client_key": client_key, "client_secret": client_secret,
        "code": code, "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }).encode()
    req = urllib.request.Request(TIKTOK_TOKEN_URL, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    if "error" in data:
        raise TikTokError(f"Token exchange: {data.get('error_description', data['error'])}")
    return data


def refresh_token(client_key: str, client_secret: str, refresh_tok: str) -> Dict:
    body = urllib.parse.urlencode({
        "client_key": client_key, "client_secret": client_secret,
        "grant_type": "refresh_token", "refresh_token": refresh_tok,
    }).encode()
    req = urllib.request.Request(TIKTOK_TOKEN_URL, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def upload_video(mp4_path: str, caption: str, access_token: str) -> str:
    """Upload and publish video via Direct Post. Returns publish_id."""
    file_size = os.path.getsize(mp4_path)
    init_body = json.dumps({
        "post_info": {
            "title": caption[:2200],
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": file_size,
            "total_chunk_count": 1,
        },
    }).encode()
    req = urllib.request.Request(
        f"{TIKTOK_API}/post/publish/video/init/",
        data=init_body, headers=_hdr(access_token), method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        init_data = json.loads(resp.read())
    if init_data.get("error", {}).get("code") != "ok":
        raise TikTokError(f"Init failed: {init_data}")
    publish_id = init_data["data"]["publish_id"]
    upload_url = init_data["data"]["upload_url"]
    with open(mp4_path, "rb") as f:
        video_bytes = f.read()
    put_req = urllib.request.Request(upload_url, data=video_bytes, method="PUT")
    put_req.add_header("Content-Type", "video/mp4")
    put_req.add_header("Content-Range", f"bytes 0-{file_size - 1}/{file_size}")
    with urllib.request.urlopen(put_req, timeout=120):
        pass
    return publish_id


def fetch_video_stats(video_ids: List[str], access_token: str) -> List[Dict]:
    body = json.dumps({
        "filters": {"video_ids": video_ids},
        "fields": ["id", "view_count", "like_count", "comment_count"],
    }).encode()
    req = urllib.request.Request(
        f"{TIKTOK_API}/video/query/", data=body,
        headers=_hdr(access_token), method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read()).get("data", {}).get("videos", [])


def fetch_user_info(access_token: str) -> Dict:
    req = urllib.request.Request(
        f"{TIKTOK_API}/user/info/?fields=follower_count,following_count,likes_count,video_count",
        headers=_hdr(access_token),
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read()).get("data", {}).get("user", {})


def fetch_comments(video_id: str, access_token: str,
                   cursor: int = 0, count: int = 50) -> List[Dict]:
    body = json.dumps({"video_id": video_id, "cursor": cursor, "count": count}).encode()
    req = urllib.request.Request(
        f"{TIKTOK_API}/comment/list/", data=body,
        headers=_hdr(access_token), method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read()).get("data", {}).get("comments", [])


def post_comment(video_id: str, text: str, access_token: str) -> str:
    """Post a reply comment. Returns comment_id."""
    body = json.dumps({"video_id": video_id, "text": text}).encode()
    req = urllib.request.Request(
        f"{TIKTOK_API}/comment/create/", data=body,
        headers=_hdr(access_token), method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    if data.get("error", {}).get("code") != "ok":
        raise TikTokError(f"Comment failed: {data}")
    return data["data"]["comment_id"]

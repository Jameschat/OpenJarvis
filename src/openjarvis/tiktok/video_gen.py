# src/openjarvis/tiktok/video_gen.py
"""Kling AI text-to-video API client (v1)."""
from __future__ import annotations
import base64, hashlib, hmac, json, time, urllib.error, urllib.request
from pathlib import Path

KLING_API_BASE = "https://api.klingai.com"


class KlingError(Exception):
    pass


def _jwt(api_key: str, api_secret: str) -> str:
    now = int(time.time())
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps({"iss": api_key, "exp": now + 1800, "nbf": now}).encode()
    ).rstrip(b"=").decode()
    sig_input = f"{header}.{payload}".encode()
    sig = hmac.new(api_secret.encode(), sig_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"


def _headers(api_key: str, api_secret: str) -> dict:
    return {"Authorization": f"Bearer {_jwt(api_key, api_secret)}",
            "Content-Type": "application/json"}


def submit_job(script: str, visual_prompt: str, api_key: str,
               api_secret: str, duration: int = 5) -> str:
    """Submit text-to-video job. Returns task_id."""
    body = json.dumps({
        "model_name": "kling-v1",
        "prompt": f"{visual_prompt}\n\nNarration: {script[:200]}",
        "negative_prompt": "text overlay, subtitles, watermark, blurry, low quality",
        "cfg_scale": 0.5,
        "mode": "std",
        "duration": str(duration),
        "aspect_ratio": "9:16",
    }).encode()
    req = urllib.request.Request(
        f"{KLING_API_BASE}/v1/videos/text2video",
        data=body, headers=_headers(api_key, api_secret), method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise KlingError(f"HTTP {e.code}: {e.read().decode()}")
    if data.get("code") != 0:
        raise KlingError(data.get("message", "unknown error"))
    return data["data"]["task_id"]


def poll_job(task_id: str, api_key: str, api_secret: str,
             max_wait: int = 600) -> str:
    """Poll until complete. Returns video URL."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        req = urllib.request.Request(
            f"{KLING_API_BASE}/v1/videos/text2video/{task_id}",
            headers=_headers(api_key, api_secret),
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        status = data["data"]["task_status"]
        if status == "succeed":
            return data["data"]["task_result"]["videos"][0]["url"]
        if status == "failed":
            raise KlingError(data["data"].get("task_status_msg", "job failed"))
        time.sleep(15)
    raise KlingError(f"Kling job timed out after {max_wait}s")


def download_video(url: str, dest_path: Path) -> Path:
    """Download MP4 to dest_path. Returns path."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, str(dest_path))
    return dest_path

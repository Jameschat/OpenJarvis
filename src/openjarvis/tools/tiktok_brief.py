"""TikTok briefing tool — fetch video, transcribe locally with whisper,
describe keyframes with vision, summarise into a structured note in
Brain/Knowledge/.

Mirrors the youtube_brief_url pattern. Differences:
- No public transcript API; download audio + faster-whisper locally.
- Always extract 4 keyframes for vision (TikTok is visual-heavy).
- Cost cap $0.10/run (only LLM calls cost money; whisper is local).

Wired by ToolRegistry as `tiktok_brief_url`. Voice fast-path lives in
`tiktok_pilot.py` (separate module so heavy deps don't load on every
voice turn).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# Matches the canonical TikTok video URL: https://www.tiktok.com/@<user>/video/<id>
_LONG_URL_RE = re.compile(
    r"https?://(?:www\.)?tiktok\.com/@[\w.\-]+/video/(?P<id>\d+)",
    re.IGNORECASE,
)
_SHORT_URL_RE = re.compile(
    r"https?://vm\.tiktok\.com/[\w]+/?",
    re.IGNORECASE,
)


def _extract_video_id(url: str) -> Optional[str]:
    """Extract numeric video id from a long TikTok URL. Returns None for
    short-links (caller must resolve first), or for unrelated URLs."""
    if not url or not isinstance(url, str):
        return None
    m = _LONG_URL_RE.search(url)
    return m.group("id") if m else None


def _resolve_short_url(url: str) -> str:
    """Follow a vm.tiktok.com 301 redirect to the canonical URL. Long
    URLs are passed through unchanged."""
    if not _SHORT_URL_RE.match(url):
        return url
    try:
        import httpx
        resp = httpx.head(url, follow_redirects=True, timeout=10.0)
        return str(resp.url)
    except Exception:
        logger.exception("tiktok: short-url resolve failed for %s", url)
        return url


def _fetch_meta(url: str) -> Optional[Dict[str, Any]]:
    """Use yt-dlp's info-only mode to extract metadata. Returns None
    on failure (region-blocked, deleted, network down)."""
    import yt_dlp
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        logger.exception("tiktok: yt-dlp meta fetch failed for %s", url)
        return None
    if not isinstance(info, dict):
        return None
    # Normalise to a known shape — TikTok extractor sometimes adds extra fields.
    return {
        "id": info.get("id"),
        "title": info.get("title") or "",
        "description": info.get("description") or "",
        "uploader": info.get("uploader") or info.get("creator") or "",
        "duration": info.get("duration") or 0,
        "webpage_url": info.get("webpage_url") or url,
    }


# ---------------------------------------------------------------------------
# Audio download + transcription
# ---------------------------------------------------------------------------


def _download_audio(url: str, dest_dir):
    """Download audio-only to dest_dir using yt-dlp. Returns the resulting
    file path or None on failure."""
    from pathlib import Path
    import yt_dlp
    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": str(Path(dest_dir) / "%(id)s.%(ext)s"),
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
    except Exception:
        logger.exception("tiktok: yt-dlp audio download failed for %s", url)
        return None
    p = Path(filename)
    return p if p.exists() else None


# Cache the whisper model so repeated calls in one session don't reload it.
_WHISPER_MODEL = None


def _get_whisper_model():
    """Lazy-init faster-whisper. Prefers GPU; falls back to CPU."""
    global _WHISPER_MODEL
    if _WHISPER_MODEL is not None:
        return _WHISPER_MODEL
    from faster_whisper import WhisperModel
    import os
    model_size = os.environ.get("OPENJARVIS_WHISPER_MODEL", "large-v3")
    device = os.environ.get("OPENJARVIS_WHISPER_DEVICE", "cuda")
    compute_type = os.environ.get("OPENJARVIS_WHISPER_COMPUTE", "float16")
    try:
        _WHISPER_MODEL = WhisperModel(model_size, device=device, compute_type=compute_type)
    except Exception:
        logger.warning("tiktok: whisper GPU init failed, falling back to CPU")
        _WHISPER_MODEL = WhisperModel(model_size, device="cpu", compute_type="int8")
    return _WHISPER_MODEL


def _transcribe_audio(audio_path):
    """Transcribe via faster-whisper. Returns (text, language, duration_s).
    Empty text on silence / music-only is a valid success path."""
    model = _get_whisper_model()
    segments, info = model.transcribe(str(audio_path), beam_size=5)
    pieces = []
    for seg in segments:
        pieces.append(seg.text)
    text = "".join(pieces).strip()
    return text, getattr(info, "language", None), float(getattr(info, "duration", 0.0))


# ---------------------------------------------------------------------------
# Keyframe extraction + vision description
# ---------------------------------------------------------------------------


# Cost per 1k tokens for gpt-4o-mini vision (May 2026 pricing).
# Vision images count as ~85 tokens each at low detail.
_VISION_COST_PER_IMAGE_USD = 0.0001  # rough; conservative


def _extract_keyframes(video_path, *, duration_s: float, n: int = 4):
    """Use ffmpeg to extract `n` evenly-spaced keyframes. Returns list of
    JPEG paths, oldest to newest. Returns [] for zero-duration videos."""
    from pathlib import Path
    import subprocess
    if duration_s <= 0.0:
        return []
    # Sample at 10%, 40%, 60%, 90% of duration for n=4. Generalises:
    # i/(n+1) * duration for i in 1..n would be evenly spaced; slightly
    # off-edge to skip blank intro/outro frames is the chosen heuristic.
    fractions = [0.1, 0.4, 0.6, 0.9] if n == 4 else [
        (i + 1) / (n + 1) for i in range(n)
    ]
    paths = []
    base = Path(video_path).parent
    stem = Path(video_path).stem
    for i, frac in enumerate(fractions[:n]):
        ts = duration_s * frac
        out_path = base / f"{stem}_kf{i:02d}.jpg"
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-ss", f"{ts:.2f}",
                    "-i", str(video_path),
                    "-frames:v", "1",
                    "-q:v", "2",
                    str(out_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception:
            logger.exception("tiktok: ffmpeg keyframe extraction failed at ts=%s", ts)
            continue
        if result.returncode == 0 and out_path.exists():
            paths.append(out_path)
    return paths


def _describe_keyframes(paths):
    """Call gpt-4o-mini vision per frame. Returns (descriptions, cost_usd).
    On per-frame failure: skip that frame, continue with the rest."""
    if not paths:
        return [], 0.0
    import base64
    import os
    try:
        from openai import OpenAI
    except ImportError:
        return [], 0.0
    base_url = os.environ.get("OPENAI_BASE_URL", "http://localhost:4000")
    api_key = os.environ.get("OPENAI_API_KEY", "sk-noop")
    client = OpenAI(base_url=base_url, api_key=api_key)
    descriptions = []
    cost = 0.0
    prompt = (
        "Describe this TikTok video frame in 1-2 sentences. Focus on: "
        "visible text overlays, what the person/subject is doing, any "
        "demo or product on screen. Skip generic descriptions of style "
        "or 'a person on TikTok'."
    )
    for p in paths:
        try:
            with open(p, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}",
                            "detail": "low",
                        }},
                    ],
                }],
                max_tokens=120,
                timeout=60,
            )
            descriptions.append(resp.choices[0].message.content.strip())
            cost += _VISION_COST_PER_IMAGE_USD
        except Exception:
            logger.exception("tiktok: vision call failed for %s", p)
            continue
    return descriptions, cost


__all__ = [
    "_extract_video_id",
    "_resolve_short_url",
    "_fetch_meta",
    "_download_audio",
    "_transcribe_audio",
    "_extract_keyframes",
    "_describe_keyframes",
]

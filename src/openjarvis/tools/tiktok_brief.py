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


__all__ = [
    "_extract_video_id",
    "_resolve_short_url",
    "_fetch_meta",
    "_download_audio",
    "_transcribe_audio",
]

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


# ---------------------------------------------------------------------------
# Summariser + writer + tool registration
# ---------------------------------------------------------------------------


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, *, max_len: int = 60) -> str:
    s = _SLUG_STRIP.sub("-", (text or "").lower()).strip("-")
    if not s:
        return "tiktok"
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s or "tiktok"


def _build_summary_prompt(
    *,
    meta: Dict[str, Any],
    transcript: str,
    vision_notes: list,
) -> str:
    duration = int(meta.get("duration") or 0)
    duration_str = f"{duration // 60:02d}:{duration % 60:02d}" if duration else "unknown"
    vision_block = (
        "\n".join(f"- {n}" for n in vision_notes)
        if vision_notes else "(no vision frames captured)"
    )
    transcript_block = transcript.strip() if transcript.strip() else "(no audio transcribed — silent or music-only)"
    return (
        "[ROLE]\n"
        "You are summarising a short TikTok video for the operator's "
        "knowledge vault. Produce a structured briefing.\n\n"
        f"[META]\n"
        f"creator: {meta.get('uploader', 'unknown')}\n"
        f"title/caption: {meta.get('title', '(untitled)')}\n"
        f"duration: {duration_str}\n"
        f"url: {meta.get('webpage_url', '')}\n\n"
        f"[TRANSCRIPT]\n{transcript_block}\n\n"
        f"[VISION NOTES — what's on screen across keyframes]\n{vision_block}\n\n"
        "[OUTPUT CONTRACT]\n"
        "Produce markdown matching this structure exactly:\n\n"
        "## TL;DR\n"
        "Two-sentence summary of what the video shows / argues / demonstrates.\n\n"
        "## Key takeaways\n"
        "- 3-5 bullets.\n\n"
        "## Notable quotes\n"
        "- 0-3 short verbatim quotes from the transcript, each ≤140 chars. "
        "Skip the section if transcript is empty.\n\n"
        "## Implied actions\n"
        "- 0-3 bullets describing what the operator might do based on this "
        "(e.g. try the technique, follow the creator, save the recipe). "
        "Skip if no clear action.\n"
    )


def _llm_summarise(prompt: str):
    """Call gpt-4o-mini for summary. Returns (briefing_md, cost_usd)."""
    import os
    try:
        from openai import OpenAI
    except ImportError:
        return "", 0.0
    base_url = os.environ.get("OPENAI_BASE_URL", "http://localhost:4000")
    api_key = os.environ.get("OPENAI_API_KEY", "sk-noop")
    client = OpenAI(base_url=base_url, api_key=api_key)
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "user", "content": prompt},
            ],
            max_tokens=1200,
            timeout=60,
        )
    except Exception:
        logger.exception("tiktok: summary LLM call failed")
        return "", 0.0
    if not resp.choices:
        return "", 0.0
    # Rough cost: prompt + completion at gpt-4o-mini rates, ~$0.0003/run typical.
    return resp.choices[0].message.content.strip(), 0.0005


def _brain_knowledge_dir():
    """Indirection so tests can monkey-patch the vault path."""
    from openjarvis.tools.obsidian_brain import KNOWLEDGE_DIR
    return KNOWLEDGE_DIR


def _write_brief_to_vault(
    *,
    meta: Dict[str, Any],
    briefing: str,
    transcript: str,
    vision_notes: list,
    cost_usd: float,
    now,
):
    """Write the briefing markdown to Brain/Knowledge/. Returns the path or None."""
    knowledge_dir = _brain_knowledge_dir()
    if knowledge_dir is None:
        return None
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    date_str = now.strftime("%Y-%m-%d")
    slug = _slugify(meta.get("title") or meta.get("uploader") or "tiktok")
    path = knowledge_dir / f"{date_str} - briefing - tiktok - {slug}.md"
    duration = int(meta.get("duration") or 0)
    frontmatter = (
        "---\n"
        "type: briefing\n"
        "source: tiktok\n"
        f"video_id: {meta.get('id', '')}\n"
        f"creator: {meta.get('uploader', '')}\n"
        f"duration_s: {duration}\n"
        f"url: {meta.get('webpage_url', '')}\n"
        f"date: {date_str}\n"
        f"cost_usd: {cost_usd:.4f}\n"
        "---\n\n"
        f"# TikTok briefing — @{meta.get('uploader', 'unknown')}\n\n"
    )
    transcript_section = (
        "## Transcript\n\n"
        f"{transcript.strip() if transcript.strip() else '(no audio — silent or music-only)'}\n\n"
    )
    visual_section = "## Visual cues\n\n"
    if vision_notes:
        for i, note in enumerate(vision_notes, start=1):
            visual_section += f"{i}. {note}\n"
    else:
        visual_section += "(no keyframes extracted)\n"
    visual_section += "\n"
    body = briefing.strip() + "\n\n" + transcript_section + visual_section
    body += f"_Compiled by `tiktok_brief_url`. Cost ${cost_usd:.4f}._\n"
    path.write_text(frontmatter + body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Public callable + ToolRegistry registration
# ---------------------------------------------------------------------------


def tiktok_brief_url(url: str, *, save: bool = True) -> Dict[str, Any]:
    """End-to-end pipeline. Returns a dict with the briefing + path + cost.
    Public callable separate from the BaseTool wrapper so internal callers
    (study-agent, manual scripts) can invoke without ToolRegistry plumbing."""
    from datetime import datetime, timezone
    from pathlib import Path
    import tempfile

    resolved = _resolve_short_url(url)
    if not _extract_video_id(resolved):
        return {"ok": False, "error": "could not parse TikTok URL", "url": url}

    meta = _fetch_meta(resolved)
    if not meta or not meta.get("id"):
        return {"ok": False, "error": "yt-dlp metadata fetch failed (region-blocked? deleted?)", "url": resolved}

    cost_total = 0.0
    transcript = ""
    vision_notes = []

    with tempfile.TemporaryDirectory(prefix="tiktok_") as tmp:
        tmp_path = Path(tmp)
        audio = _download_audio(resolved, dest_dir=tmp_path)
        if audio is None:
            return {"ok": False, "error": "audio download failed", "url": resolved}
        transcript, _lang, duration_actual = _transcribe_audio(audio)

        # Use yt-dlp's reported duration if whisper didn't return one
        dur = duration_actual or float(meta.get("duration") or 0)
        # Keyframes need a video file. yt-dlp's `bestaudio/best` for TikTok
        # may yield .mp4 (combined audio+video) or .m4a (audio-only). If
        # the file is .mp4/.webm/.mov we can extract frames; otherwise skip.
        if audio.suffix.lower() in (".mp4", ".webm", ".mov"):
            keyframes = _extract_keyframes(audio, duration_s=dur)
        else:
            keyframes = []
        if keyframes:
            vision_notes, vision_cost = _describe_keyframes(keyframes)
            cost_total += vision_cost

        prompt = _build_summary_prompt(
            meta=meta, transcript=transcript, vision_notes=vision_notes,
        )
        briefing, summary_cost = _llm_summarise(prompt)
        cost_total += summary_cost

    written_path = None
    if save and briefing:
        written_path = _write_brief_to_vault(
            meta=meta, briefing=briefing, transcript=transcript,
            vision_notes=vision_notes, cost_usd=cost_total,
            now=datetime.now(timezone.utc),
        )

    return {
        "ok": bool(briefing),
        "briefing": briefing,
        "path": str(written_path) if written_path else None,
        "transcript_chars": len(transcript),
        "vision_frame_count": len(vision_notes),
        "cost_usd": round(cost_total, 4),
        "meta": meta,
    }


# Tool registration -- mirrors youtube_brief.py imports
try:
    import time
    from openjarvis.core.registry import ToolRegistry
    from openjarvis.core.types import ToolResult
    from openjarvis.tools._stubs import BaseTool, ToolSpec
    _REGISTRY_AVAILABLE = True
except ImportError:
    _REGISTRY_AVAILABLE = False


if _REGISTRY_AVAILABLE:

    @ToolRegistry.register("tiktok_brief_url")
    class TikTokBriefTool(BaseTool):
        """Fetch a TikTok video, transcribe locally with whisper, describe
        keyframes with vision, and write a briefing to Obsidian. Mirrors
        youtube_brief_url for the TikTok platform."""

        tool_id = "tiktok_brief_url"
        is_local = False

        @property
        def spec(self) -> "ToolSpec":
            return ToolSpec(
                name="tiktok_brief_url",
                description=(
                    "Fetch a TikTok video (vm.tiktok.com or tiktok.com URL), "
                    "transcribe its audio locally via faster-whisper, describe "
                    "4 keyframes via gpt-4o-mini vision, and produce a "
                    "structured briefing — TL;DR, key takeaways, notable "
                    "quotes, implied actions. Briefing is saved to "
                    "Brain/Knowledge/. Use when the operator says "
                    "'brief this TikTok', 'summarise this TikTok', or pastes a TikTok URL."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "TikTok video URL (long or vm.tiktok.com short-link)",
                        },
                        "save_to_vault": {
                            "type": "boolean",
                            "description": (
                                "If true (default), write the briefing to "
                                "Brain/Knowledge/<date> - briefing - tiktok - <slug>.md. "
                                "Set false if you only want the text returned."
                            ),
                        },
                    },
                    "required": ["url"],
                },
                category="research",
                cost_estimate=0.001,
                latency_estimate=60.0,
                timeout_seconds=180.0,
            )

        def execute(self, **params: Any) -> "ToolResult":
            t0 = time.time()
            url = (params.get("url") or "").strip()
            save = params.get("save_to_vault", True)
            result = tiktok_brief_url(url, save=save)
            return ToolResult(
                tool_name=self.tool_id,
                content=result.get("briefing") or result.get("error", "unknown error"),
                success=bool(result.get("ok")),
                metadata={
                    "path": result.get("path"),
                    "transcript_chars": result.get("transcript_chars"),
                    "vision_frame_count": result.get("vision_frame_count"),
                    "cost_usd": result.get("cost_usd"),
                    "meta": result.get("meta"),
                },
                latency_seconds=time.time() - t0,
            )


__all__ = [
    "_extract_video_id",
    "_resolve_short_url",
    "_fetch_meta",
    "_download_audio",
    "_transcribe_audio",
    "_extract_keyframes",
    "_describe_keyframes",
    "_slugify",
    "_build_summary_prompt",
    "_write_brief_to_vault",
    "tiktok_brief_url",
]

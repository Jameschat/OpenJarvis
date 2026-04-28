"""youtube_brief — fetch a YouTube transcript and write a structured briefing.

Used by the browser-pilot agent to deliver on operator commands like
"watch this video and brief me." The browser pilot itself just navigates
to the page (so the operator can see the same thing the agent saw); this
tool runs separately to extract a transcript and summarise it.

Two-tier transcript fetch:
  Tier 1 — youtube-transcript-api (free, fast, ~70% of videos have captions).
  Tier 2 — yt-dlp + faster-whisper (any video, but slow). Reserved for a
           future iteration; for now Tier 2 returns a clear error and the
           operator can decide whether to retry with a different video.

Briefing summariser uses gpt-4o-mini (cheap), chunks long transcripts at
~8000 characters, meta-summarises chunks. Output lands in:

    Brain/Knowledge/<date> - briefing - <title-slug>.md

with frontmatter so it shows up correctly in graphify and Obsidian's
graph view alongside the rest of the operator's knowledge notes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_SUMMARY_MODEL = os.environ.get("OPENJARVIS_BRIEF_MODEL", "gpt-4o-mini")
# Chunk threshold — gpt-4o-mini handles 128k context, so this is for
# practical brevity, not capacity. Long transcripts get split, summarised
# per-chunk, then a final meta-summary collapses the chunk briefings.
_CHUNK_CHARS = int(os.environ.get("OPENJARVIS_BRIEF_CHUNK_CHARS", "8000"))
# Hard cap on briefing cost — gpt-4o-mini is so cheap this is mostly
# psychological insurance. $0.15/1M input + $0.60/1M output → a 30-min
# video transcript (~30k chars ≈ 8k tokens) costs ~$0.005 to brief.
_MAX_COST_USD = float(os.environ.get("OPENJARVIS_BRIEF_BUDGET_USD", "0.10"))
_PRICE_IN_PER_1M = float(os.environ.get("OPENJARVIS_BRIEF_PRICE_IN", "0.15"))
_PRICE_OUT_PER_1M = float(os.environ.get("OPENJARVIS_BRIEF_PRICE_OUT", "0.60"))


# ---------------------------------------------------------------------------
# URL parsing & metadata
# ---------------------------------------------------------------------------

_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com",
             "youtu.be", "music.youtube.com"}


def _extract_video_id(url_or_id: str) -> Optional[str]:
    """Pull the 11-char video id from any of YouTube's URL shapes, or
    return the input unchanged if it already looks like a bare id."""
    s = (url_or_id or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    try:
        u = urlparse(s if "://" in s else "https://" + s)
    except Exception:
        return None
    host = (u.hostname or "").lower()
    if host not in _YT_HOSTS:
        return None
    if host == "youtu.be":
        seg = u.path.strip("/").split("/")[0]
        return seg if re.fullmatch(r"[A-Za-z0-9_-]{11}", seg) else None
    if u.path == "/watch":
        v = parse_qs(u.query).get("v", [None])[0]
        return v if v and re.fullmatch(r"[A-Za-z0-9_-]{11}", v) else None
    # /shorts/<id> or /embed/<id> or /v/<id>
    parts = [p for p in u.path.split("/") if p]
    if len(parts) >= 2 and parts[0] in ("shorts", "embed", "v"):
        cand = parts[1]
        return cand if re.fullmatch(r"[A-Za-z0-9_-]{11}", cand) else None
    return None


def _fetch_oembed_meta(video_id: str) -> Dict[str, Any]:
    """Title + author from YouTube's public oEmbed endpoint. No auth."""
    import urllib.request
    url = (
        f"https://www.youtube.com/oembed"
        f"?url=https://www.youtube.com/watch?v={video_id}"
        f"&format=json"
    )
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (compatible; jarvis/0.1)"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        return {
            "title": data.get("title") or "",
            "author": data.get("author_name") or "",
            "thumbnail_url": data.get("thumbnail_url") or "",
        }
    except Exception as exc:
        logger.warning("youtube_brief: oembed fetch failed for %s: %s", video_id, exc)
        return {"title": "", "author": "", "thumbnail_url": ""}


# ---------------------------------------------------------------------------
# Transcript fetch — Tier 1 (captions API)
# ---------------------------------------------------------------------------


def _fetch_transcript_via_api(video_id: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Returns (snippets, error). Snippets are dicts {text, start, duration}."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None, "youtube-transcript-api not installed"
    try:
        # The 1.x API uses an instance .fetch() method and returns a
        # FetchedTranscript object with a .snippets list. Older 0.x
        # exposes a classmethod get_transcript(). Try new first.
        try:
            api = YouTubeTranscriptApi()
            fetched = api.fetch(video_id)
            snippets = []
            for s in fetched.snippets:
                snippets.append({
                    "text": getattr(s, "text", "") or "",
                    "start": float(getattr(s, "start", 0.0) or 0.0),
                    "duration": float(getattr(s, "duration", 0.0) or 0.0),
                })
            return snippets, None
        except AttributeError:
            # Fallback to 0.x classmethod API
            raw = YouTubeTranscriptApi.get_transcript(video_id)
            return [
                {"text": r.get("text", ""),
                 "start": float(r.get("start", 0.0)),
                 "duration": float(r.get("duration", 0.0))}
                for r in raw
            ], None
    except Exception as exc:
        # Common shapes: TranscriptsDisabled, NoTranscriptFound, VideoUnavailable
        return None, f"{type(exc).__name__}: {exc}"


def _format_transcript_with_timestamps(snippets: List[Dict[str, Any]]) -> str:
    """Render snippets as 'mm:ss  text' lines, one per snippet, so the
    summariser can cite timestamps."""
    out_lines: List[str] = []
    for s in snippets:
        secs = int(s.get("start", 0) or 0)
        mm, ss = divmod(secs, 60)
        out_lines.append(f"{mm:02d}:{ss:02d}  {(s.get('text') or '').strip()}")
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# Summarisation
# ---------------------------------------------------------------------------

_BRIEF_SYSTEM_PROMPT = """You are JARVIS Briefing Writer.

Produce a structured markdown briefing of a YouTube video for an operator who wants to learn the gist without watching. The transcript is provided line-by-line, each line prefixed with mm:ss timestamps.

Output format (markdown, in this exact order):

## TL;DR
One paragraph, 3-5 sentences, capturing the central point.

## Key takeaways
5-7 bullet points. Each ends with a timestamp like "(12:34)" referencing where in the video this is discussed. Choose the most useful, concrete points. No filler.

## Notable quotes
2-4 direct quotes from the transcript, each with a timestamp. Quote verbatim.

## What to actually do
If the video implies clear actions for the operator, list them as bullets. If the video is purely informational with no actionable advice, write "_No specific actions implied._" and nothing else.

Discipline:
- Be honest. If the video is thin or padded, say so in TL;DR.
- Don't fabricate timestamps. If unsure, omit.
- Don't add conclusions, "in summary" sections, or motivational filler.
- British English spelling.
"""

_CHUNK_PROMPT = """You are summarising a chunk of a longer video transcript for later meta-summarisation. Capture the substantive points and quotable lines from THIS chunk only. Output 4-8 bullet points, each ending with a (mm:ss) timestamp from the transcript. No preamble. No conclusion."""


def _chunk(text: str, size: int) -> List[str]:
    """Split text into chunks of approximately `size` characters at line
    boundaries — never mid-line so timestamps stay aligned."""
    chunks: List[str] = []
    buf: List[str] = []
    cur = 0
    for line in text.splitlines():
        if cur + len(line) + 1 > size and buf:
            chunks.append("\n".join(buf))
            buf, cur = [], 0
        buf.append(line)
        cur += len(line) + 1
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def _llm_call(client, model: str, messages: List[Dict[str, Any]],
              cost_acc: List[float]) -> str:
    """Single chat completion. Mutates cost_acc[0] with running USD total."""
    resp = client.chat.completions.create(
        model=model, messages=messages, temperature=0.3,
    )
    usage = resp.usage
    pt = getattr(usage, "prompt_tokens", 0) or 0
    ct = getattr(usage, "completion_tokens", 0) or 0
    cost_acc[0] += (pt / 1_000_000) * _PRICE_IN_PER_1M + (ct / 1_000_000) * _PRICE_OUT_PER_1M
    return resp.choices[0].message.content or ""


def _summarise(transcript_text: str, meta: Dict[str, Any]) -> Tuple[str, float]:
    """Return (briefing_markdown, cost_usd). Chunks long transcripts."""
    try:
        from openai import OpenAI
    except ImportError:
        return ("_(openai package not installed — cannot summarise)_", 0.0)
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return ("_(OPENAI_API_KEY not set — cannot summarise)_", 0.0)
    client = OpenAI(api_key=api_key)
    cost_acc = [0.0]

    title = meta.get("title") or "(untitled)"
    author = meta.get("author") or "(unknown channel)"
    url = meta.get("url") or ""

    if len(transcript_text) <= _CHUNK_CHARS:
        # One-shot summary
        msgs = [
            {"role": "system", "content": _BRIEF_SYSTEM_PROMPT},
            {"role": "user",
             "content": (
                 f"Title: {title}\nChannel: {author}\nURL: {url}\n\n"
                 f"Transcript:\n{transcript_text}"
             )},
        ]
        return _llm_call(client, _SUMMARY_MODEL, msgs, cost_acc), cost_acc[0]

    # Multi-chunk path: per-chunk bullet summaries → meta brief
    chunks = _chunk(transcript_text, _CHUNK_CHARS)
    chunk_summaries: List[str] = []
    for i, ch in enumerate(chunks, 1):
        if cost_acc[0] >= _MAX_COST_USD:
            chunk_summaries.append(f"_(chunk {i}/{len(chunks)} skipped — budget cap)_")
            continue
        msgs = [
            {"role": "system", "content": _CHUNK_PROMPT},
            {"role": "user",
             "content": f"Chunk {i}/{len(chunks)} of transcript for: {title}\n\n{ch}"},
        ]
        chunk_summaries.append(_llm_call(client, _SUMMARY_MODEL, msgs, cost_acc))

    joined = "\n\n".join(
        f"### Chunk {i}\n{s}" for i, s in enumerate(chunk_summaries, 1)
    )
    msgs = [
        {"role": "system", "content": _BRIEF_SYSTEM_PROMPT},
        {"role": "user",
         "content": (
             f"Title: {title}\nChannel: {author}\nURL: {url}\n\n"
             f"Below are bullet-summaries of consecutive chunks of the "
             f"transcript. Use them to produce the final briefing in the "
             f"required format. Cite timestamps the chunk summaries already "
             f"identified.\n\n{joined}"
         )},
    ]
    return _llm_call(client, _SUMMARY_MODEL, msgs, cost_acc), cost_acc[0]


# ---------------------------------------------------------------------------
# Vault write — Brain/Knowledge/<date> - briefing - <slug>.md
# ---------------------------------------------------------------------------


def _slugify(text: str, max_len: int = 60) -> str:
    s = re.sub(r"[^A-Za-z0-9 \-]+", "", text or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    return s[:max_len].strip("-") or "untitled"


def _write_briefing_to_vault(meta: Dict[str, Any], briefing: str,
                             transcript_chars: int, cost_usd: float) -> Optional[str]:
    """Write the briefing to Brain/Knowledge/. Returns the absolute path
    or None if the vault isn't reachable."""
    try:
        from openjarvis.tools.obsidian_brain import KNOWLEDGE_DIR, BRAIN_ROOT
    except ImportError:
        return None
    if not BRAIN_ROOT.exists():
        return None
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    slug = _slugify(meta.get("title", ""))
    fname = f"{today} - briefing - {slug}.md"
    out = KNOWLEDGE_DIR / fname

    frontmatter = (
        "---\n"
        f"type: briefing\n"
        "tags: [briefing, youtube, video, knowledge, browser-pilot]\n"
        f"date: {today}\n"
        f"title: {json.dumps(meta.get('title', ''))}\n"
        f"channel: {json.dumps(meta.get('author', ''))}\n"
        f"url: {meta.get('url', '')}\n"
        f"video_id: {meta.get('video_id', '')}\n"
        f"transcript_chars: {transcript_chars}\n"
        f"summary_cost_usd: {cost_usd:.4f}\n"
        "parent: [[00 Index]]\n"
        "---\n\n"
    )
    body = (
        f"# Briefing: {meta.get('title', '(untitled)')}\n\n"
        f"> Channel: **{meta.get('author', '(unknown)')}**  ·  "
        f"[Watch on YouTube]({meta.get('url', '')})\n\n"
        f"{briefing.strip()}\n"
    )
    try:
        out.write_text(frontmatter + body, encoding="utf-8")
        return str(out)
    except Exception as exc:
        logger.exception("youtube_brief: failed to write %s: %s", out, exc)
        return None


# ---------------------------------------------------------------------------
# Public tool — youtube_brief_url
# ---------------------------------------------------------------------------


@ToolRegistry.register("youtube_brief_url")
class YouTubeBriefTool(BaseTool):
    """Fetch a YouTube transcript and write a structured briefing to the
    Obsidian vault. Returns the briefing markdown in the result content."""

    tool_id = "youtube_brief_url"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="youtube_brief_url",
            description=(
                "Fetch a YouTube video's transcript (via captions API) and "
                "produce a structured briefing — TL;DR, key takeaways with "
                "timestamps, notable quotes, and any implied actions. The "
                "briefing is also saved to the operator's Obsidian vault "
                "under Brain/Knowledge/. Use this when the operator asks "
                "you to 'watch X and brief me' or 'summarise this video'. "
                "Works only for YouTube videos that have captions; videos "
                "without captions return an error and you should report "
                "that to the operator."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": (
                            "Full YouTube URL (youtube.com/watch?v=..., "
                            "youtu.be/..., or shorts URL) OR the bare 11-char "
                            "video id."
                        ),
                    },
                    "save_to_vault": {
                        "type": "boolean",
                        "description": (
                            "If true (default), write the briefing to "
                            "Brain/Knowledge/<date> - briefing - <slug>.md. "
                            "Set false if you only want the text returned."
                        ),
                    },
                },
                "required": ["url"],
            },
            category="research",
            cost_estimate=0.005,
            latency_estimate=10.0,
            timeout_seconds=180.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        t0 = time.time()
        url = (params.get("url") or "").strip()
        save = params.get("save_to_vault", True)

        video_id = _extract_video_id(url)
        if not video_id:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"Could not extract a YouTube video id from {url!r}",
                success=False,
                latency_seconds=time.time() - t0,
            )

        # Tier-1 transcript fetch
        snippets, err = _fetch_transcript_via_api(video_id)
        if snippets is None:
            return ToolResult(
                tool_name=self.tool_id,
                content=(
                    f"No transcript available for video {video_id}. "
                    f"Reason: {err}. The yt-dlp+whisper fallback isn't "
                    f"wired in this build — try a different video, or "
                    f"the operator can run `youtube_brief_url` manually "
                    f"with a video that has captions."
                ),
                success=False,
                metadata={"video_id": video_id, "error": err},
                latency_seconds=time.time() - t0,
            )

        # Metadata
        meta = _fetch_oembed_meta(video_id)
        meta["url"] = f"https://www.youtube.com/watch?v={video_id}"
        meta["video_id"] = video_id

        # Render transcript with timestamps
        transcript_text = _format_transcript_with_timestamps(snippets)
        # Quick guard against absurd length (e.g. 4-hour podcast). 200k chars
        # ~ 50k tokens; gpt-4o-mini will still handle it (128k context),
        # but chunking covers the worst case.
        briefing, cost = _summarise(transcript_text, meta)

        path = None
        if save:
            path = _write_briefing_to_vault(
                meta, briefing,
                transcript_chars=len(transcript_text),
                cost_usd=cost,
            )

        return ToolResult(
            tool_name=self.tool_id,
            content=briefing,
            success=True,
            metadata={
                "video_id": video_id,
                "title": meta.get("title"),
                "channel": meta.get("author"),
                "url": meta.get("url"),
                "transcript_chars": len(transcript_text),
                "summary_cost_usd": round(cost, 4),
                "snippet_count": len(snippets),
                "vault_path": path,
            },
            latency_seconds=time.time() - t0,
        )

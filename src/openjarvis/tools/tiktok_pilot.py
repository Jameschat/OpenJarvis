"""TikTok voice fast-path detector.

Lives separately from `tiktok_brief.py` so the heavy deps (whisper, yt-dlp,
ffmpeg subprocess) only load when the brief tool is actually invoked, not
on every voice turn.

Mirrors the `_try_browse` pattern in browser_pilot.py.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# Match a TikTok URL anywhere in a text — both short and long forms.
_TIKTOK_URL_RE = re.compile(
    r"https?://(?:www\.|vm\.)?tiktok\.com/[\w@./?=&\-]+",
    re.IGNORECASE,
)


def _extract_url(text: str) -> Optional[str]:
    """Pull the first TikTok URL out of `text`. Strips trailing punctuation."""
    if not text:
        return None
    m = _TIKTOK_URL_RE.search(text)
    if not m:
        return None
    url = m.group(0)
    # Strip trailing punctuation that the regex may have grabbed.
    url = url.rstrip(".,!?)")
    return url


def _try_tiktok(text: str) -> Optional[str]:
    """Voice fast-path detector. If the text contains a TikTok URL,
    dispatch a `tiktok_brief_url`-shaped task and return a spoken ack.
    Returns None when no URL is present (caller falls through to LLM).

    Routing: dispatched through the existing `architect` agent with a
    prompt that names the `tiktok_brief_url` tool. The architect's tool-
    use brain will pick the right tool. We avoid creating a dedicated
    `tiktok-pilot` agent for v1 to keep agent count down.
    """
    url = _extract_url(text)
    if not url:
        return None
    try:
        from openjarvis.tools.agent_runner import add_task
    except Exception:
        logger.exception("tiktok fast-path: agent_runner import failed")
        return None
    title = f"tiktok-brief: {url[:60]}"
    prompt = (
        f"Use the tiktok_brief_url tool to brief the operator on this "
        f"video: {url}\n\n"
        f"Save the briefing to the vault. Report back with the path "
        f"and a one-line summary."
    )
    try:
        add_task(
            title=title,
            agent_id="architect",
            prompt=prompt,
            priority=20,
        )
    except Exception:
        logger.exception("tiktok fast-path: add_task failed")
        return None
    return "On it — fetching that TikTok now. The briefing will land in your vault."


__all__ = ["_try_tiktok", "_extract_url"]

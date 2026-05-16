"""Music control tool — play, pause, skip, and search music via media keys and Spotify."""

from __future__ import annotations

import ctypes
import os
import sys
import time
from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

# Windows virtual key codes for media control
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_VOLUME_UP = 0xAF
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_MUTE = 0xAD

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002


def _press_media_key(vk_code: int) -> None:
    """Simulate a media key press on Windows."""
    if sys.platform != "win32":
        raise RuntimeError("Media key simulation only supported on Windows")
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.keybd_event(vk_code, 0, KEYEVENTF_EXTENDEDKEY, 0)
    time.sleep(0.05)
    user32.keybd_event(vk_code, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)


def _spotify_search_and_play(query: str) -> str:
    """Open Spotify, search for a track, and start playback."""
    # Open Spotify search via URI — this opens the Spotify app
    uri = f"spotify:search:{query}"
    os.startfile(uri)

    # Wait for Spotify to open and navigate, then press play
    time.sleep(2.0)
    _press_media_key(VK_MEDIA_PLAY_PAUSE)

    return f"Searching Spotify for '{query}' and playing"


@ToolRegistry.register("music_control")
class MusicControlTool(BaseTool):
    """Control music playback — play, pause, skip, search on Spotify."""

    tool_id = "music_control"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="music_control",
            description=(
                "Control music playback on the user's computer. "
                "Can play/pause, skip to next/previous track, stop music, "
                "adjust volume, and search for songs on Spotify. "
                "Works with Spotify and any media player that responds to "
                "Windows media keys. Spotify must be installed for search."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "play_pause",
                            "next",
                            "previous",
                            "stop",
                            "volume_up",
                            "volume_down",
                            "mute",
                            "search_and_play",
                        ],
                        "description": (
                            "Action to perform. "
                            "'play_pause' toggles playback. "
                            "'next'/'previous' skip tracks. "
                            "'stop' stops playback. "
                            "'volume_up'/'volume_down' adjust volume. "
                            "'mute' toggles mute. "
                            "'search_and_play' searches Spotify for a song and plays it."
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "Search query for 'search_and_play' action. "
                            "E.g. 'Bohemian Rhapsody', 'Drake Hotline Bling', "
                            "'lofi hip hop', 'workout playlist'."
                        ),
                    },
                },
                "required": ["action"],
            },
            category="media",
            requires_confirmation=False,
            timeout_seconds=10.0,
            metadata={"platform": "windows"},
        )

    def execute(self, **params: Any) -> ToolResult:
        action = params.get("action", "").strip()
        query = params.get("query", "").strip()

        if not action:
            return ToolResult(
                tool_name="music_control",
                content="No action specified.",
                success=False,
            )

        if sys.platform != "win32":
            return ToolResult(
                tool_name="music_control",
                content="Music control is only supported on Windows.",
                success=False,
            )

        try:
            if action == "play_pause":
                _press_media_key(VK_MEDIA_PLAY_PAUSE)
                msg = "Toggled play/pause"
            elif action == "next":
                _press_media_key(VK_MEDIA_NEXT_TRACK)
                msg = "Skipped to next track"
            elif action == "previous":
                _press_media_key(VK_MEDIA_PREV_TRACK)
                msg = "Went to previous track"
            elif action == "stop":
                _press_media_key(VK_MEDIA_STOP)
                msg = "Stopped playback"
            elif action == "volume_up":
                for _ in range(5):
                    _press_media_key(VK_VOLUME_UP)
                msg = "Volume increased"
            elif action == "volume_down":
                for _ in range(5):
                    _press_media_key(VK_VOLUME_DOWN)
                msg = "Volume decreased"
            elif action == "mute":
                _press_media_key(VK_VOLUME_MUTE)
                msg = "Toggled mute"
            elif action == "search_and_play":
                if not query:
                    return ToolResult(
                        tool_name="music_control",
                        content="No search query provided for search_and_play.",
                        success=False,
                    )
                msg = _spotify_search_and_play(query)
            else:
                return ToolResult(
                    tool_name="music_control",
                    content=f"Unknown action: {action}",
                    success=False,
                )

            return ToolResult(
                tool_name="music_control",
                content=msg,
                success=True,
                metadata={"action": action, "query": query},
            )
        except Exception as exc:
            return ToolResult(
                tool_name="music_control",
                content=f"Music control failed: {exc}",
                success=False,
            )


__all__ = ["MusicControlTool"]

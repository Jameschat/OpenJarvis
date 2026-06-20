"""Auto-capture desktop chat turns into long-term memory so it keeps expanding.

After every chat turn the bridge calls :func:`capture_chat_async`, which (on a
daemon thread, fully non-fatal) feeds the exchange into the three memory systems:

* **agentmemory** (episodic sidecar, :7730) — EVERY turn. It is built for high
  volume and has its own reflect/insights pass that distils episodics into
  higher-order memories, so we don't gate it.
* **Obsidian vault Inbox** — only *salient* turns (see :func:`_is_salient`), so
  the vault isn't flooded with "what's the weather" / "turn the lights on".
* **graphify** — a cooldown-gated rebuild is queued whenever we wrote to the
  vault, so the knowledge graph grows as the vault grows.

Every path is wrapped so a missing/unhealthy backend can never break chat.
Operator surface: writes show up as growing counts on the obsidian / agentmemory
/ graphify nodes in Mission Control's memory view, and as notes in the vault
Inbox.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Iterable, Optional

logger = logging.getLogger("openjarvis.server.chat_capture")

# Tools whose use, on its own, marks a turn as transactional rather than
# memorable. A short "what's the weather" or "lights on" that only touches these
# is skipped for the vault (it still lands in episodic agentmemory).
_TRANSACTIONAL_TOOLS = {
    "weather",
    "crypto",
    "music_control",
    "hue_lights",
    "sonos",
    "lutron",
    "app_launcher",
    "macro",
    "calculator",
}

# Tools whose use marks a turn as worth keeping in the vault regardless of length.
_SUBSTANTIVE_TOOLS = {
    "file_write",
    "file_edit",
    "todo_write",
    "web_search",
    "knowledge_search",
    "memory_search",
    "memory_retrieve",
    "shell_exec",
    "browser_navigate",
}


def _is_salient(user_text: str, answer: str, tool_names: set[str]) -> bool:
    """Decide whether a turn earns a vault note (vs episodic-only)."""
    if tool_names & _SUBSTANTIVE_TOOLS:
        return True
    # Pure ephemeral lookups (weather/crypto/lights/music/…) -> episodic only,
    # no matter how verbose the answer. A live forecast is not vault knowledge.
    if tool_names and tool_names <= _TRANSACTIONAL_TOOLS:
        return False
    # Otherwise (no tools, or a mix): keep substantive exchanges only.
    return len(answer) >= 240 or len(user_text) >= 160


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + " […]"


def _capture(
    user_text: str,
    answer: str,
    tool_names: list[str],
    model: str,
) -> None:
    names = {t for t in tool_names if t}
    exchange = f"User: {_truncate(user_text, 2000)}\nJarvis: {_truncate(answer, 4000)}"
    tags = ["chat"] + ([model] if model else []) + sorted(names)

    # 1. Episodic memory — every turn (the sidecar dedupes/reflects itself).
    try:
        from openjarvis.tools import agentmemory_client

        if agentmemory_client.health():
            agentmemory_client.remember(exchange, tags=tags)
    except Exception as exc:  # never fatal
        logger.debug("agentmemory capture skipped: %s", exc)

    # 2. Vault Inbox — salient turns only, then queue a graph rebuild.
    if not _is_salient(user_text, answer, names):
        return
    try:
        from openjarvis.tools import obsidian_brain

        title = _truncate(user_text.strip().splitlines()[0] if user_text.strip() else "chat", 70)
        body = (
            f"**Asked:** {_truncate(user_text, 2000)}\n\n"
            f"**Jarvis:** {_truncate(answer, 6000)}\n\n"
            f"_Tools: {', '.join(sorted(names)) or 'none'} · model: {model or 'local'}_"
        )
        path = obsidian_brain.remember(body, title=title, folder="Inbox", tags=tags)
    except Exception as exc:
        logger.debug("vault capture skipped: %s", exc)
        path = None

    if path is not None:
        try:
            from openjarvis.tools import graphify_trigger

            graphify_trigger.maybe_refresh_after_task()
        except Exception as exc:
            logger.debug("graphify trigger skipped: %s", exc)


def capture_chat_async(
    *,
    user_text: str,
    answer: str,
    tool_results: Optional[Iterable[Any]] = None,
    model: str = "",
) -> None:
    """Fire-and-forget capture of one chat turn. Returns immediately.

    Safe to call on the hot path: all work runs on a daemon thread and every
    backend call is guarded, so memory capture can never delay or break chat.
    """
    if not (user_text or answer):
        return
    tool_names = [getattr(tr, "tool_name", "") for tr in (tool_results or [])]
    try:
        threading.Thread(
            target=_capture,
            args=(user_text or "", answer or "", tool_names, model or ""),
            daemon=True,
        ).start()
    except Exception as exc:
        logger.debug("capture thread spawn failed: %s", exc)


__all__ = ["capture_chat_async"]

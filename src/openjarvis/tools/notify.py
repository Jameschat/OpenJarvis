"""Channel-agnostic operator notifications (#1c).

One entry point — ``notify(message)`` — fans out to every configured
channel, best-effort each, and always appends to a JSONL log at
``~/.openjarvis/notifications.jsonl`` (so the morning briefing and future
surfaces can replay what was sent, including messages that failed to
deliver because the stack itself was down).

Channels:
- ``chat``      — HUD chat panel via task_followup.push_to_chat. Always on.
- ``whatsapp``  — via channels.whatsapp_baileys, ONLY when
  ``OPENJARVIS_NOTIFY_WHATSAPP=1`` and the bridge is paired
  (``OPENJARVIS_NOTIFY_WHATSAPP_TO`` holds the target JID). Dormant until
  the operator scans the pairing QR; everything degrades gracefully.

Never raises. A notification failure must never break the caller —
especially the watchdog, which calls this mid-incident.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_LOG_PATH = Path.home() / ".openjarvis" / "notifications.jsonl"


def _send_chat(message: str) -> bool:
    from openjarvis.tools import task_followup

    return task_followup.push_to_chat(message)


def _whatsapp_enabled() -> bool:
    return os.environ.get("OPENJARVIS_NOTIFY_WHATSAPP", "0").strip().lower() in (
        "1",
        "true",
        "on",
    )


def _send_whatsapp(message: str) -> bool:
    """Send via the Baileys bridge. Returns False unless the bridge is
    paired and a target JID is configured — dormant by default."""
    target = os.environ.get("OPENJARVIS_NOTIFY_WHATSAPP_TO", "").strip()
    if not target:
        return False
    try:
        from openjarvis.channels.whatsapp_baileys import WhatsAppBaileysChannel

        # send() returns False unless the bridge subprocess is running and
        # CONNECTED (paired) — real delivery requires the channel managed by
        # `jarvis serve` plus a one-time pairing QR scan by the operator.
        channel = WhatsAppBaileysChannel()
        return bool(channel.send(target, message))
    except Exception:
        logger.debug("notify: whatsapp send failed (non-fatal)", exc_info=True)
        return False


def _log(message: str, level: str, delivered: Dict[str, bool]) -> None:
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": time.time(),
            "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "level": level,
            "message": message,
            "delivered": delivered,
        }
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        logger.debug("notify: log write failed (non-fatal)", exc_info=True)


def notify(message: str, *, level: str = "info") -> Dict[str, Any]:
    """Fan the message out to all configured channels. Returns a per-channel
    delivery map. Never raises."""
    delivered: Dict[str, bool] = {}
    try:
        delivered["chat"] = bool(_send_chat(message))
    except Exception:
        logger.debug("notify: chat send crashed (non-fatal)", exc_info=True)
        delivered["chat"] = False
    if _whatsapp_enabled():
        try:
            delivered["whatsapp"] = bool(_send_whatsapp(message))
        except Exception:
            logger.debug("notify: whatsapp send crashed (non-fatal)", exc_info=True)
            delivered["whatsapp"] = False
    _log(message, level, delivered)
    return delivered


__all__ = ["notify"]

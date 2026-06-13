"""FastAPI routes for the Memory page feed + capability inbox.

The live ``jarvis serve`` backend is this FastAPI app â€” NOT the legacy
``cli/brain_server.py`` HTTP server (discovered 2026-06-13: endpoints added
there never serve in the current deployment; the SPA catch-all swallows
them as client routes). Loopback-trust model matches the studio router.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

memory_router = APIRouter(prefix="/memory", tags=["memory"])
capability_router = APIRouter(prefix="/capability", tags=["capability"])
whatsapp_router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


@memory_router.get("/activity")
def memory_activity(since: Optional[float] = None) -> Dict[str, Any]:
    """Data feed for the desktop Memory page constellation: nodes + pulses
    + realtime log. Poll with ?since=<last payload ts> for deltas.

    Deliberately a SYNC route: snapshot() does file walks and short HTTP
    probes. As `async def` it blocked the uvicorn event loop on every 2s
    poll and starved all other endpoints (ping took 16s â€” found live
    2026-06-13). Plain `def` runs in FastAPI's threadpool."""
    from openjarvis.tools import memory_activity as activity

    return activity.snapshot(since)


class CapabilityDecision(BaseModel):
    capability: str


@capability_router.get("/inbox")
def capability_inbox() -> Dict[str, Any]:
    """Self-improvement approval inbox (Phase 7 #6): latest capability-queue
    items annotated with operator decisions."""
    from openjarvis.tools import capability_inbox as inbox

    return inbox.list_inbox()


@capability_router.post("/inbox/approve")
def capability_inbox_approve(body: CapabilityDecision) -> Dict[str, Any]:
    from openjarvis.tools import capability_inbox as inbox

    result = inbox.approve(body.capability.strip())
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "unknown capability"))
    return result


@capability_router.post("/inbox/dismiss")
def capability_inbox_dismiss(body: CapabilityDecision) -> Dict[str, Any]:
    from openjarvis.tools import capability_inbox as inbox

    return inbox.dismiss(body.capability.strip())


# --- WhatsApp pairing (Phase 8 #1, in-app surface) ---------------------------


@whatsapp_router.post("/pair/start")
def whatsapp_pair_start() -> Dict[str, Any]:
    """Begin (or report) a background WhatsApp pairing session. The QR string
    is exposed via /whatsapp/pair/status for the UI to render."""
    from openjarvis.channels import whatsapp_pairing

    return whatsapp_pairing.start_pairing_session()


@whatsapp_router.get("/pair/status")
def whatsapp_pair_status() -> Dict[str, Any]:
    from openjarvis.channels import whatsapp_pairing

    return whatsapp_pairing.pairing_status()


@whatsapp_router.post("/pair/enable")
def whatsapp_pair_enable() -> Dict[str, Any]:
    """After a connected pairing, write the notify env vars into jarvis.env so
    watchdog/briefing notices reach WhatsApp on the next stack restart."""
    from openjarvis.channels import whatsapp_pairing

    status = whatsapp_pairing.pairing_status()
    if status.get("status") != "connected" or not status.get("jid"):
        raise HTTPException(status_code=409, detail="not paired yet")
    written = _upsert_jarvis_env(
        {"OPENJARVIS_NOTIFY_WHATSAPP": "1", "OPENJARVIS_NOTIFY_WHATSAPP_TO": status["jid"]}
    )
    return {"ok": written, "jid": status["jid"]}


def _upsert_jarvis_env(values: Dict[str, str]) -> bool:
    """Upsert KEY=VALUE lines into ~/.openjarvis/jarvis.env (user-only file)."""
    from pathlib import Path

    path = Path.home() / ".openjarvis" / "jarvis.env"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        remaining = [
            ln for ln in lines
            if not any(ln.strip().startswith(f"{k}=") for k in values)
        ]
        for k, v in values.items():
            remaining.append(f"{k}={v}")
        path.write_text("\n".join(remaining) + "\n", encoding="utf-8")
        return True
    except OSError:
        logger.exception("whatsapp: could not write jarvis.env")
        return False

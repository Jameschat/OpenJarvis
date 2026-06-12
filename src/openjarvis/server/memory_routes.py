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

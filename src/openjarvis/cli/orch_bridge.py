"""Bridge between the local ``agent_runner`` orchestrator and the
mission-control HUD's SSE layer.

Originally this module wrapped a third-party ``orch`` CLI; that tool turned
out to be a phantom (sandbox-only fiction — not a real npm package), so
this file now reads snapshots from our own in-process ``agent_runner``.
The public surface (``get_snapshot``, ``subscribe``, ``unsubscribe``,
``start_orch_bridge``) is unchanged so ``brain_server.py`` and the HUD
keep working without edits.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, List

from openjarvis.tools import agent_runner

logger = logging.getLogger(__name__)

POLL_INTERVAL = 1.0  # seconds — cheap to read local state, so poll snappily


# ---------------------------------------------------------------------------
# State holder + pub-sub for SSE clients
# ---------------------------------------------------------------------------


class _BridgeState:
    def __init__(self) -> None:
        self.snapshot: dict = {"online": False, "agents": [], "tasks": [], "aggregate": {}}
        self._serialized = json.dumps(self.snapshot)
        self._clients: List[Any] = []
        self._lock = threading.Lock()

    def update(self, snapshot: dict) -> None:
        serialized = json.dumps(snapshot)
        with self._lock:
            if serialized == self._serialized:
                return  # no change → skip broadcast (saves the UI a render pass)
            self.snapshot = snapshot
            self._serialized = serialized
            dead = []
            msg = f"data: {serialized}\n\n".encode()
            for wfile in self._clients:
                try:
                    wfile.write(msg)
                    wfile.flush()
                except Exception:
                    dead.append(wfile)
            for d in dead:
                self._clients.remove(d)

    def subscribe(self, wfile: Any) -> None:
        with self._lock:
            self._clients.append(wfile)

    def unsubscribe(self, wfile: Any) -> None:
        with self._lock:
            self._clients = [c for c in self._clients if c is not wfile]

    def current(self) -> dict:
        with self._lock:
            return self.snapshot


_state = _BridgeState()


# ---------------------------------------------------------------------------
# Poller thread
# ---------------------------------------------------------------------------


_thread: Any = None


def _decorate(snap: dict) -> dict:
    """Inject plan summaries into the snapshot so the PROJECTS HUD panel
    can render without a second round-trip. List-plans walks a few small
    JSON files on disk — cheap enough to do every poll tick (1s)."""
    try:
        from openjarvis.tools import agent_plan
        snap["plans"] = agent_plan.list_plans()
    except Exception:
        snap.setdefault("plans", [])
    return snap


def _poll_loop() -> None:
    logger.info("agent_runner bridge started — HUD will show 6 local agents")
    while True:
        try:
            snap = _decorate(agent_runner.get_snapshot())
            _state.update(snap)
        except Exception:
            logger.exception("agent_runner bridge poll iteration crashed (continuing)")
        time.sleep(POLL_INTERVAL)


def start_orch_bridge() -> None:
    """Start the background poller + the underlying agent worker (idempotent)."""
    global _thread
    # Spin up the agent worker so tasks actually execute when queued
    try:
        agent_runner.start_worker()
    except Exception:
        logger.exception("failed to start agent_runner worker")

    if _thread is not None:
        return
    _thread = threading.Thread(target=_poll_loop, daemon=True, name="agent-bridge")
    _thread.start()


# ---------------------------------------------------------------------------
# Public API — unchanged signature so brain_server doesn't need edits
# ---------------------------------------------------------------------------


def get_snapshot() -> dict:
    # Prefer the cached snapshot (set by the poller); if the poller hasn't
    # run yet (first request arriving before the first tick), read live.
    snap = _state.current()
    if not snap.get("agents"):
        try:
            snap = _decorate(agent_runner.get_snapshot())
            _state.update(snap)
        except Exception:
            pass
    return snap


def subscribe(wfile: Any) -> None:
    _state.subscribe(wfile)


def unsubscribe(wfile: Any) -> None:
    _state.unsubscribe(wfile)


__all__ = [
    "start_orch_bridge",
    "get_snapshot",
    "subscribe",
    "unsubscribe",
]

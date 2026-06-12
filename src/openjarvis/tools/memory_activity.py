"""Memory-activity aggregator for the desktop Memory page (Phase 8).

Shapes everything the memory constellation needs into one payload:

    {
      "ts": <server time>,
      "nodes":  [{id, label, online, stat}],
      "pulses": [{ts, source, target, op, label, kind}],
      "log":    [{ts, actor, op, label, kind}],
    }

- nodes: the memory vaults (obsidian, graphify, agentmemory) + actor brains
  (jarvis, qwen, claude, codex), each with liveness + a one-line stat.
- pulses: directed edge events the canvas fires "neurons" along — derived
  from the vault event ring (``obsidian_brain.recent_events()``). Reads flow
  vault→actor, writes/appends flow actor→vault.
- log: newest-first rows for the realtime table (vault events + notification
  rows merged).

Every section is best-effort: a dead sidecar dims its node, never breaks the
payload. Poll with ``since`` (epoch seconds) to receive only new pulses/log.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_LOG_ROW_CAP = 200

# Actor attribution: vault-event ``source`` → constellation node id.
_SOURCE_NODE = {
    "voice": "jarvis",
    "manual": "jarvis",
    "chatgpt": "jarvis",
    "agent": "jarvis",      # generic agent until plan step A3 adds per-provider attribution
    "qwen": "qwen",
    "claude": "claude",
    "codex": "codex",
}

_ACTOR_NODES = [
    {"id": "jarvis", "label": "J.A.R.V.I.S."},
    {"id": "qwen", "label": "QWEN"},
    {"id": "claude", "label": "CLAUDE"},
    {"id": "codex", "label": "CODEX"},
]


def _vault_node() -> Dict[str, Any]:
    try:
        from datetime import datetime, timezone

        from openjarvis.tools import vault_stats
        from openjarvis.tools.obsidian_brain import BRAIN_ROOT

        s = vault_stats.summary(Path(BRAIN_ROOT), now=datetime.now(timezone.utc))
        return {
            "id": "vault",
            "label": "OBSIDIAN VAULT",
            "online": bool(s.get("total_notes")),
            "stat": f"{s.get('total_notes', 0)} notes · {s.get('daily_today', 0)} today",
        }
    except Exception:
        logger.debug("memory_activity: vault node failed", exc_info=True)
        return {"id": "vault", "label": "OBSIDIAN VAULT", "online": False, "stat": "offline"}


def _graphify_node() -> Dict[str, Any]:
    try:
        from openjarvis.cli import graphify_bridge

        status = graphify_bridge.status()
        nodes = status.get("nodes") or status.get("total_nodes") or 0
        return {
            "id": "graphify",
            "label": "GRAPHIFY",
            "online": bool(status.get("online", nodes)),
            "stat": f"{nodes} graph nodes",
        }
    except Exception:
        return {"id": "graphify", "label": "GRAPHIFY", "online": False, "stat": "offline"}


def _agentmemory_node() -> Dict[str, Any]:
    try:
        from openjarvis.tools import agentmemory_client

        # health() returns a plain bool in the thin client; tolerate a dict
        # in case the client grows up later.
        health = agentmemory_client.health()
        if isinstance(health, dict):
            ok = health.get("status") in ("ok", "healthy")
        else:
            ok = bool(health)
        return {
            "id": "agentmemory",
            "label": "AGENT MEMORY",
            "online": ok,
            "stat": "episodic · port 7730" if ok else "offline",
        }
    except Exception:
        return {"id": "agentmemory", "label": "AGENT MEMORY", "online": False, "stat": "offline"}


def _vault_events(since: Optional[float]) -> List[Dict[str, Any]]:
    try:
        from openjarvis.tools.obsidian_brain import recent_events

        events = recent_events()
    except Exception:
        logger.debug("memory_activity: recent_events failed", exc_info=True)
        return []
    if since:
        events = [e for e in events if (e.get("ts") or 0) > since]
    return events


def _pulse_for_event(event: Dict[str, Any]) -> Dict[str, Any]:
    actor = _SOURCE_NODE.get(str(event.get("source") or ""), "jarvis")
    op = str(event.get("op") or "read")
    if op == "read":
        source, target = "vault", actor
    else:
        source, target = actor, "vault"
    return {
        "ts": event.get("ts"),
        "source": source,
        "target": target,
        "op": op,
        "label": event.get("label", ""),
        "kind": event.get("kind", ""),
    }


def _notification_rows(since: Optional[float]) -> List[Dict[str, Any]]:
    try:
        from openjarvis.tools import notify as notify_mod

        path = notify_mod._LOG_PATH
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines()[-50:]:
            if not line.strip():
                continue
            row = json.loads(line)
            if since and (row.get("ts") or 0) <= since:
                continue
            rows.append(
                {
                    "ts": row.get("ts"),
                    "actor": "jarvis",
                    "op": "notify",
                    "label": (row.get("message") or "")[:120],
                    "kind": row.get("level", "info"),
                }
            )
        return rows
    except Exception:
        return []


def snapshot(since: Optional[float] = None) -> Dict[str, Any]:
    """One poll of the memory constellation. Never raises."""
    try:
        events = _vault_events(since)
    except Exception:
        logger.debug("memory_activity: event source failed", exc_info=True)
        events = []
    pulses = [_pulse_for_event(e) for e in events]

    log: List[Dict[str, Any]] = [
        {
            "ts": e.get("ts"),
            "actor": _SOURCE_NODE.get(str(e.get("source") or ""), "jarvis"),
            "op": e.get("op", ""),
            "label": e.get("label", ""),
            "kind": e.get("kind", ""),
        }
        for e in events
    ]
    log.extend(_notification_rows(since))
    log.sort(key=lambda r: r.get("ts") or 0, reverse=True)

    return {
        "ts": time.time(),
        "nodes": [_vault_node(), _graphify_node(), _agentmemory_node(), *(
            {**n, "online": True, "stat": ""} for n in _ACTOR_NODES
        )],
        "pulses": pulses,
        "log": log[:_LOG_ROW_CAP],
    }


__all__ = ["snapshot"]

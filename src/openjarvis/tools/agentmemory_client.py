"""Thin HTTP client for the agentmemory episodic memory sidecar (port 7730).

All routes are under the /agentmemory/ prefix.
Reads AGENTMEMORY_URL from env (default http://localhost:7730).
Hard 3-second timeout on all calls.
Raises AgentMemoryUnavailable on any network/timeout error — callers
must catch and degrade gracefully to vault-only behaviour.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_BASE = os.environ.get("AGENTMEMORY_URL", "http://localhost:7730")
_TIMEOUT = 3.0

_PATH_HEALTH   = "/agentmemory/livez"
_PATH_SEARCH   = "/agentmemory/search"
_PATH_REMEMBER = "/agentmemory/remember"
_PATH_REFLECT  = "/agentmemory/reflect"
_PATH_INSIGHTS = "/agentmemory/insights"


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class AgentMemoryUnavailable(Exception):
    """Raised when the agentmemory HTTP server is unreachable or times out."""


@dataclass
class Hit:
    snippet: str
    score: float
    session_id: str
    tier: str = "episodic"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get(path: str) -> dict:
    url = f"{_BASE}{path}"
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read())
    except json.JSONDecodeError as exc:
        raise AgentMemoryUnavailable(f"invalid JSON from sidecar: {exc}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AgentMemoryUnavailable(str(exc)) from exc


def _post(path: str, body: dict) -> dict:
    url = f"{_BASE}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read())
    except json.JSONDecodeError as exc:
        raise AgentMemoryUnavailable(f"invalid JSON from sidecar: {exc}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AgentMemoryUnavailable(str(exc)) from exc


def _extract_snippet(result: dict) -> str:
    """Extract human-readable content from a search result object."""
    obs = result.get("observation", {})
    if not isinstance(obs, dict):
        return str(obs)[:200]
    # Try common content keys in order of preference
    for key in ("content", "text", "data", "summary", "title"):
        val = obs.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    # Fallback: stringify the observation
    return json.dumps(obs)[:200]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def health() -> bool:
    """Return True if the agentmemory server is reachable and healthy."""
    try:
        data = _get(_PATH_HEALTH)
        return data.get("status") == "ok" or data.get("ok") is True
    except AgentMemoryUnavailable:
        return False


def search(
    query: str,
    limit: int = 5,
    project: str | None = None,
) -> list[Hit]:
    """Search episodic and semantic memory. Raises AgentMemoryUnavailable on failure."""
    body: dict = {"query": query, "limit": limit}
    if project is not None:
        body["project"] = project
    data = _post(_PATH_SEARCH, body)
    return [
        Hit(
            snippet=_extract_snippet(r),
            score=float(r.get("score", 0.0)),
            session_id=str(r.get("sessionId", "")),
        )
        for r in data.get("results", [])
    ]


def remember(content: str, tags: list[str] | None = None) -> bool:
    """Write an explicit memory entry. Raises AgentMemoryUnavailable on failure."""
    body: dict = {"content": content}
    if tags:
        body["tags"] = tags
    data = _post(_PATH_REMEMBER, body)
    return bool(data.get("ok", False))


def reflect(topic: str) -> str:
    """Return consolidated lessons/patterns for a topic. Raises AgentMemoryUnavailable on failure."""
    data = _post(_PATH_REFLECT, {"topic": topic})
    return data.get("content", "")


def insights() -> list[str]:
    """Return cross-session insights. Raises AgentMemoryUnavailable on failure."""
    data = _get(_PATH_INSIGHTS)
    return data.get("insights", [])

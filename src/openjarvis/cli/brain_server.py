"""Tiny HTTP + SSE server that serves the brain visualization, streams state,
and handles phone push-to-talk voice turns.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from openjarvis.cli import orch_bridge
# UniFi bridge removed from active use 2026-04-26 — operator didn't find
# the panel useful. unifi_bridge.py remains on disk if it's wanted back.

logger = logging.getLogger(__name__)

_WEB_DIR = Path(__file__).resolve().parent.parent.parent.parent / "jarvis_web"
_PORT = 7710


def _jarvis_web_path(filename: str) -> Path:
    """Return a file path under jarvis_web."""
    return _WEB_DIR / filename


def _codegraph_status(
    repo_root: Path | None = None,
    tool_path: Path | None = None,
) -> dict[str, Any]:
    """Return lightweight CodeGraph index status for the HUD.

    This intentionally avoids starting the MCP server or refreshing the index;
    the daily automation owns refresh work and the dashboard only reads state.
    """
    repo = Path(repo_root) if repo_root is not None else _WEB_DIR.parent
    codegraph_dir = repo / ".codegraph"
    db = codegraph_dir / "codegraph.db"
    config = codegraph_dir / "config.json"
    mcp_config = repo / ".mcp.json"
    default_tool = (
        Path.home()
        / ".openjarvis"
        / "tools"
        / "codegraph-0.8.0"
        / "node_modules"
        / ".bin"
        / ("codegraph.cmd" if os.name == "nt" else "codegraph")
    )
    tool = Path(tool_path) if tool_path is not None else default_tool

    counts: dict[str, int | None] = {"files": None, "nodes": None, "edges": None}
    if db.exists():
        try:
            import sqlite3
            with sqlite3.connect(str(db)) as conn:
                for table in ("files", "nodes", "edges"):
                    counts[table] = int(conn.execute(f"select count(*) from {table}").fetchone()[0])
        except Exception:
            logger.debug("CodeGraph count read failed", exc_info=True)

    mcp_configured = False
    mcp_command = ""
    if mcp_config.exists():
        try:
            data = json.loads(mcp_config.read_text(encoding="utf-8"))
            server = (data.get("mcpServers") or {}).get("codegraph") or {}
            mcp_command = str(server.get("command") or "")
            args = [str(arg) for arg in (server.get("args") or [])]
            mcp_configured = bool(mcp_command and "serve" in args and "--mcp" in args)
        except Exception:
            logger.debug("CodeGraph MCP config read failed", exc_info=True)

    updated_at = db.stat().st_mtime if db.exists() else None
    size_mb = round(db.stat().st_size / (1024 * 1024), 2) if db.exists() else 0.0
    indexed = db.exists() and bool(counts["nodes"])
    return {
        "online": indexed and mcp_configured,
        "installed": tool.exists(),
        "indexed": indexed,
        "mcp_configured": mcp_configured,
        "files": counts["files"] or 0,
        "nodes": counts["nodes"] or 0,
        "edges": counts["edges"] or 0,
        "size_mb": size_mb,
        "updated_at": updated_at,
        "repo_root": str(repo),
        "index_dir": str(codegraph_dir),
        "tool_path": str(tool),
        "mcp_command": mcp_command,
        "refresh": "live debounced during sessions plus daily 06:05 automation",
        "mode": "project-local MCP, no-watch, read-only dashboard status",
    }


class VoiceTurnContext:
    """Shared backends + command processor supplied by the voice loop."""

    def __init__(self) -> None:
        self.stt_backend: Any = None
        self.tts_backend: Any = None
        self.config: Any = None
        self.process_command: Optional[Callable[[str], str]] = None


_ctx = VoiceTurnContext()

# Shared 503 message when the voice/chat pipeline lock is held by a
# previous turn. Operator-readable — tells them what to do, not just
# that something is busy. Used by /chat, /text_command, /voice_turn.
_BUSY_MSG = (
    "Brain busy on a previous turn — gpt-4o can hang 5–30s on complex "
    "tool chains. Wait a few seconds and ask again. If this persists "
    "for more than a minute, restart jarvis.bat."
)

# Serialize concurrent /voice_turn calls. faster-whisper's model isn't safe
# to invoke from two threads at once — a second request arriving while the
# first is still inside transcribe() can hang indefinitely. This lock ensures
# each turn runs to completion before the next begins.
_voice_turn_lock = threading.Lock()

# Lock acquire timeout. Was 5s (fail-fast). With voice acks now playing
# "Working on it" inside the lock, fast-fail is no longer needed —
# concurrent requests sit on the lock long enough for the previous turn
# to finish naturally. 30s covers a normal gpt-4o tool chain; beyond
# that we 503 with the busy message + a busy ack so the operator hears
# something rather than staring at a stuck UI.
_LOCK_ACQUIRE_TIMEOUT_S = 30

# Tool-chain wall-clock deadline. process_command can in theory run for
# minutes if the LLM chains web_search → fetch_url → dispatch_agent →
# etc. We let it run up to this long, then return a fallback voice
# response. The worker thread isn't killed (Python can't cooperatively
# kill a thread without rewriting process_command); it keeps running in
# the background and its result lands in the vault as a journal entry,
# but the HTTP turn unblocks the operator. Accepted trade-off.
# Bumped 25s → 60s on 2026-05-01: chart_analyst legitimately runs
# 25-40s (two vision calls + OHLCV fetch + matplotlib render). Voice
# ack already plays so silence is no longer the failure mode the
# tighter deadline protected against.
_PROCESS_DEADLINE_S = 60


def _run_with_deadline(fn: Callable[[str], str], arg: str,
                       deadline_s: float) -> "Optional[str]":
    """Run ``fn(arg)`` on a daemon thread; return its result, or ``None``
    if the deadline elapses first.

    On timeout the worker keeps running (we can't kill it safely), but
    the caller stops waiting and returns a fallback response to the
    user. Background work may eventually write to the vault — that's
    intentional. Operator can grep ``Brain/Daily/`` for late results.

    Re-raises any exception the worker raised, so the handler's
    existing try/except still sees them.
    """
    import threading as _threading
    box: List[Any] = []
    err_box: List[BaseException] = []

    def _runner() -> None:
        try:
            box.append(fn(arg))
        except BaseException as exc:                # noqa: BLE001
            err_box.append(exc)

    t = _threading.Thread(target=_runner, daemon=True,
                          name="process_command-deadline")
    t.start()
    t.join(timeout=deadline_s)
    if t.is_alive():
        return None
    if err_box:
        raise err_box[0]
    return box[0] if box else ""


# ---------------------------------------------------------------------------
# PIN gate — protects the public-tunnel endpoints from random visitors
# ---------------------------------------------------------------------------
#
# When ``OPENJARVIS_PUBLIC_PIN`` is set, every "sensitive" endpoint
# (voice_turn, command, agent_task, schedule, content/kickoff, the SSE
# streams, etc.) requires either:
#   (a) a session cookie ``mc-session=<token>`` issued via ``POST /auth``, or
#   (b) an Authorization: Bearer <pin> header (for scripted clients).
#
# When the PIN env var is unset, auth is bypassed (legacy/dev mode).

import hmac
import secrets

def _err_ref() -> str:
    """Audit 2026-04-26 M2: opaque error ref returned to clients in
    place of str(exc), so 500 responses don't leak filesystem paths,
    env-var hints, or third-party-library exception text (which can
    occasionally include secrets baked in). The full exception is
    still logged server-side via logger.exception, keyed by this ref
    so the operator can correlate."""
    return secrets.token_hex(4)


_PIN_SESSIONS: Dict[str, float] = {}  # token -> expires_at (unix seconds)
_PIN_SESSIONS_LOCK = threading.Lock()
# Audit 2026-04-26 M4: dropped from 30 days to 7 days to limit blast
# radius if a cookie ever leaks (XSS, LAN sniff over plain HTTP, etc).
# Operator pays the cost of one extra PIN entry per week.
_SESSION_TTL = 7 * 86400               # 7 days

# Brute-force defence (audit 2026-04-26 C3): per-IP failed-attempt
# tracker. Keys are stripped client IPs; values are
# (failure_count, first_failure_ts). After _PIN_LOCK_THRESHOLD failures
# within _PIN_LOCK_WINDOW_S, the IP is locked out for _PIN_LOCK_DURATION_S.
_PIN_FAILS: Dict[str, "tuple[int, float, float]"] = {}  # ip -> (count, window_start, locked_until)
_PIN_FAILS_LOCK = threading.Lock()
_PIN_LOCK_THRESHOLD = 8                # failures within window → lockout
_PIN_LOCK_WINDOW_S = 300               # 5 min sliding window
_PIN_LOCK_DURATION_S = 1800            # 30 min lockout
_PIN_FAIL_DELAY_BASE_S = 0.4           # base delay (existing behaviour)
_PIN_FAIL_DELAY_MAX_S = 4.0            # caps additive backoff
_PIN_MIN_LENGTH = 6                    # minimum PIN length enforced at startup


def _public_pin() -> str:
    return os.environ.get("OPENJARVIS_PUBLIC_PIN", "").strip()


def _make_session_token() -> str:
    token = secrets.token_hex(24)
    with _PIN_SESSIONS_LOCK:
        _PIN_SESSIONS[token] = time.time() + _SESSION_TTL
        # Drop expired tokens opportunistically
        cutoff = time.time()
        for t, exp in list(_PIN_SESSIONS.items()):
            if exp < cutoff:
                _PIN_SESSIONS.pop(t, None)
    return token


def _is_valid_session(token: str) -> bool:
    if not token:
        return False
    with _PIN_SESSIONS_LOCK:
        exp = _PIN_SESSIONS.get(token)
    return bool(exp) and exp > time.time()


def set_voice_context(
    stt_backend: Any,
    tts_backend: Any,
    config: Any,
    process_command: Callable[[str], str],
) -> None:
    """Register the backends used by the /voice_turn endpoint."""
    _ctx.stt_backend = stt_backend
    _ctx.tts_backend = tts_backend
    _ctx.config = config
    _ctx.process_command = process_command
    # Wire the voice-ack module so /chat /text_command /voice_turn can
    # play "On it, sir" while the LLM tool chain runs (5-30s window
    # was previously silent — most-cited frustration with the pipeline).
    try:
        from openjarvis.cli import voice_ack
        digest = getattr(config, "digest", None)
        voice_ack.configure(
            tts_backend,
            voice_id=(getattr(digest, "voice_id", None) or "fable"),
            speed=(getattr(digest, "voice_speed", None) or 1.0),
        )
        voice_ack.warmup_async()
    except Exception:
        logger.debug("voice_ack configure failed", exc_info=True)
    # Intent-classifier centroid build runs in the background on first
    # startup (~150ms one OpenAI call). Subsequent runs hit the disk
    # cache. Keeps the very first turn from paying the embedding cost.
    try:
        from openjarvis.cli import intent_classifier
        intent_classifier.warmup_async()
    except Exception:
        logger.debug("intent_classifier warmup failed", exc_info=True)


class _BrainState:
    """Thread-safe state holder that broadcasts to SSE clients."""

    def __init__(self) -> None:
        self.state = "idle"
        self.energy = 0.0
        self._clients: List[Any] = []
        self._lock = threading.Lock()

    def update(self, state: Optional[str] = None, energy: Optional[float] = None) -> None:
        if state is not None:
            self.state = state
        if energy is not None:
            self.energy = max(0.0, min(1.0, energy))
        msg = json.dumps({"state": self.state, "energy": self.energy})
        with self._lock:
            dead = []
            for wfile in self._clients:
                try:
                    wfile.write(f"data: {msg}\n\n".encode())
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


_brain_state = _BrainState()


# ---------------------------------------------------------------------------
# Vault event bus — feeds the "second brain" animation in Mission Control
# ---------------------------------------------------------------------------


class _VaultEventBus:
    """Fans Obsidian read/write events out to all connected SSE clients."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: List[Any] = []
        self._subscribed_to_obsidian = False

    def _ensure_obsidian_hook(self) -> None:
        if self._subscribed_to_obsidian:
            return
        try:
            from openjarvis.tools import obsidian_brain
            obsidian_brain.subscribe_vault_events(self.broadcast)
            self._subscribed_to_obsidian = True
        except Exception:
            logger.debug("could not subscribe to obsidian events", exc_info=True)

    def broadcast(self, event: dict) -> None:
        msg = ("data: " + json.dumps(event) + "\n\n").encode("utf-8")
        with self._lock:
            dead = []
            for wfile in self._clients:
                try:
                    wfile.write(msg)
                    wfile.flush()
                except Exception:
                    dead.append(wfile)
            for d in dead:
                self._clients.remove(d)

    def subscribe(self, wfile: Any) -> None:
        self._ensure_obsidian_hook()
        with self._lock:
            self._clients.append(wfile)

    def unsubscribe(self, wfile: Any) -> None:
        with self._lock:
            self._clients = [c for c in self._clients if c is not wfile]


# ---------------------------------------------------------------------------
# Chat history bus — feeds the right-edge slide-out chat widget in the HUD.
# Rolling in-memory buffer of the last N exchanges; broadcasts new turns to
# subscribed SSE clients. Cleared on Jarvis restart by design (per the
# 2026-04-28 spec: A1 — current-session in-memory only). Cross-session
# replay from Brain/Daily/ is a future-work add.
# ---------------------------------------------------------------------------


class _ChatHistoryBus:
    """Thread-safe ring buffer of operator/jarvis exchanges + pub-sub.

    Two event shapes broadcast:
        {"kind": "msg", "role": "operator"|"jarvis", "content": ..., "ts": float}
        {"kind": "toggle", "action": "open"|"close"}
    """

    MAX_TURNS = 200            # rolling cap; ~200 exchanges = ~30k chars typical

    def __init__(self) -> None:
        from collections import deque
        self._lock = threading.Lock()
        self._clients: List[Any] = []
        self._buffer: "deque[Dict[str, Any]]" = deque(maxlen=self.MAX_TURNS)

    def _broadcast(self, event: Dict[str, Any]) -> None:
        msg = ("data: " + json.dumps(event) + "\n\n").encode("utf-8")
        with self._lock:
            n_clients = len(self._clients)
            dead = []
            for wfile in self._clients:
                try:
                    wfile.write(msg)
                    wfile.flush()
                except Exception:
                    dead.append(wfile)
            for d in dead:
                self._clients.remove(d)
            n_alive = len(self._clients)
        # Loud diagnostic — operator pastes terminal output if chat
        # history stays empty. Tag is grep-friendly.
        kind = event.get("kind", "?")
        try:
            print(f"[CHATBUS] broadcast kind={kind} clients={n_clients}->{n_alive}",
                  flush=True)
        except Exception:
            pass

    def append_pair(self, operator_text: str, jarvis_text: str) -> None:
        """Record one operator → jarvis exchange. Called from /chat and
        /voice_turn after the response is finalised. Empty strings on
        either side are skipped (e.g. an attachment-only message has no
        operator text — we still record the response)."""
        ts = time.time()
        try:
            print(f"[CHATBUS] append_pair op={len(operator_text or '')}c "
                  f"jv={len(jarvis_text or '')}c", flush=True)
        except Exception:
            pass
        with self._lock:
            if (operator_text or "").strip():
                self._buffer.append({
                    "role": "operator", "content": operator_text, "ts": ts,
                })
            if (jarvis_text or "").strip():
                self._buffer.append({
                    "role": "jarvis", "content": jarvis_text, "ts": ts,
                })
        # Emit each captured message as its own event so the widget
        # animates them in one-by-one.
        if (operator_text or "").strip():
            self._broadcast({"kind": "msg", "role": "operator",
                             "content": operator_text, "ts": ts})
        if (jarvis_text or "").strip():
            self._broadcast({"kind": "msg", "role": "jarvis",
                             "content": jarvis_text, "ts": ts})

    def emit_widget(self, widget: Dict[str, Any]) -> None:
        """Push a widget render request to all subscribed HUD clients.

        Side-channel for tools that produce visual output alongside a
        text reply (maps_locate → map widget, weather → weather card,
        image_search → image card, link_card → link preview).

        The HUD attaches the widget below the most recent jarvis chat
        bubble, so the visual answer travels with the spoken reply.
        Tools call this directly; the LLM still sees a normal string
        result describing what happened. This avoids widening the
        return type of every tool/process_command call site.

        Schema (caller's responsibility to fill correctly):
            {"type": "map", "data": {"lat": 51.5, "lon": -0.1,
                                     "label": "London"}}
            {"type": "image", "data": {"url": "...", "caption": "..."}}
            {"type": "link", "data": {"url": "...", "title": "...",
                                      "description": "..."}}
            {"type": "weather", "data": {"location": "London",
                                          "temp_c": 12,
                                          "condition": "Cloudy"}}
        """
        if not isinstance(widget, dict) or not widget.get("type"):
            return
        self._broadcast({
            "kind": "widget",
            "widget": widget,
            "ts": time.time(),
        })

    def emit_toggle(self, action: str, target: str = "chat") -> None:
        """Emit a widget-open/close hint to all subscribed HUD clients.
        Called from voice fast-paths when the operator says 'open the
        chat' / 'close the chat' / 'open the activity log' etc. The HUD
        listens and toggles the right panel based on `target`.
        Server holds no opinion about who's currently visible — this is
        purely a hint, the HUD is the source of truth for visibility.

        target: "chat" (default) | "log" | "briefing" | "markets"
        action: "open" | "close" | "toggle"
        """
        if action not in ("open", "close", "toggle"):
            return
        if target not in ("chat", "log", "briefing", "markets"):
            return
        self._broadcast({
            "kind": "toggle", "target": target, "action": action,
            "ts": time.time(),
        })

    def snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._buffer)

    def subscribe(self, wfile: Any) -> None:
        with self._lock:
            self._clients.append(wfile)
            n = len(self._clients)
        try:
            print(f"[CHATBUS] +subscribe clients={n}", flush=True)
        except Exception:
            pass

    def unsubscribe(self, wfile: Any) -> None:
        with self._lock:
            self._clients = [c for c in self._clients if c is not wfile]
            n = len(self._clients)
        try:
            print(f"[CHATBUS] -unsubscribe clients={n}", flush=True)
        except Exception:
            pass


_chat_history = _ChatHistoryBus()


def emit_widget(widget_type: str, data: Dict[str, Any]) -> None:
    """Public helper for tools to render a widget in the chat panel.

    Called from any tool that produces visual output alongside its
    text reply. The widget lands below the next jarvis chat bubble.
    Non-fatal: if the bus is missing or the HUD has no subscribers,
    we silently no-op so the tool's text result still flows back to
    the LLM and gets spoken.

    Example:
        emit_widget("map", {"lat": 51.5074, "lon": -0.1278,
                            "label": "London, UK", "zoom": 12})
    """
    try:
        _chat_history.emit_widget({"type": widget_type, "data": data or {}})
    except Exception:
        logger.debug("emit_widget failed", exc_info=True)


# ---------------------------------------------------------------------------
# Markets-Pro page helpers — data assembly for the standalone
# jarvis_web/markets.html UI (Dashboard / Pulse / Analyze tabs).
# ---------------------------------------------------------------------------

def _markets_pro_dashboard() -> Dict[str, Any]:
    """Dashboard tab — stats, recent analyses, and the live market
    strip across the bottom."""
    from openjarvis.markets import store
    from openjarvis.markets.sources import coingecko
    out: Dict[str, Any] = {"ok": True}
    # Stats
    recent_charts = _markets_pro_recent_analyses(limit=20)
    try:
        watchlist_count = len(store.watchlist_get() or [])
    except Exception:
        watchlist_count = 0
    try:
        from openjarvis.markets import paper_broker
        paper = paper_broker.paper_portfolio()
        win_rate = paper.get("win_rate")
        open_positions = len(paper.get("open_positions") or [])
    except Exception:
        win_rate = None
        open_positions = 0
    out["stats"] = {
        "analyses_total": _markets_pro_count_analyses(),
        "analyses_last_7d": _markets_pro_count_analyses(within_days=7),
        "watchlist_count": watchlist_count,
        "win_rate": win_rate,
        "open_positions": open_positions,
    }
    out["recent_analyses"] = recent_charts[:8]
    out["global"] = coingecko.fetch_global()
    return out


def _markets_pro_pulse(*, hide_high_risk: bool = False) -> Dict[str, Any]:
    """Pulse tab — top gainers / losers / trending + market overview.

    Uses the top-1000 universe so the long-tail movers surface — but
    those are exactly where rug-pulls hide, so the risk module
    annotates each row before it ships to the client.

    ``hide_high_risk`` (query string ``?hide_high_risk=1``) drops any
    coin scored ``high`` or ``rugpull``. Default False: badges visible
    but coins still listed, so the operator can see what's being
    pumped today without it dominating the list.
    """
    from openjarvis.markets.sources import coingecko
    from openjarvis.markets import risk as _risk
    raw = coingecko.fetch_top_n(1000) or coingecko.fetch_top_100() or []
    coins = _risk.annotate(raw)
    if hide_high_risk:
        coins = _risk.filter_clean(coins, max_label_key="caution")

    by_change = [c for c in coins if c.get("change_24h_pct") is not None]
    gainers = sorted(by_change, key=lambda c: c["change_24h_pct"],
                     reverse=True)[:12]
    losers = sorted(by_change, key=lambda c: c["change_24h_pct"])[:12]
    by_vol = sorted([c for c in coins if c.get("volume_24h")],
                    key=lambda c: c["volume_24h"], reverse=True)[:12]

    risk_counts = {"clean": 0, "caution": 0, "high": 0, "rugpull": 0}
    for c in coins:
        k = (c.get("risk") or {}).get("label_key") or "clean"
        risk_counts[k] = risk_counts.get(k, 0) + 1

    return {
        "ok": True,
        "global": coingecko.fetch_global(),
        "gainers": gainers,
        "losers": losers,
        "trending": by_vol,
        "all_count": len(coins),
        "universe_size": len(raw),
        "risk_counts": risk_counts,
        "hide_high_risk": hide_high_risk,
    }


def _markets_pro_coins_page(qs: Dict[str, List[str]]) -> Dict[str, Any]:
    from openjarvis.markets.sources import coingecko

    def _first(name: str, default: str) -> str:
        values = qs.get(name) or []
        return (values[0] if values else default) or default

    try:
        page = int(_first("page", "1"))
    except ValueError:
        page = 1
    try:
        per_page = int(_first("per_page", "100"))
    except ValueError:
        per_page = 100
    category = (_first("category", "") or "").strip() or None
    query = (_first("q", "") or _first("query", "") or "").strip() or None
    currency = (_first("currency", "gbp") or "gbp").strip().lower()
    return coingecko.fetch_markets_page(
        page=page,
        per_page=per_page,
        vs_currency=currency,
        category=category,
        query=query,
    )


def _markets_pro_coin_categories() -> Dict[str, Any]:
    from openjarvis.markets.sources import coingecko

    categories = coingecko.fetch_categories_list()
    return {"ok": True, "categories": categories, "count": len(categories)}


def _markets_pro_count_analyses(*, within_days: Optional[int] = None) -> int:
    """Count chart-analysis markdown files in
    Brain/Trading/Research/Charts/."""
    try:
        from openjarvis.tools.obsidian_brain import BRAIN_ROOT
    except Exception:
        return 0
    p = Path(BRAIN_ROOT) / "Trading" / "Research" / "Charts"
    if not p.is_dir():
        return 0
    cutoff = (time.time() - within_days * 86400) if within_days else 0
    n = 0
    try:
        for f in p.iterdir():
            if not f.is_file() or f.suffix.lower() != ".md":
                continue
            if cutoff and f.stat().st_mtime < cutoff:
                continue
            n += 1
    except Exception:
        pass
    return n


def _markets_pro_recent_analyses(*, limit: int = 8) -> List[Dict[str, Any]]:
    """Scan the Charts dir for recent chart-analysis files. Returns
    [{filename, ticker, timeframe, verdict, ts}] newest-first."""
    try:
        from openjarvis.tools.obsidian_brain import BRAIN_ROOT
    except Exception:
        return []
    p = Path(BRAIN_ROOT) / "Trading" / "Research" / "Charts"
    if not p.is_dir():
        return []
    items: List[Dict[str, Any]] = []
    try:
        files = [f for f in p.iterdir()
                 if f.is_file() and f.suffix.lower() == ".md"]
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files[:limit * 2]:    # over-fetch then trim — some may parse-fail
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            ticker = _extract_frontmatter(txt, "ticker") or "?"
            tf = _extract_frontmatter(txt, "timeframe") or "?"
            verdict = _extract_verdict(txt) or "—"
            items.append({
                "filename": f.name,
                "ticker": ticker,
                "timeframe": tf,
                "verdict": verdict,
                "ts": f.stat().st_mtime,
                "chart_image": f.with_suffix(".png").name
                                if f.with_suffix(".png").is_file() else None,
            })
            if len(items) >= limit:
                break
    except Exception:
        logger.debug("markets_pro recent_analyses failed", exc_info=True)
    return items


def _extract_frontmatter(text: str, key: str) -> Optional[str]:
    if not text.startswith("---"):
        return None
    try:
        end = text.find("\n---", 3)
        if end < 0:
            return None
        block = text[3:end]
        for line in block.splitlines():
            if line.strip().startswith(f"{key}:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return None


def _extract_verdict(text: str) -> Optional[str]:
    """Pull the verdict string from '## Verdict: LONG — high'."""
    for line in text.splitlines():
        ls = line.strip().lower()
        if ls.startswith("## verdict"):
            return line.split(":", 1)[-1].strip(" *#")
    return None



def _markets_pro_paper_portfolio() -> Dict[str, Any]:
    from openjarvis.markets import paper_broker
    paper_broker.check_open_positions()
    return paper_broker.paper_portfolio()


def _markets_pro_paper_buy(body: Dict[str, Any]) -> Dict[str, Any]:
    from openjarvis.markets import paper_broker
    ticker = (body.get("ticker") or "").strip().upper()
    gbp = body.get("gbp", body.get("gbp_amount", 0))
    return paper_broker.paper_buy(
        ticker,
        gbp,
        stop=body.get("stop"),
        tp1=body.get("tp1"),
        tp2=body.get("tp2"),
    )


def _markets_pro_paper_sell(body: Dict[str, Any]) -> Dict[str, Any]:
    from openjarvis.markets import paper_broker
    ticker = (body.get("ticker") or "").strip().upper()
    reason = (body.get("reason") or "closed_manually").strip()
    return paper_broker.paper_sell(ticker, reason=reason)


def _markets_pro_bot_backtest(body: Dict[str, Any]) -> Dict[str, Any]:
    from openjarvis.markets import bot_lab

    ticker = (body.get("ticker") or "").strip().upper()
    if not ticker:
        return {"ok": False, "error": "ticker required"}
    strategy = (body.get("strategy") or "dca").strip().lower()
    try:
        if strategy == "dca_sweep":
            allowed = {
                "take_profit_pct_values",
                "safety_order_deviation_pct_values",
                "max_safety_orders_values",
                "initial_cash_gbp",
                "base_order_gbp",
                "safety_order_gbp",
                "fee_rate",
                "slippage_pct",
            }
            kwargs = {key: body[key] for key in allowed if key in body}
            return bot_lab.sweep_dca_from_history(
                ticker,
                since_ts=body.get("since_ts"),
                limit=body.get("limit", 500),
                **kwargs,
            )
        if strategy == "grid_sweep":
            allowed = {
                "lower_price_values",
                "upper_price_values",
                "grid_count_values",
                "order_gbp_values",
                "initial_cash_gbp",
                "fee_rate",
                "slippage_pct",
            }
            kwargs = {key: body[key] for key in allowed if key in body}
            return bot_lab.sweep_grid_from_history(
                ticker,
                since_ts=body.get("since_ts"),
                limit=body.get("limit", 500),
                **kwargs,
            )
        if strategy == "grid":
            allowed = {
                "initial_cash_gbp",
                "lower_price",
                "upper_price",
                "grid_count",
                "order_gbp",
                "fee_rate",
                "slippage_pct",
            }
            kwargs = {key: body[key] for key in allowed if key in body}
            return bot_lab.backtest_grid_from_history(
                ticker,
                since_ts=body.get("since_ts"),
                limit=body.get("limit", 500),
                **kwargs,
            )
        if strategy == "signal":
            allowed = {
                "signals",
                "initial_cash_gbp",
                "default_order_gbp",
                "fee_rate",
                "slippage_pct",
            }
            kwargs = {key: body[key] for key in allowed if key in body}
            return bot_lab.backtest_signal_from_history(
                ticker,
                since_ts=body.get("since_ts"),
                limit=body.get("limit", 500),
                **kwargs,
            )
        allowed = {
            "initial_cash_gbp",
            "base_order_gbp",
            "safety_order_gbp",
            "max_safety_orders",
            "safety_order_deviation_pct",
            "take_profit_pct",
            "stop_loss_pct",
            "fee_rate",
            "slippage_pct",
        }
        kwargs = {key: body[key] for key in allowed if key in body}
        return bot_lab.backtest_dca_from_history(
            ticker,
            since_ts=body.get("since_ts"),
            limit=body.get("limit", 500),
            **kwargs,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "ticker": ticker, "strategy": strategy}


_DIRECT_DCA_RE = re.compile(
    r"\b(?P<ticker>[A-Z]{2,10})(?:/USDT|/USD|/GBP)?\b.*\bDCA\b.*\bbacktest\b"
    r"|\bDCA\b.*\b(?P<ticker_after>[A-Z]{2,10})(?:/USDT|/USD|/GBP)?\b.*\bbacktest\b",
    re.I | re.S,
)


def _try_direct_markets_chat(text: str) -> str | None:
    """Handle known Bot Lab commands without waiting on the general LLM path."""
    candidate = (text or "").strip()
    if not candidate:
        return None
    lowered = candidate.lower()
    if "backtest" not in lowered or "dca" not in lowered:
        return None
    match = _DIRECT_DCA_RE.search(candidate)
    ticker = ""
    if match:
        ticker = (match.group("ticker") or match.group("ticker_after") or "").upper()
    if ticker in {"RUN", "LIVE", "DATA", "USING", "CURRENT", "BOT", "LAB"}:
        ticker = ""
    if not ticker and "sol" in lowered:
        ticker = "SOL"
    if not ticker:
        return None
    body = {
        "ticker": ticker,
        "strategy": "dca",
        "initial_cash_gbp": 1000,
        "base_order_gbp": 100,
        "safety_order_gbp": 100,
        "max_safety_orders": 3,
        "safety_order_deviation_pct": 3,
        "take_profit_pct": 2,
        "fee_rate": 0.001,
        "slippage_pct": 0.05,
        "limit": 500,
    }
    result = _markets_pro_bot_backtest(body)
    if not result.get("ok"):
        return f"Bot Lab DCA backtest failed for {ticker}: {result.get('error') or 'unknown error'}"
    return _format_dca_backtest_chat_result(result)


def _fmt_ts(ts: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d", time.gmtime(float(ts)))
    except Exception:
        return "unknown"


def _format_dca_backtest_chat_result(result: Dict[str, Any]) -> str:
    initial = float(result.get("initial_cash_gbp") or 0.0)
    ending = float(result.get("ending_equity_gbp") or 0.0)
    net_profit = ending - initial
    ticker = result.get("ticker") or "SOL"
    return (
        f"Paper-only {ticker} DCA backtest complete. "
        f"Net profit: GBP {net_profit:.2f} ({float(result.get('roi_pct') or 0.0):.2f}% ROI). "
        f"Realised P/L: GBP {float(result.get('realized_pnl_gbp') or 0.0):.2f}; "
        f"unrealised P/L: GBP {float(result.get('unrealized_pnl_gbp') or 0.0):.2f}. "
        f"Max drawdown: {float(result.get('max_drawdown_pct') or 0.0):.2f}%; "
        f"floating drawdown: {float(result.get('max_floating_drawdown_pct') or 0.0):.2f}%. "
        f"Win rate: {float(result.get('win_rate_pct') or 0.0):.1f}% across "
        f"{int(result.get('closed_deals') or 0)} closed deals, with "
        f"{int(result.get('open_deals') or 0)} open deal and "
        f"GBP {float(result.get('capital_locked_gbp') or 0.0):.2f} still locked. "
        f"History: {int(result.get('bars') or 0)} live CoinGecko OHLCV bars, "
        f"{_fmt_ts(result.get('first_ts'))} to {_fmt_ts(result.get('last_ts'))}. "
        "Assumptions: current Bot Lab defaults, GBP 1000 starting cash, GBP 100 base order, "
        "GBP 100 safety order, 3 safety orders, 3% deviation, 2% take profit, no stop loss, "
        "0.05% slippage, 0.1% fee. No live order was placed."
    )


def _markets_pro_paper_bot_schedule(body: Dict[str, Any]) -> Dict[str, Any]:
    from openjarvis.markets import paper_scheduler

    return paper_scheduler.schedule_paper_bot(
        ticker=(body.get("ticker") or "").strip().upper(),
        strategy=(body.get("strategy") or "dca").strip().lower(),
        interval_minutes=body.get("interval_minutes", 60),
        config=body.get("config") if isinstance(body.get("config"), dict) else {},
        name=body.get("name"),
        execute_paper=bool(body.get("execute_paper", False)),
        confirm_paper_execution=bool(body.get("confirm_paper_execution", False)),
    )


def _markets_pro_paper_bot_list() -> Dict[str, Any]:
    from openjarvis.markets import paper_scheduler

    return paper_scheduler.list_paper_bots()


def _markets_pro_paper_bot_cancel(body: Dict[str, Any]) -> Dict[str, Any]:
    from openjarvis.markets import paper_scheduler

    return paper_scheduler.cancel_paper_bot(str(body.get("id") or ""))


def _markets_pro_paper_bot_run_due(body: Dict[str, Any]) -> Dict[str, Any]:
    from openjarvis.markets import paper_scheduler

    return paper_scheduler.run_due_paper_bots(now_ts=body.get("now_ts"))


def _markets_pro_paper_bot_approve_execution(body: Dict[str, Any]) -> Dict[str, Any]:
    from openjarvis.markets import paper_scheduler

    return paper_scheduler.approve_paper_execution(
        str(body.get("id") or body.get("bot_id") or ""),
        approval_phrase=str(body.get("approval_phrase") or ""),
    )


def _round_gb(mb: int | float | None) -> float | None:
    if mb is None:
        return None
    return round(float(mb) / 1024.0, 1)


def _system_health_snapshot() -> Dict[str, Any]:
    """Return a lightweight live hardware snapshot for the HUD."""
    health: Dict[str, Any] = {
        "gpu": {
            "online": False,
            "name": "",
            "util_percent": None,
            "memory_used_mb": None,
            "memory_total_mb": None,
            "memory_percent": None,
            "power_w": None,
        },
        "cpu_percent": None,
        "ram_used_gb": None,
        "ram_total_gb": None,
        "ram_percent": None,
    }
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total,power.draw",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        line = (result.stdout or "").splitlines()[0].strip()
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 5:
            used = int(float(parts[2]))
            total = int(float(parts[3]))
            if total > 0 and used > total:
                used = total
            health["gpu"].update(
                {
                    "online": True,
                    "name": parts[0],
                    "util_percent": int(float(parts[1])),
                    "memory_used_mb": used,
                    "memory_total_mb": total,
                    "memory_used_gb": _round_gb(used),
                    "memory_total_gb": _round_gb(total),
                    "memory_percent": round((used / total) * 100) if total else None,
                    "power_w": round(float(parts[4]), 1),
                }
            )
    except Exception:
        logger.debug("nvidia-smi health snapshot unavailable", exc_info=True)

    try:
        import psutil

        memory = psutil.virtual_memory()
        health.update(
            {
                "cpu_percent": round(float(psutil.cpu_percent(interval=None))),
                "ram_used_gb": round((memory.total - memory.available) / (1024 ** 3), 1),
                "ram_total_gb": round(memory.total / (1024 ** 3), 1),
                "ram_percent": round(float(memory.percent)),
            }
        )
    except Exception:
        logger.debug("psutil health snapshot unavailable", exc_info=True)
        if os.name == "nt":
            try:
                import ctypes

                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]

                status = MEMORYSTATUSEX()
                status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
                used = status.ullTotalPhys - status.ullAvailPhys
                health.update(
                    {
                        "ram_used_gb": round(used / (1024 ** 3), 1),
                        "ram_total_gb": round(status.ullTotalPhys / (1024 ** 3), 1),
                        "ram_percent": int(status.dwMemoryLoad),
                    }
                )
            except Exception:
                logger.debug("windows memory snapshot unavailable", exc_info=True)
    return health


def _markets_pro_analyze(body: Dict[str, Any]) -> Dict[str, Any]:
    """Decode posted image + run chart_analyst + return inline result."""
    from openjarvis.markets import chart_analyst
    img_b64 = body.get("image_b64") or ""
    if not img_b64:
        return {"ok": False, "error": "image_b64 required"}
    name = (body.get("image_name") or "chart.png").replace("\\", "_").replace("/", "_")[:120]
    ticker_hint = (body.get("ticker_hint") or "").strip() or None
    timeframe = body.get("timeframe") or "2h"
    forecast_horizon = (body.get("forecast_horizon") or "3d").strip()
    if forecast_horizon not in {"24h", "3d", "7d", "30d"}:
        forecast_horizon = "3d"
    # Persist to Brain/Inbox/ for trace + so chart_analyst can read it
    try:
        from openjarvis.tools import obsidian_brain
        obsidian_brain._ensure_layout()
        inbox = obsidian_brain.INBOX_DIR
    except Exception:
        inbox = Path(os.path.expanduser("~/Obsidian/Claude/Brain/Inbox"))
        inbox.mkdir(parents=True, exist_ok=True)
    from datetime import datetime as _dt
    stamp = _dt.now().strftime("%Y-%m-%d-%H%M%S")
    img_path = inbox / f"markets-pro-{stamp}-{name}"
    try:
        img_path.write_bytes(base64.b64decode(img_b64))
    except Exception:
        return {"ok": False, "error": "could not decode image_b64"}
    raw = chart_analyst.analyze_chart(
        image_path=str(img_path),
        ticker_hint=ticker_hint,
        timeframe=timeframe,
        forecast_horizon=forecast_horizon,
    )
    try:
        result = json.loads(raw)
    except Exception:
        return {"ok": False, "error": "analyzer returned non-JSON",
                "raw": raw[:500]}
    # Inline the rendered PNG + the markdown body for the page
    chart_b64 = ""
    chart_image = result.get("chart_image")
    if chart_image:
        try:
            chart_b64 = base64.b64encode(
                Path(chart_image).read_bytes()
            ).decode("ascii")
        except Exception:
            chart_b64 = ""
    md_body = ""
    md_path = result.get("vault_note")
    if md_path:
        try:
            md_body = Path(md_path).read_text(encoding="utf-8")
        except Exception:
            md_body = ""
    result["chart_image_b64"] = chart_b64
    result["markdown_body"] = md_body
    return result


def _jarvis_os_state() -> Dict[str, Any]:
    """Assemble the first Jarvis OS desktop payload.

    The first build intentionally returns stable, conservative widget
    contracts. Where live data is unavailable or expensive, the widget marks
    itself as "pending" rather than triggering heavy work on page load.
    """
    now = time.strftime("%H:%M")
    health = _system_health_snapshot()
    gpu = health.get("gpu") or {}
    widgets: Dict[str, Any] = {
        "missions": {"label": "Active missions", "value": 0, "status": "pending"},
        "agents": {"label": "Agent queue", "value": 0, "status": "pending"},
        "plugins": {"label": "Plugin learning queue", "value": "Ready", "status": "ready"},
        "markets": {"label": "Market pulse", "value": "Idle", "status": "pending"},
        "gpu": {
            "label": "GPU / local load",
            "value": (
                f"{gpu.get('util_percent')}%"
                if gpu.get("util_percent") is not None
                else "Unavailable"
            ),
            "status": "ready" if gpu.get("online") else "pending",
            "sub": (
                f"{gpu.get('name')} / {gpu.get('memory_used_gb')} of {gpu.get('memory_total_gb')} GB VRAM"
                if gpu.get("online")
                else "nvidia-smi unavailable"
            ),
        },
        "schedule": {"label": "Scheduled work", "value": 0, "status": "pending"},
        "inbox": {"label": "Approvals / escalations", "value": 0, "status": "ready"},
        "memory": {"label": "Brain memory", "value": "Loaded", "status": "ready"},
    }
    try:
        snapshot = orch_bridge.get_snapshot()
        tasks = snapshot.get("tasks") or []
        agents = snapshot.get("agents") or []
        active_tasks = [
            t for t in tasks
            if (t.get("status") or "").lower() in {"running", "queued", "pending"}
        ]
        widgets["missions"]["value"] = len(active_tasks)
        widgets["missions"]["status"] = "ready"
        widgets["agents"]["value"] = len(agents)
        widgets["agents"]["status"] = "ready"
    except Exception:
        logger.debug("jarvis os orch snapshot unavailable", exc_info=True)
    try:
        from openjarvis.tools import agent_runner
        schedules = agent_runner.list_scheduled() or []
        widgets["schedule"]["value"] = len(schedules)
        widgets["schedule"]["status"] = "ready"
    except Exception:
        logger.debug("jarvis os schedule snapshot unavailable", exc_info=True)
    return {
        "ok": True,
        "time": now,
        "model": {
            "primary": "qwen3.6:27b",
            "alias": "qwen3.6-27b-local",
            "mode": "local-first",
            "escalation": "Claude/Codex standby",
        },
        "system": health,
        "widgets": widgets,
        "actions": [
            "New Mission",
            "New Project",
            "Plugin Studio",
            "Model Center",
            "Memory",
            "Settings",
        ],
    }


def _studio_plugins() -> List[Dict[str, Any]]:
    plugins: List[Dict[str, Any]] = []
    try:
        codegraph = _codegraph_status()
        plugins.append({
            "id": "codegraph",
            "name": "CodeGraph",
            "status": "online" if codegraph.get("online") else "attention",
            "summary": f"{codegraph.get('files') or 0} files, {codegraph.get('nodes') or 0} nodes",
            "details": codegraph,
        })
    except Exception as exc:
        plugins.append({"id": "codegraph", "name": "CodeGraph", "status": "offline", "error": str(exc)})
    try:
        from openjarvis.tools import agentmemory_client

        health = agentmemory_client.health()
        plugins.append({
            "id": "agentmemory",
            "name": "AgentMemory",
            "status": "online" if health else "attention",
            "summary": "episodic memory",
            "details": {"online": bool(health)},
        })
    except Exception as exc:
        plugins.append({"id": "agentmemory", "name": "AgentMemory", "status": "offline", "error": str(exc)})
    try:
        from openjarvis.tools import obsidian_brain

        root = Path(obsidian_brain.BRAIN_ROOT)
        plugins.append({
            "id": "obsidian",
            "name": "Obsidian Vault",
            "status": "online" if root.exists() else "attention",
            "summary": str(root),
        })
    except Exception as exc:
        plugins.append({"id": "obsidian", "name": "Obsidian Vault", "status": "offline", "error": str(exc)})
    return plugins


def _studio_state() -> Dict[str, Any]:
    from openjarvis.tools.studio_store import StudioStore
    from openjarvis.tools import studio_runner

    store = StudioStore()
    studio_runner.sync_completed_run_outputs(store)
    state = store.initial_state()
    state["ok"] = True
    state["model"] = {
        "primary": "qwen3.6:27b",
        "alias": "qwen3.6-27b-local",
        "mode": "local-first",
        "escalation": "Claude/Codex standby",
    }
    state["system"] = _system_health_snapshot()
    state["plugins"] = _studio_plugins()
    try:
        state["orchestration"] = orch_bridge.get_snapshot()
    except Exception:
        state["orchestration"] = {"tasks": [], "agents": []}
    try:
        from openjarvis.tools import agent_runner

        state["automations"] = agent_runner.list_scheduled()
        state["provider"] = agent_runner.get_provider_mode()
    except Exception:
        state["automations"] = []
        state["provider"] = "auto"
    return state


def emit_chat_widget_toggle(action: str) -> None:
    """Public bridge for voice_cmd / fast-paths to nudge the HUD's chat
    widget open or closed. Decoupled from _chat_history's internal class
    so importers don't need to touch the bus directly."""
    _chat_history.emit_toggle(action, target="chat")


def emit_ui_toggle(target: str, action: str) -> None:
    """Generalised version of emit_chat_widget_toggle that supports
    multiple HUD targets. Currently 'chat' and 'log' are recognised by
    the HUD; adding more targets is a one-line addition in the HUD's
    connectChatSSE handler."""
    _chat_history.emit_toggle(action, target=target)


_vault_bus = _VaultEventBus()


def _vault_openapi_schema() -> dict:
    """OpenAPI 3.1 schema served at /vault/openapi.json — paste this URL into
    a ChatGPT Custom GPT's "Add Action" flow to wire ChatGPT to the vault.

    The base URL is auto-discovered from ``OPENJARVIS_TUNNEL_URL`` (the
    Cloudflare named tunnel), or falls back to localhost.
    """
    base = os.environ.get("OPENJARVIS_TUNNEL_URL", "").strip() or "http://127.0.0.1:7710"
    # Only declare auth in the schema if the server actually enforces it.
    # When OPENJARVIS_VAULT_TOKEN is unset the endpoints are open, so requiring
    # auth in the schema would just block ChatGPT from saving the Action.
    auth_required = bool(os.environ.get("OPENJARVIS_VAULT_TOKEN", "").strip())
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Jarvis Vault API",
            "version": "1.0.0",
            "description": (
                "Read/write access to the user's J.A.R.V.I.S. Obsidian vault — "
                "the persistent second brain that Jarvis (voice) and the user "
                "share. Use this to remember things, recall past notes, and "
                "review the daily voice journal."
            ),
        },
        "servers": [{"url": base}],
        "paths": {
            "/vault/remember": {
                "post": {
                    "operationId": "rememberNote",
                    "summary": "Save a new note to the vault",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "required": ["content"],
                            "properties": {
                                "content": {"type": "string", "description": "Body of the note (markdown allowed)"},
                                "title":   {"type": "string", "description": "Short title (defaults to first ~80 chars of content)"},
                                "folder":  {"type": "string", "enum": ["Knowledge", "Projects", "People", "Decisions"], "default": "Knowledge"},
                                "tags":    {"type": "array", "items": {"type": "string"}},
                            },
                        }}},
                    },
                    "responses": {"200": {"description": "Note created", "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {"ok": {"type": "boolean"}, "name": {"type": "string"}, "path": {"type": "string"}},
                    }}}}},
                },
            },
            "/vault/recall": {
                "get": {
                    "operationId": "recallNotes",
                    "summary": "Search the vault by keyword",
                    "parameters": [
                        {"name": "q", "in": "query", "required": True, "schema": {"type": "string"}, "description": "Search query"},
                        {"name": "limit", "in": "query", "required": False, "schema": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20}},
                    ],
                    "responses": {"200": {"description": "Matching notes with snippets"}},
                },
            },
            "/vault/get": {
                "get": {
                    "operationId": "getNote",
                    "summary": "Read the full content of a single note by name",
                    "parameters": [{"name": "name", "in": "query", "required": True, "schema": {"type": "string"}, "description": "Note filename without .md"}],
                    "responses": {"200": {"description": "Full note content"}, "404": {"description": "Not found"}},
                },
            },
            "/vault/list": {
                "get": {
                    "operationId": "listNotes",
                    "summary": "List notes in a folder, newest first",
                    "parameters": [
                        {"name": "folder", "in": "query", "required": False, "schema": {"type": "string", "enum": ["", "Knowledge", "Projects", "People", "Decisions", "Daily", "Sessions"]}, "description": "Subfolder under Brain/ (empty = whole brain)"},
                        {"name": "limit",  "in": "query", "required": False, "schema": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100}},
                    ],
                    "responses": {"200": {"description": "Listing of notes with modified time"}},
                },
            },
            "/vault/journal": {
                "get": {
                    "operationId": "readJournal",
                    "summary": "Read a daily voice journal (today by default)",
                    "parameters": [{"name": "date", "in": "query", "required": False, "schema": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"}, "description": "ISO date YYYY-MM-DD; omit for today"}],
                    "responses": {"200": {"description": "Journal markdown content"}, "404": {"description": "No journal for that date"}},
                },
            },
        },
        # Auth schema is conditional — only present when the server enforces a token.
        **({"components": {
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Token from OPENJARVIS_VAULT_TOKEN env var on the server.",
                },
            },
        }, "security": [{"BearerAuth": []}]} if auth_required else {}),
    }


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(_WEB_DIR), **kwargs)

    def end_headers(self) -> None:
        # Disable browser caching for HTML/JS/CSS — we iterate on the mission-
        # control page frequently; stale caches cause "where's the button?"
        # confusion. Only applies to text resources — audio/images can cache.
        path_lower = (self.path or "").lower()
        if any(path_lower.endswith(ext) or ext + "?" in path_lower
               for ext in (".html", ".js", ".css")) or self.path in ("/", "/brain", "/phone"):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self) -> None:
        try:
            self._dispatch_get()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError) as exc:
            # Client closed the connection while we were responding —
            # the same kind of mid-response disconnect _json_response handles
            # internally, but the browser can also drop while super().do_GET()
            # is mid-stream of a static file. Log at debug, swallow the trace.
            logger.debug("client disconnect during GET %s: %s", self.path, exc)

    def _dispatch_get(self) -> None:
        # Strip the query string before matching open routes — the auto-opened
        # local URL adds ?host=192.168.x.x:7710 which would otherwise miss
        # the /brain.html match and bounce to the auth gate.
        from urllib.parse import urlparse
        path_only = urlparse(self.path).path

        # Open routes (no auth required) — needed so the login modal can load,
        # the Custom GPT keeps working, and the PWA shell can boot.
        if path_only == "/auth/check":
            return self._handle_auth_check()
        if path_only == "/vault/openapi.json":
            # Audit 2026-04-26 L1: serving this open lets any scanner
            # hitting the public tunnel fingerprint the instance as a
            # Jarvis vault and learn the full /vault/* API surface.
            # Require the bearer token (so ChatGPT can still fetch it
            # with its Authorization header configured) but reject
            # anonymous scanners.
            if not self._check_vault_auth():
                return self._json_response(401, {
                    "error": "openapi schema requires bearer token",
                })
            return self._json_response(200, _vault_openapi_schema())
        if path_only in ("/", "/brain", "/brain.html", "/phone", "/phone.html",
                          "/markets", "/markets.html",
                          "/studio", "/studio.html",
                          "/jarvis-os", "/jarvis-os.html",
                          "/manifest.webmanifest"):
            # Static shell — auth happens client-side via the modal
            if path_only in ("/", "/brain", "/brain.html"):
                self.path = "/brain.html"
            elif path_only in ("/phone", "/phone.html"):
                self.path = "/phone.html"
            elif path_only in ("/markets", "/markets.html"):
                self.path = "/markets.html"
            elif path_only in ("/studio", "/studio.html"):
                self.path = "/studio.html"
            elif path_only in ("/jarvis-os", "/jarvis-os.html"):
                # Compatibility alias: super().do_GET() serves Studio below.
                self.path = "/studio.html"
            return super().do_GET()
        if path_only.startswith("/icons/"):
            return super().do_GET()
        # /vault/* uses its own bearer-token auth, separate from the PIN gate
        if path_only.startswith("/vault/recall") or path_only.startswith("/vault/get") \
           or path_only.startswith("/vault/list") or path_only.startswith("/vault/journal"):
            return self._dispatch_get_unauthenticated()

        # Everything else is locked behind the PIN gate
        if not self._require_pin_auth():
            return
        return self._dispatch_get_unauthenticated()

    def _dispatch_get_unauthenticated(self) -> None:
        # The original routing logic — only reached after auth has passed
        # OR for the routes that have their own auth (vault/* with bearer).
        if self.path.startswith("/events"):
            self._handle_sse()
        elif self.path.startswith("/orch_events"):
            self._handle_orch_sse()
        elif self.path.startswith("/vault_events"):
            self._handle_vault_sse()
        elif self.path.startswith("/chat_events"):
            self._handle_chat_sse()
        elif self.path.startswith("/agent_stats"):
            # /agent_stats[?id=<agent_id>][&days=N] — outcome-aggregated
            # stats per agent or all agents. Backed by Phase L-1
            # outcomes module (~/.openjarvis/outcomes/<date>/*.json).
            self._handle_agent_stats_get()
        elif self.path.startswith("/briefing"):
            # Briefing reader endpoint — /briefing or /briefing?date=YYYY-MM-DD.
            # Returns the AI-pulse note from Brain/Knowledge/ for the
            # requested date (defaults to most-recent), plus a list of
            # available dates so the HUD widget can populate a dropdown.
            self._handle_briefing_get()
        elif self.path.startswith("/digest"):
            # Learning digest reader — /digest or /digest?date=YYYY-MM-DD.
            # Returns the Jarvis learning digest from Brain/Knowledge/ for
            # the requested date (defaults to most-recent). Mirrors the
            # /briefing contract so the HUD can present digest + briefing
            # with the same UX. Source: learning-reviewer agent, daily.
            self._handle_digest_get()
        elif self.path.startswith("/markets/"):
            # Markets subsystem — Day-1 read endpoints for the HUD panel.
            # /markets/watchlist  → operator's tracked tickers + cached prices
            # /markets/today      → placeholder for the 06:15 briefing artefact
            #                       (LLM pipeline ships next session)
            # /markets/health     → SQLite + ingestion source health
            self._handle_markets_get()
        elif self.path.startswith("/markets-pro/"):
            # Standalone Markets-Pro page (jarvis_web/markets.html) data
            # endpoints — separate from the in-HUD slide-out panel.
            self._handle_markets_pro_get()
        elif self.path.startswith("/tiktok"):
            self._handle_tiktok_get()
        elif self.path == "/chat_history":
            # One-shot snapshot of the in-memory chat ring buffer. The HUD
            # widget calls this on first open to seed past bubbles, then
            # subscribes to /chat_events for live appends. Cleared on
            # Jarvis restart by design.
            try:
                self._json_response(200, {"messages": _chat_history.snapshot()})
            except Exception:
                logger.exception("/chat_history failed")
                self._json_response(500, {"error": "internal error", "ref": _err_ref()})
        elif self.path == "/vault":
            # one-shot snapshot of the recent ring buffer (for late joiners)
            try:
                from openjarvis.tools.obsidian_brain import recent_events
                self._json_response(200, {"events": recent_events()})
            except Exception:
                self._json_response(200, {"events": []})
        elif self.path == "/vault/summary":
            self._handle_vault_summary()
        elif self.path in ("/memory-vault", "/memory-vault/"):
            self.path = "/memory-vault.html"
            super().do_GET()
        elif self.path == "/vault_graph":
            try:
                from openjarvis.tools.obsidian_brain import parse_graph
                self._json_response(200, parse_graph())
            except Exception as exc:
                logger.exception("/vault_graph failed")
                self._json_response(500, {"error": "internal error", "ref": _err_ref()})
        elif self.path == "/vault/openapi.json":
            # OpenAPI schema for the ChatGPT Custom GPT "Add Action"
            # flow. Audit 2026-04-26 L1: bearer-gated to stop public
            # scanners fingerprinting Jarvis. (Also handled at the
            # open-routes top — this branch is rarely hit but kept for
            # the symmetry.)
            if not self._check_vault_auth():
                return self._json_response(401, {"error": "openapi schema requires bearer token"})
            self._json_response(200, _vault_openapi_schema())
        elif self.path.startswith("/vault/recall"):
            self._handle_vault_recall()
        elif self.path.startswith("/vault/get"):
            self._handle_vault_get()
        elif self.path.startswith("/vault/list"):
            self._handle_vault_list()
        elif self.path.startswith("/vault/journal"):
            self._handle_vault_journal()
        elif self.path == "/orch":
            self._json_response(200, orch_bridge.get_snapshot())
        elif self.path == "/jarvis-os/state":
            self._json_response(200, _studio_state())
        elif self.path == "/studio/state":
            self._json_response(200, _studio_state())
        elif self.path.startswith("/studio/projects"):
            try:
                from openjarvis.tools.studio_store import StudioStore

                self._json_response(200, {"projects": StudioStore().list_projects()})
            except Exception:
                logger.exception("/studio/projects failed")
                self._json_response(500, {"error": "internal error", "ref": _err_ref()})
        elif self.path.startswith("/studio/chats"):
            try:
                from urllib.parse import parse_qs, urlparse
                from openjarvis.tools.studio_store import StudioStore

                qs = parse_qs(urlparse(self.path).query)
                project_id = (qs.get("project_id") or [None])[0]
                self._json_response(200, {"chats": StudioStore().list_chats(project_id)})
            except Exception:
                logger.exception("/studio/chats failed")
                self._json_response(500, {"error": "internal error", "ref": _err_ref()})
        elif self.path.startswith("/studio/runs"):
            try:
                from urllib.parse import parse_qs, urlparse
                from openjarvis.tools.studio_store import StudioStore

                qs = parse_qs(urlparse(self.path).query)
                project_id = (qs.get("project_id") or [None])[0]
                chat_id = (qs.get("chat_id") or [None])[0]
                self._json_response(200, {"runs": StudioStore().list_runs(project_id, chat_id)})
            except Exception:
                logger.exception("/studio/runs failed")
                self._json_response(500, {"error": "internal error", "ref": _err_ref()})
        elif self.path.startswith("/studio/search"):
            try:
                from urllib.parse import parse_qs, urlparse
                from openjarvis.tools.studio_store import StudioStore

                qs = parse_qs(urlparse(self.path).query)
                query = (qs.get("q") or [""])[0]
                self._json_response(200, {"results": StudioStore().search(query)})
            except Exception:
                logger.exception("/studio/search failed")
                self._json_response(500, {"error": "internal error", "ref": _err_ref()})
        elif self.path == "/studio/plugins":
            self._json_response(200, {"plugins": _studio_plugins()})
        elif self.path == "/studio/automations":
            try:
                from openjarvis.tools import agent_runner

                self._json_response(200, {"automations": agent_runner.list_scheduled()})
            except Exception:
                logger.exception("/studio/automations failed")
                self._json_response(500, {"error": "internal error", "ref": _err_ref()})
        elif self.path == "/provider":
            try:
                from openjarvis.tools import agent_runner
                self._json_response(200, {"mode": agent_runner.get_provider_mode()})
            except Exception as exc:
                logger.exception("/provider get failed")
                self._json_response(500, {"error": "internal error", "ref": _err_ref()})
        # /unifi + /unifi_events removed — UniFi bridge no longer active
        elif self.path == "/schedule":
            try:
                from openjarvis.tools import agent_runner
                self._json_response(200, {"schedules": agent_runner.list_scheduled()})
            except Exception as exc:
                logger.exception("/schedule list failed")
                self._json_response(500, {"error": "internal error", "ref": _err_ref()})
        elif self.path == "/commands":
            self._handle_commands_list()
        elif self.path in ("/graphify", "/graphify/"):
            # Our 3d-force-graph viz, served from jarvis_web/. Falls back
            # to graphify's stock HTML if our custom file is missing.
            our = _WEB_DIR / "graphify.html"
            if our.exists():
                self.path = "/graphify.html"
                super().do_GET()
            else:
                self._serve_graphify_file("graph.html", "text/html")
        elif self.path in ("/graphify/static", "/graphify/static/"):
            # Graphify's own stock HTML viz — kept available as a fallback
            self._serve_graphify_file("graph.html", "text/html")
        elif self.path == "/graphify/graph.json":
            self._serve_graphify_file("graph.json", "application/json")
        elif self.path == "/graphify/report":
            self._serve_graphify_file("GRAPH_REPORT.md", "text/markdown; charset=utf-8")
        elif self.path == "/graphify/status":
            self._handle_graphify_status()
        elif self.path == "/codegraph/status":
            self._json_response(200, _codegraph_status())
        elif self.path in ("/codegraph", "/codegraph/"):
            self.path = "/codegraph.html"
            super().do_GET()
        elif self.path == "/music/status":
            self._handle_music_status()
        elif self.path in ("/", "/brain", "/brain.html"):
            self.path = "/brain.html"
            super().do_GET()
        elif self.path in ("/phone", "/phone.html"):
            self.path = "/phone.html"
            super().do_GET()
        elif self.path in ("/jarvis-os", "/jarvis-os.html"):
            self.path = "/studio.html"
            super().do_GET()
        elif self.path in ("/studio", "/studio.html"):
            self.path = "/studio.html"
            super().do_GET()
        else:
            super().do_GET()

    def do_POST(self) -> None:
        try:
            self._dispatch_post()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError) as exc:
            logger.debug("client disconnect during POST %s: %s", self.path, exc)

    def _dispatch_post(self) -> None:
        # Open routes — login + ChatGPT vault writes + Claude Code hooks.
        # /claude_event is open because the hooks (shell scripts spawned by
        # Claude Code itself) can't easily authenticate. Worst case: someone
        # who knows the URL can fake "session" events on the HUD — purely
        # cosmetic, no protected action triggered. ChatGPT vault writes use
        # their own bearer token (OPENJARVIS_VAULT_TOKEN) checked internally.
        if self.path == "/auth":
            return self._handle_auth_login()
        if self.path == "/auth/logout":
            return self._handle_auth_logout()
        if self.path == "/vault/remember":
            return self._handle_vault_remember()  # uses _check_vault_auth internally
        if self.path == "/claude_event":
            return self._handle_claude_event()

        # Everything else is locked
        if not self._require_pin_auth():
            return

        if self.path == "/voice_turn":
            self._handle_voice_turn()
        elif self.path == "/command":
            self._handle_text_command()
        elif self.path == "/chat":
            self._handle_chat()
        elif self.path == "/agent_task":
            self._handle_agent_task()
        elif self.path == "/studio/chats":
            self._handle_studio_chat_create()
        elif self.path == "/studio/runs":
            self._handle_studio_run_create()
        elif self.path.startswith("/studio/runs/") and self.path.endswith("/evidence"):
            self._handle_studio_run_evidence()
        # /claude_event handled at the open-routes top (skipped here)
        # /vault/remember handled at the open-routes top too (skipped here)
        elif self.path.startswith("/agent_task/cancel/"):
            self._handle_agent_cancel(self.path.rsplit("/", 1)[-1])
        elif self.path == "/agents/wake_all":
            self._handle_wake_all()
        elif self.path == "/agents/cancel_all":
            self._handle_cancel_all()
        elif self.path == "/provider":
            self._handle_provider_set()
        elif self.path == "/graphify/refresh":
            self._handle_graphify_refresh()
        elif self.path == "/schedule":
            self._handle_schedule_create()
        elif self.path.startswith("/schedule/cancel/"):
            self._handle_schedule_cancel(self.path.rsplit("/", 1)[-1])
        elif self.path == "/content/kickoff":
            self._handle_content_kickoff()
        elif self.path.startswith("/markets/"):
            self._handle_markets_post()
        elif self.path.startswith("/markets-pro/"):
            self._handle_markets_pro_post()
        elif self.path.startswith("/tiktok/"):
            self._handle_tiktok_post()
        else:
            self.send_error(404, "Not Found")

    def _read_json_body(self) -> Dict[str, Any]:
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n) if n > 0 else b"{}"
        data = json.loads(body.decode("utf-8")) if body else {}
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def _handle_studio_chat_create(self) -> None:
        try:
            from openjarvis.tools.studio_store import StudioStore

            data = self._read_json_body()
            store = StudioStore()
            project_id = str(data.get("project_id") or "openjarvis")
            title = str(data.get("title") or "New chat")
            store.ensure_project(project_id, title=project_id)
            chat = store.create_chat(project_id, title=title)
            self._json_response(200, {"chat": chat})
        except ValueError as exc:
            self._json_response(400, {"error": str(exc)})
        except Exception:
            logger.exception("/studio/chats create failed")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _handle_studio_run_create(self) -> None:
        try:
            from openjarvis.tools.studio_runner import start_studio_run
            from openjarvis.tools.studio_store import StudioStore

            data = self._read_json_body()
            store = StudioStore()
            project_id = str(data.get("project_id") or "openjarvis")
            prompt = str(data.get("prompt") or "").strip()
            if not prompt:
                return self._json_response(400, {"error": "prompt is required"})
            chats = store.list_chats(project_id)
            chat_id = str(data.get("chat_id") or (chats[0]["id"] if chats else ""))
            if not chat_id:
                chat = store.create_chat(project_id, title=prompt[:80] or "New chat")
                chat_id = chat["id"]
            store.add_message(chat_id, "operator", prompt)
            result = start_studio_run(project_id, chat_id, prompt, approved=bool(data.get("approved")))
            run = result.get("run") or {}
            status = run.get("status", "queued")
            store.add_message(
                chat_id,
                "jarvis",
                str(result.get("reply") or f"Studio run {status}: {result.get('decision', {}).get('reason', 'workflow selected')}"),
                run_id=run.get("id"),
            )
            self._json_response(200, result)
        except KeyError as exc:
            self._json_response(404, {"error": f"chat not found: {exc}"})
        except ValueError as exc:
            self._json_response(400, {"error": str(exc)})
        except Exception:
            logger.exception("/studio/runs create failed")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _handle_studio_run_evidence(self) -> None:
        try:
            from urllib.parse import urlparse
            from openjarvis.tools.studio_runner import record_verification_evidence

            path_only = urlparse(self.path).path
            run_id = path_only.split("/")[-2]
            data = self._read_json_body()
            run = record_verification_evidence(
                run_id,
                kind=str(data.get("kind") or "manual"),
                status=str(data.get("status") or "recorded"),
                summary=str(data.get("summary") or "Verification evidence recorded"),
                command_or_check=str(data.get("command_or_check") or ""),
                artifact=str(data.get("artifact") or ""),
            )
            self._json_response(200, {"run": run})
        except KeyError as exc:
            self._json_response(404, {"error": f"run not found: {exc}"})
        except ValueError as exc:
            self._json_response(400, {"error": str(exc)})
        except Exception:
            logger.exception("/studio/runs evidence failed")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _handle_provider_set(self) -> None:
        """POST /provider — body: {"mode": "auto"|"claude"|"codex"}.

        Lets the operator force every dispatched task to a specific provider
        when one team's usage cap has been hit. Persists across restarts.
        """
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n) if n > 0 else b"{}"
            data = json.loads(body.decode("utf-8")) if body else {}
            mode = (data.get("mode") or "auto").lower()
            from openjarvis.tools import agent_runner
            new_mode = agent_runner.set_provider_mode(mode)
            self._json_response(200, {"mode": new_mode})
        except ValueError as exc:
            self._json_response(400, {"error": str(exc)})
        except Exception as exc:
            logger.exception("/provider set failed")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _graphify_dir(self) -> Path:
        """Where the graphify outputs live. Configurable via env, with a
        sensible Windows default so the operator's existing run is found."""
        env = os.environ.get("OPENJARVIS_GRAPHIFY_DIR", "").strip()
        if env:
            return Path(env)
        return Path(r"E:\Claude\Brain-Graphs\graphify-out")

    def _serve_graphify_file(self, name: str, content_type: str) -> None:
        try:
            target = self._graphify_dir() / name
            if not target.exists() or not target.is_file():
                return self._json_response(404, {
                    "error": f"graphify file not found: {name}",
                    "looked_in": str(self._graphify_dir()),
                    "hint": "run `graphify <vault path>` then refresh, or set OPENJARVIS_GRAPHIFY_DIR.",
                })
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:
            logger.exception("/graphify serve failed: %s", name)
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _handle_graphify_status(self) -> None:
        """Quick liveness/summary used by the HUD to decide whether to show the
        5th brain. Returns counts pulled from the cached graph.json + the
        live staleness counter from the bridge."""
        try:
            d = self._graphify_dir()
            graph = d / "graph.json"
            stale_info = {}
            try:
                from openjarvis.cli import graphify_bridge
                stale_info = graphify_bridge.staleness()
            except Exception:
                pass
            if not graph.exists():
                return self._json_response(200, {
                    "online": False,
                    "reason": "no graph.json yet",
                    "looked_in": str(d),
                    **stale_info,
                })
            payload = json.loads(graph.read_text(encoding="utf-8"))
            nodes = payload.get("nodes") or []
            links = payload.get("links") or []
            communities = sorted({n.get("community") for n in nodes if n.get("community") is not None})
            return self._json_response(200, {
                "online": True,
                "nodes": len(nodes),
                "edges": len(links),
                "communities": len(communities),
                "updated_at": graph.stat().st_mtime,
                "dir": str(d),
                **stale_info,
            })
        except Exception as exc:
            logger.exception("/graphify/status failed")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _handle_vault_summary(self) -> None:
        """GET /vault/summary — at-a-glance vault stats for the HUD VAULT
        button. Returns total_notes, total_bytes, last_write_iso,
        daily_today. Mirrors the contract of /graphify/status."""
        try:
            from datetime import datetime, timezone
            from openjarvis.tools import vault_stats
            from openjarvis.tools.obsidian_brain import BRAIN_ROOT
            now = datetime.now(timezone.utc)
            s = vault_stats.summary(BRAIN_ROOT, now=now)
            return self._json_response(200, {
                "online": s["total_notes"] > 0,
                **s,
                "vault_root": str(BRAIN_ROOT),
            })
        except Exception:
            logger.exception("/vault/summary failed")
            return self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _handle_music_status(self) -> None:
        """Check whether ACE-Step UI (Vite dev server) is reachable. Probe
        the configured frontend URL with a short timeout so it never
        blocks the HUD if the music app is offline. Returns the URL the
        HUD should open."""
        # ace-step-ui actually serves its frontend on 3000 (not 5173 as the
        # server/.env's FRONTEND_URL placeholder suggests). Override via
        # OPENJARVIS_MUSIC_URL env var if you've reconfigured.
        url = os.environ.get("OPENJARVIS_MUSIC_URL", "http://localhost:3000")
        try:
            import urllib.request
            with urllib.request.urlopen(url, timeout=1.5) as r:
                online = (r.status == 200)
        except Exception:
            online = False
        return self._json_response(200, {
            "online": online,
            "url": url,
            "label": "ACE-Step",
        })

    def _handle_graphify_refresh(self) -> None:
        """POST /graphify/refresh — kick off a background rebuild from the
        live vault. Non-blocking. The HUD polls /graphify/status to see
        when the new graph lands."""
        try:
            from openjarvis.cli import graphify_bridge
            res = graphify_bridge.refresh(blocking=False)
            self._json_response(200, res)
        except Exception as exc:
            logger.exception("/graphify/refresh failed")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _handle_commands_list(self) -> None:
        """Return the menu structure as JSON for the browser HUD to render."""
        try:
            from openjarvis.cli.jarvis_ui import MENU_CATEGORIES
            # Convert {category: [(label, command), ...]} → {category: [{label, command}]}
            payload = {
                cat: [{"label": lbl, "command": cmd} for lbl, cmd in items]
                for cat, items in MENU_CATEGORIES.items()
            }
            self._json_response(200, {"categories": payload})
        except Exception as exc:
            logger.exception("/commands failed")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _handle_chat(self) -> None:
        """POST /chat — body: {text, files: [{name, type, content_b64}]}.

        Like /voice_turn but driven by the chat composer in Mission Control.
        Saves any attached files to Brain/Inbox/, reads text-ish files into
        the prompt as context, runs through the same process_command pipeline.
        """
        if _ctx.process_command is None:
            return self._json_response(503, {"error": "Voice pipeline not initialised."})

        # Reuse the voice-turn lock so chat + mic + menu don't collide.
        # 30s wait (was 5s) — paired with voice_ack.emit_busy so the
        # operator hears that the brain is on a previous turn instead
        # of getting a 503 that races their fingers. Beyond 30s we do
        # 503 + busy ack so the UI isn't stuck forever.
        acquired = _voice_turn_lock.acquire(timeout=_LOCK_ACQUIRE_TIMEOUT_S)
        if not acquired:
            try:
                from openjarvis.cli import voice_ack
                voice_ack.emit_busy()
            except Exception:
                pass
            return self._json_response(503, {"error": _BUSY_MSG})
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > 50_000_000:                  # 50 MB hard cap on the whole payload
                return self._json_response(413, {"error": "Payload too large (>50MB)."})
            body = self.rfile.read(n) if n > 0 else b"{}"
            try:
                data = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError as exc:
                return self._json_response(400, {"error": f"bad JSON: {exc}"})
            text = (data.get("text") or "").strip()
            files = data.get("files") or []
            if not text and not files:
                return self._json_response(400, {"error": "'text' or 'files' required."})

            # --- Save attachments to Brain/Inbox/ + collect any plain-text content ---
            from openjarvis.tools import obsidian_brain
            from datetime import datetime
            obsidian_brain._ensure_layout()
            inbox = obsidian_brain.INBOX_DIR
            saved: List[dict] = []
            text_excerpts: List[str] = []
            stamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
            for f in files[:8]:                  # cap at 8 files per turn
                try:
                    name = (f.get("name") or "untitled").replace("\\", "_").replace("/", "_")
                    name = name[:120]
                    ftype = (f.get("type") or "").lower()
                    b64 = f.get("content_b64") or ""
                    raw = base64.b64decode(b64) if b64 else b""
                    if len(raw) > 20_000_000:    # 20 MB per file
                        continue
                    target = inbox / f"{stamp} - {name}"
                    if target.exists():
                        target = inbox / f"{stamp} - {target.stem}-{int(time.time())}{target.suffix}"
                    target.write_bytes(raw)
                    rec = {"name": name, "path": str(target), "type": ftype, "size": len(raw)}
                    saved.append(rec)
                    # Extract text content for context if it looks textual + small
                    is_text = (ftype.startswith("text/") or
                               ftype in ("application/json", "application/xml",
                                         "application/javascript", "application/x-yaml") or
                               name.lower().endswith((".md", ".txt", ".json", ".yaml", ".yml",
                                                       ".py", ".js", ".ts", ".html", ".css",
                                                       ".csv", ".sql", ".sh", ".log", ".xml")))
                    if is_text and len(raw) <= 200_000:
                        try:
                            content = raw.decode("utf-8", errors="replace")
                        except Exception:
                            content = raw.decode("latin-1", errors="replace")
                        # Trim very long files so the prompt stays manageable
                        if len(content) > 12000:
                            content = content[:12000] + "\n\n…[file truncated for prompt — full version in Inbox]"
                        text_excerpts.append(f"=== Attached file: {name} ({len(raw)} bytes) ===\n{content}\n=== end {name} ===")
                except Exception as exc:
                    logger.warning("chat attachment %s failed: %s", f.get("name"), exc)

            # --- Compose the effective prompt ---
            prompt_parts = []
            # Optional client-side conversation history. The browser sends
            # the last ~6 turns it remembers; we splice them in as a context
            # block so the model can interpret short replies like 'yes' /
            # 'continue' that depend on the previous turn. Stateless on the
            # server — no shared mutable state, so a stuck request can't
            # poison anyone else's session.
            history = data.get("history") or []
            if isinstance(history, list) and history:
                hist_lines = ["[Recent conversation context — use this to "
                              "interpret short replies like 'yes' / 'continue':]"]
                for m in history[-12:]:        # safety cap (6 turns)
                    if not isinstance(m, dict):
                        continue
                    role = m.get("role")
                    content = (m.get("content") or "")[:1200]
                    if not content:
                        continue
                    label = "Operator" if role == "user" else "Jarvis"
                    hist_lines.append(f"{label}: {content.strip()}")
                if len(hist_lines) > 1:
                    prompt_parts.append("\n".join(hist_lines))
            if text_excerpts:
                prompt_parts.append("\n\n".join(text_excerpts))
            if saved and not text_excerpts:
                listing = "\n".join(f"- {r['name']} ({r['size']} bytes, {r['type'] or 'unknown'}) at {r['path']}" for r in saved)
                prompt_parts.append(f"=== {len(saved)} file(s) attached (saved to Inbox) ===\n{listing}\n")
            if text:
                prompt_parts.append(text)
            effective = "\n\n".join(prompt_parts).strip() or "(empty message)"
            direct_response = _try_direct_markets_chat(effective)
            if direct_response is not None:
                _brain_state.update(state="idle")
                turn_text = text + (
                    f"\n\n[+ {len(saved)} file{'s' if len(saved)!=1 else ''}: "
                    + ", ".join(s["name"] for s in saved) + "]" if saved else ""
                )
                try:
                    from openjarvis.tools.obsidian_brain import log_voice_turn
                    log_voice_turn(turn_text, direct_response)
                except Exception:
                    logger.debug("daily journal capture failed", exc_info=True)
                try:
                    _chat_history.append_pair(turn_text, direct_response)
                except Exception:
                    logger.debug("chat history append failed", exc_info=True)
                return self._json_response(200, {
                    "transcript": text,
                    "response": direct_response,
                    "audio_b64": "",
                    "attachments": saved,
                })

            # --- Pipeline ---
            _brain_state.update(state="thinking")
            # Voice ack: "On it, sir." plays immediately while the LLM
            # tool chain runs (5-30s). Without this the operator sees
            # silence and assumes the request was lost.
            try:
                from openjarvis.cli import voice_ack
                voice_ack.emit_thinking()
            except Exception:
                pass
            _raw = _run_with_deadline(
                _ctx.process_command, effective,
                deadline_s=_PROCESS_DEADLINE_S,
            )
            if _raw is None:
                # Deadline elapsed — soft fallback so the operator
                # gets a response instead of staring at a stuck UI.
                try:
                    from openjarvis.cli import voice_ack
                    voice_ack.emit_timeout()
                except Exception:
                    pass
                response_text = (
                    "That's running long, sir — I'll keep working on "
                    "it and write what I find to your vault."
                )
            else:
                response_text = _raw or ""

            # TTS for the response
            audio_b64 = ""
            if (_ctx.tts_backend is not None and response_text
                    and not response_text.startswith("__")):
                try:
                    tts_res = _ctx.tts_backend.synthesize(
                        response_text,
                        voice_id=_ctx.config.digest.voice_id or "fable",
                        speed=_ctx.config.digest.voice_speed,
                        output_format="mp3",
                    )
                    audio_b64 = base64.b64encode(tts_res.audio).decode("ascii")
                except Exception:
                    logger.exception("TTS failed for /chat")
            _brain_state.update(state="idle")

            # Auto-capture into the daily journal — just like /voice_turn
            try:
                from openjarvis.tools.obsidian_brain import log_voice_turn
                turn_text = text + (
                    f"\n\n[+ {len(saved)} file{'s' if len(saved)!=1 else ''}: "
                    + ", ".join(s["name"] for s in saved) + "]" if saved else ""
                )
                log_voice_turn(turn_text, response_text)
            except Exception:
                logger.debug("daily journal capture failed", exc_info=True)

            # Belt-and-braces: also feed the chat bus from here.
            # voice_cmd's _record_fp / LLM-return paths normally fire
            # first, but a silent failure there would leave the panel
            # empty. The HUD's Map-based 5s dedupe collapses both
            # broadcasts into a single rendered bubble per side.
            try:
                _chat_history.append_pair(turn_text, response_text)
            except Exception:
                logger.debug("chat history append failed", exc_info=True)

            self._json_response(200, {
                "transcript":   text,
                "response":     response_text,
                "audio_b64":    audio_b64,
                "attachments":  saved,
            })
        except Exception as exc:
            logger.exception("/chat failed")
            _brain_state.update(state="idle")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})
        finally:
            _voice_turn_lock.release()

    def _handle_text_command(self) -> None:
        """Run a text command through the same pipeline the voice loop uses.

        Body: ``{"text": "turn off all lights"}``. Returns the spoken response
        plus optional TTS audio (same shape as /voice_turn) so the browser
        can display + play it identically.
        """
        if _ctx.process_command is None:
            self._json_response(503, {"error": "Voice pipeline not initialised."})
            return

        # Reuse the voice-turn lock so a menu click doesn't collide with a
        # concurrent mic turn. 30s wait + voice_ack on busy/timeout.
        acquired = _voice_turn_lock.acquire(timeout=_LOCK_ACQUIRE_TIMEOUT_S)
        if not acquired:
            try:
                from openjarvis.cli import voice_ack
                voice_ack.emit_busy()
            except Exception:
                pass
            self._json_response(503, {"error": _BUSY_MSG})
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n) if n > 0 else b"{}"
            data = json.loads(body.decode("utf-8"))
            text = (data.get("text") or "").strip()
            if not text:
                self._json_response(400, {"error": "'text' field is required."})
                return
            direct_response = _try_direct_markets_chat(text)
            if direct_response is not None:
                _brain_state.update(state="idle")
                return self._json_response(200, {
                    "transcript": text,
                    "response": direct_response,
                    "audio_b64": "",
                })

            _brain_state.update(state="thinking")
            try:
                from openjarvis.cli import voice_ack
                voice_ack.emit_thinking()
            except Exception:
                pass
            _raw = _run_with_deadline(
                _ctx.process_command, text,
                deadline_s=_PROCESS_DEADLINE_S,
            )
            if _raw is None:
                try:
                    from openjarvis.cli import voice_ack
                    voice_ack.emit_timeout()
                except Exception:
                    pass
                response_text = (
                    "That's running long, sir — I'll keep working on "
                    "it and write what I find to your vault."
                )
            else:
                response_text = _raw or ""

            audio_b64 = ""
            if _ctx.tts_backend is not None and response_text and not response_text.startswith("__"):
                try:
                    tts_res = _ctx.tts_backend.synthesize(
                        response_text,
                        voice_id=_ctx.config.digest.voice_id or "fable",
                        speed=_ctx.config.digest.voice_speed,
                        output_format="mp3",
                    )
                    audio_b64 = base64.b64encode(tts_res.audio).decode("ascii")
                except Exception:
                    logger.exception("TTS failed for /command")

            _brain_state.update(state="idle")
            self._json_response(200, {
                "transcript": text,
                "response": response_text,
                "audio_b64": audio_b64,
            })
        except Exception as exc:
            logger.exception("/command failed")
            _brain_state.update(state="idle")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})
        finally:
            _voice_turn_lock.release()

    def _handle_agent_task(self) -> None:
        """Queue a new task on a named agent. JSON body: {agent_id, title, prompt?}."""
        from openjarvis.tools import agent_runner
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n) if n > 0 else b"{}"
            data = json.loads(body.decode("utf-8"))
            agent_id = (data.get("agent_id") or "").strip()
            title = (data.get("title") or "").strip()
            prompt = data.get("prompt") or title
            if not agent_id or not title:
                self._json_response(400, {"error": "agent_id and title are required"})
                return
            task_id = agent_runner.add_task(title=title, agent_id=agent_id, prompt=prompt)
            self._json_response(200, {"ok": True, "task_id": task_id})
        except ValueError as exc:
            self._json_response(400, {"error": str(exc)})
        except Exception as exc:
            logger.exception("/agent_task failed")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _handle_agent_cancel(self, task_id: str) -> None:
        from openjarvis.tools import agent_runner
        ok_running = agent_runner.cancel_running_task(task_id)
        ok_todo = agent_runner.cancel_task(task_id)
        self._json_response(200 if (ok_running or ok_todo) else 404,
                            {"ok": ok_running or ok_todo, "task_id": task_id,
                             "killed_proc": ok_running, "cancelled_todo": ok_todo})

    def _handle_wake_all(self) -> None:
        """Queue a brief 'introduce yourself' task on every idle built-in agent."""
        from openjarvis.tools import agent_runner
        try:
            queued = agent_runner.wake_all_idle_agents()
            self._json_response(200, {"ok": True, "queued": queued, "count": len(queued)})
        except Exception as exc:
            logger.exception("/agents/wake_all failed")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _handle_cancel_all(self) -> None:
        """Terminate every running task + cancel all queued todos."""
        from openjarvis.tools import agent_runner
        try:
            n = agent_runner.cancel_all_running()
            self._json_response(200, {"ok": True, "cancelled": n})
        except Exception as exc:
            logger.exception("/agents/cancel_all failed")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _handle_schedule_cancel(self, sched_id: str) -> None:
        from openjarvis.tools import agent_runner
        ok = agent_runner.cancel_scheduled(sched_id)
        self._json_response(200 if ok else 404, {"ok": ok, "id": sched_id})

    def _handle_content_kickoff(self) -> None:
        """Kick off the TikTok content pipeline: research → scripts → drafts."""
        from openjarvis.tools import agent_runner
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n) if n > 0 else b"{}"
            data = json.loads(body.decode("utf-8")) if body.strip() else {}
            topic = (data.get("topic") or "").strip()
            ids = agent_runner.kick_off_content_pipeline(topic_hint=topic or None)
            self._json_response(200, {"ok": True, "tasks": ids})
        except Exception as exc:
            logger.exception("/content/kickoff failed")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _handle_schedule_create(self) -> None:
        """POST /schedule — body: {agent_id, title, run_at, prompt?, recurrence?}.

        run_at must be ISO-8601 (e.g. ``2026-05-02T09:00:00``) or one of the
        relative shortcuts: ``+1h``, ``+1d``, ``+1w`` (case-insensitive).
        """
        from openjarvis.tools import agent_runner
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n) if n > 0 else b"{}"
            data = json.loads(body.decode("utf-8"))
            agent_id = (data.get("agent_id") or "").strip()
            title = (data.get("title") or "").strip()
            run_at = (data.get("run_at") or "").strip()
            prompt = data.get("prompt") or None
            recurrence = (data.get("recurrence") or "once").strip()
            if not agent_id or not title or not run_at:
                return self._json_response(400, {"error": "agent_id, title, run_at required"})
            # Relative shortcuts: +Nh / +Nd / +Nw / +Nm (minutes)
            if run_at.startswith("+"):
                from datetime import datetime, timedelta
                m = re.match(r"\+(\d+)\s*([smhdw])$", run_at, re.I)
                if not m:
                    return self._json_response(400, {"error": "invalid relative run_at"})
                qty, unit = int(m.group(1)), m.group(2).lower()
                delta = {"s": "seconds", "m": "minutes", "h": "hours",
                         "d": "days", "w": "weeks"}[unit]
                run_at = (datetime.now() + timedelta(**{delta: qty})).isoformat(timespec="seconds")
            path = agent_runner.schedule_task(agent_id, title, run_at,
                                              prompt=prompt, recurrence=recurrence)
            if path is None:
                return self._json_response(500, {"error": "could not write scheduled note"})
            self._json_response(200, {"ok": True, "path": str(path), "run_at": run_at})
        except Exception as exc:
            logger.exception("/schedule failed")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    # ----- Browser/PIN auth for the protected endpoints -----

    def _check_pin_auth(self) -> bool:
        """Return True if the request is authenticated to access protected
        endpoints. Bypassed entirely when OPENJARVIS_PUBLIC_PIN is unset."""
        pin = _public_pin()
        if not pin:
            return True  # PIN not configured → open access (dev mode)
        # Cookie?
        cookie = self.headers.get("Cookie") or ""
        for chunk in cookie.split(";"):
            chunk = chunk.strip()
            if chunk.startswith("mc-session="):
                if _is_valid_session(chunk[len("mc-session="):]):
                    return True
                break
        # Bearer token equal to the PIN (for scripted access from your terminal)
        auth = self.headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            if hmac.compare_digest(auth[7:].strip(), pin):
                return True
        return False

    def _require_pin_auth(self) -> bool:
        """Convenience: returns True if the request can proceed; if False,
        a 401 has already been written and the handler should bail."""
        if self._check_pin_auth():
            return True
        # Mild rate-limit on failed attempts (timing-safe)
        time.sleep(0.4)
        self._json_response(401, {"error": "authentication required",
                                  "auth_endpoint": "/auth"})
        return False

    def _request_is_https(self) -> bool:
        """Detect whether the original client connection was HTTPS. Cloudflare
        Tunnel terminates TLS at the edge and forwards plain HTTP to us, but
        sets ``X-Forwarded-Proto: https`` and ``Cf-Visitor: {"scheme":"https"}``
        so we can recover the original scheme."""
        xfp = (self.headers.get("X-Forwarded-Proto") or "").lower()
        if xfp == "https":
            return True
        cfv = (self.headers.get("Cf-Visitor") or "").lower()
        return '"https"' in cfv

    def _handle_auth_login(self) -> None:
        """POST /auth — body: {"pin": "..."} → sets mc-session cookie."""
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n) if n > 0 else b"{}"
            data = json.loads(body.decode("utf-8"))
        except Exception:
            return self._json_response(400, {"ok": False, "error": "bad request"})
        submitted = (data.get("pin") or "").strip()
        pin = _public_pin()
        if not pin:
            return self._json_response(503, {"ok": False,
                                              "error": "PIN not configured on server"})

        # Per-IP brute-force gate (audit 2026-04-26 C3). Use the
        # Cf-Connecting-Ip header when present (Cloudflare tunnel
        # forwards this), otherwise fall back to the TCP peer address.
        # That way a brute-forcer who keeps hitting the public tunnel
        # gets locked out at their real source IP, not at cloudflared's.
        peer_ip = (self.headers.get("Cf-Connecting-Ip")
                   or self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                   or (self.client_address[0] if self.client_address else "unknown"))
        now = time.time()
        with _PIN_FAILS_LOCK:
            count, win_start, locked_until = _PIN_FAILS.get(peer_ip, (0, now, 0.0))
            if locked_until > now:
                # Already locked — return 429 + retry-after
                retry = int(locked_until - now)
                logger.warning("PIN brute-force lockout active for %s (%ds remain)",
                               peer_ip, retry)
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Retry-After", str(retry))
                body_bytes = json.dumps({
                    "ok": False, "error": "too many failed attempts",
                    "retry_after_s": retry,
                }).encode("utf-8")
                self.send_header("Content-Length", str(len(body_bytes)))
                self.end_headers()
                try: self.wfile.write(body_bytes)
                except Exception: pass
                return
            # Slide the window
            if now - win_start > _PIN_LOCK_WINDOW_S:
                count = 0
                win_start = now

        # Backoff delay grows with failure count (capped). Even
        # one failure costs a base delay so the legitimate user
        # doesn't notice but a parallel attacker still pays.
        delay = min(_PIN_FAIL_DELAY_BASE_S * (count + 1), _PIN_FAIL_DELAY_MAX_S)
        time.sleep(delay)

        if not hmac.compare_digest(submitted, pin):
            with _PIN_FAILS_LOCK:
                count, win_start, _ = _PIN_FAILS.get(peer_ip, (0, now, 0.0))
                if now - win_start > _PIN_LOCK_WINDOW_S:
                    count = 0
                    win_start = now
                count += 1
                if count >= _PIN_LOCK_THRESHOLD:
                    locked_until = now + _PIN_LOCK_DURATION_S
                    logger.warning("PIN brute-force lockout for %s "
                                   "(%d failures in %ds, locked %dm)",
                                   peer_ip, count, _PIN_LOCK_WINDOW_S,
                                   _PIN_LOCK_DURATION_S // 60)
                    _PIN_FAILS[peer_ip] = (count, win_start, locked_until)
                else:
                    _PIN_FAILS[peer_ip] = (count, win_start, 0.0)
                    logger.info("PIN failure for %s (%d/%d in window)",
                                peer_ip, count, _PIN_LOCK_THRESHOLD)
            return self._json_response(401, {"ok": False, "error": "wrong PIN"})

        # Success — clear any failure history for this IP
        with _PIN_FAILS_LOCK:
            _PIN_FAILS.pop(peer_ip, None)
        token = _make_session_token()
        # Build response with Set-Cookie. Only flag as ``Secure`` when the
        # client actually came over HTTPS — otherwise the browser silently
        # drops the cookie on plain-HTTP LAN access (auto-opened local URL).
        secure_flag = "Secure; " if self._request_is_https() else ""
        body_bytes = json.dumps({"ok": True, "ttl_days": _SESSION_TTL // 86400}).encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Set-Cookie",
                f"mc-session={token}; Path=/; Max-Age={_SESSION_TTL}; "
                f"{secure_flag}HttpOnly; SameSite=Lax")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.end_headers()
            self.wfile.write(body_bytes)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _handle_auth_check(self) -> None:
        """GET /auth/check — returns 200 if authenticated, 401 otherwise.
        Used by the browser to decide whether to show the login modal."""
        if not _public_pin():
            return self._json_response(200, {"ok": True, "auth_required": False})
        if self._check_pin_auth():
            return self._json_response(200, {"ok": True, "auth_required": True,
                                              "authenticated": True})
        return self._json_response(401, {"ok": False, "auth_required": True,
                                          "authenticated": False})

    def _handle_auth_logout(self) -> None:
        """POST /auth/logout — invalidate the current session token."""
        cookie = self.headers.get("Cookie") or ""
        for chunk in cookie.split(";"):
            chunk = chunk.strip()
            if chunk.startswith("mc-session="):
                token = chunk[len("mc-session="):]
                with _PIN_SESSIONS_LOCK:
                    _PIN_SESSIONS.pop(token, None)
                break
        secure_flag = "Secure; " if self._request_is_https() else ""
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            # Expire the cookie immediately — same Secure-conditional logic
            self.send_header("Set-Cookie",
                f"mc-session=; Path=/; Max-Age=0; {secure_flag}HttpOnly; SameSite=Lax")
            body = b'{"ok":true}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _check_vault_auth(self) -> bool:
        """Bearer-token check for the /vault/* endpoints exposed to ChatGPT.

        Hardening (audit 2026-04-26):
          * Requires ``OPENJARVIS_VAULT_TOKEN`` to be configured. If the
            env var is unset OR empty, ALL /vault/* requests are denied
            (previously the endpoints defaulted to wide-open which is a
            CRITICAL bypass over the public Cloudflare tunnel).
          * Token comparison via ``hmac.compare_digest`` to defeat the
            byte-by-byte timing oracle that ``==`` enables.
          * Empty-bearer attack (``Authorization: Bearer ``) explicitly
            rejected — previously matched empty == empty when the env
            var was unset.
        """
        expected = os.environ.get("OPENJARVIS_VAULT_TOKEN", "").strip()
        if not expected:
            return False                # configured-or-deny
        auth = self.headers.get("Authorization") or ""
        if not auth.startswith("Bearer "):
            return False
        supplied = auth[7:].strip()
        if not supplied:
            return False                # empty token always denied
        return hmac.compare_digest(supplied, expected)

    def _handle_vault_remember(self) -> None:
        """POST /vault/remember — body: {content, title?, folder?, tags?}.
        Creates a note in the Obsidian vault. Mirrors the voice "remember…"
        path so anything ChatGPT writes appears on Mission Control too.
        """
        if not self._check_vault_auth():
            return self._json_response(401, {"error": "missing or invalid bearer token"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n) if n > 0 else b"{}"
            data = json.loads(body.decode("utf-8"))
            content = (data.get("content") or "").strip()
            if not content:
                return self._json_response(400, {"error": "'content' is required"})
            title = (data.get("title") or "").strip() or None
            folder = (data.get("folder") or "Knowledge").strip()
            tags = data.get("tags") or []
            if not isinstance(tags, list):
                tags = [str(tags)]
            from openjarvis.tools import obsidian_brain
            with obsidian_brain.source_context("chatgpt"):
                path = obsidian_brain.remember(content, title=title, folder=folder, tags=tags)
            if path is None:
                return self._json_response(500, {"error": "vault not available"})
            self._json_response(200, {
                "ok": True,
                "path": str(path),
                "name": path.stem,
            })
        except Exception as exc:
            logger.exception("/vault/remember failed")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _handle_vault_recall(self) -> None:
        """GET /vault/recall?q=... — keyword search across the vault."""
        if not self._check_vault_auth():
            return self._json_response(401, {"error": "missing or invalid bearer token"})
        try:
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            query = (qs.get("q") or [""])[0].strip()
            limit = int((qs.get("limit") or ["5"])[0])
            limit = max(1, min(20, limit))
            if not query:
                return self._json_response(400, {"error": "'q' query parameter required"})
            from openjarvis.tools import obsidian_brain
            with obsidian_brain.source_context("chatgpt"):
                hits = obsidian_brain.recall(query, limit=limit)
            results = [{"name": p.stem, "path": str(p), "snippet": s} for p, s in hits]
            self._json_response(200, {"query": query, "count": len(results), "results": results})
        except Exception as exc:
            logger.exception("/vault/recall failed")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _handle_vault_get(self) -> None:
        """GET /vault/get?name=<note-stem>  -- read a specific note."""
        if not self._check_vault_auth():
            return self._json_response(401, {"error": "missing or invalid bearer token"})
        try:
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            name = (qs.get("name") or [""])[0].strip()
            if not name:
                return self._json_response(400, {"error": "'name' parameter required"})
            # Hardening (audit 2026-04-26 C2): reject anything that looks
            # like a path component, restrict the search to BRAIN_ROOT
            # (was DEFAULT_VAULT, exposing notes outside Brain/), and
            # verify the resolved hit stays inside BRAIN_ROOT.
            if any(c in name for c in ("/", "\\", "..")) or "\x00" in name:
                return self._json_response(400, {"error": "invalid name"})
            if len(name) > 200:
                return self._json_response(400, {"error": "name too long"})
            from openjarvis.tools.obsidian_brain import BRAIN_ROOT
            from pathlib import Path as _P
            brain = _P(BRAIN_ROOT).resolve()
            target = None
            for md in brain.rglob("*.md"):
                if any(part.startswith(".") for part in md.parts):
                    continue
                if md.stem == name:
                    # Final containment check against resolved path
                    try:
                        if not md.resolve().is_relative_to(brain):
                            continue
                    except (ValueError, OSError):
                        continue
                    target = md
                    break
            if target is None:
                return self._json_response(404, {"error": f"note '{name}' not found"})
            content = target.read_text(encoding="utf-8", errors="replace")
            # Tag this read so the third-brain visualization animates correctly
            from openjarvis.tools import obsidian_brain
            obsidian_brain._emit_event("read", f"get: {name}", kind="get", source="chatgpt")
            self._json_response(200, {
                "name": target.stem,
                "path": str(target),
                "content": content,
                "size": len(content),
            })
        except Exception as exc:
            logger.exception("/vault/get failed")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _handle_vault_list(self) -> None:
        """GET /vault/list?folder=Knowledge&limit=20  -- list notes in a folder."""
        if not self._check_vault_auth():
            return self._json_response(401, {"error": "missing or invalid bearer token"})
        try:
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            folder = (qs.get("folder") or [""])[0].strip()
            limit = int((qs.get("limit") or ["20"])[0])
            limit = max(1, min(100, limit))
            # Hardening (audit 2026-04-26 M2): reject path-traversal
            # components in folder; verify resolved base stays inside
            # BRAIN_ROOT before enumerating.
            if any(c in folder for c in ("/", "\\", "..")) or "\x00" in folder:
                return self._json_response(400, {"error": "invalid folder"})
            from openjarvis.tools.obsidian_brain import DEFAULT_VAULT, BRAIN_ROOT
            base = (BRAIN_ROOT / folder) if folder else BRAIN_ROOT
            if not base.exists():
                return self._json_response(404, {"error": f"folder '{folder}' not found"})
            try:
                if not base.resolve().is_relative_to(BRAIN_ROOT.resolve()):
                    return self._json_response(400, {"error": "folder outside vault"})
            except (ValueError, OSError):
                return self._json_response(400, {"error": "folder outside vault"})
            items = []
            for md in sorted(base.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
                if any(part.startswith(".") for part in md.parts):
                    continue
                items.append({
                    "name": md.stem,
                    "path": str(md),
                    "modified": md.stat().st_mtime,
                })
                if len(items) >= limit:
                    break
            from openjarvis.tools import obsidian_brain
            obsidian_brain._emit_event("read", f"list: {folder or 'Brain'}", kind="list", source="chatgpt")
            self._json_response(200, {"folder": folder or "Brain", "count": len(items), "notes": items})
        except Exception as exc:
            logger.exception("/vault/list failed")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _handle_vault_journal(self) -> None:
        """GET /vault/journal?date=YYYY-MM-DD  -- get a daily journal (today by default)."""
        if not self._check_vault_auth():
            return self._json_response(401, {"error": "missing or invalid bearer token"})
        try:
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            date = (qs.get("date") or [""])[0].strip()
            from openjarvis.tools.obsidian_brain import DAILY_DIR, read_today_journal
            from openjarvis.tools import obsidian_brain
            from datetime import datetime
            if not date:
                with obsidian_brain.source_context("chatgpt"):
                    content = read_today_journal()
                date = datetime.now().strftime("%Y-%m-%d")
            else:
                # Hardening (audit 2026-04-26 M3): enforce ISO date
                # format. Previously '?date=../../etc/passwd' was treated
                # as a literal filename; with the .md suffix that won't
                # match the real /etc/passwd but DOES enable reads of
                # any .md anywhere on disk via path traversal.
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                    return self._json_response(400, {"error": "invalid date format (expect YYYY-MM-DD)"})
                p = DAILY_DIR / f"{date}.md"
                # Final containment check
                try:
                    if not p.resolve().is_relative_to(DAILY_DIR.resolve()):
                        return self._json_response(400, {"error": "date outside vault"})
                except (ValueError, OSError):
                    return self._json_response(400, {"error": "date outside vault"})
                content = p.read_text(encoding="utf-8") if p.exists() else None
                if content is not None:
                    obsidian_brain._emit_event("read", f"journal: {date}", kind="daily", source="chatgpt")
            if content is None:
                return self._json_response(404, {"error": f"no journal for {date}"})
            self._json_response(200, {"date": date, "content": content})
        except Exception as exc:
            logger.exception("/vault/journal failed")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _handle_claude_event(self) -> None:
        """Ingest a Claude Code hook payload.

        Called by a small wrapper script registered as a hook in
        ~/.claude/settings.json. Each Claude Code session becomes a live
        card in mission control for the duration of its activity.

        Hardening (audit 2026-04-26 C4): the hook scripts run on the SAME
        host as Jarvis, so this endpoint never legitimately needs to
        accept connections from anywhere except 127.0.0.1. Restricting
        to loopback closes a major unauth attack surface — the audit
        showed this open route enabled vault poisoning + eventual RCE
        (an attacker could POST a crafted hook event whose subagent
        description re-armed dispatch_agent on the operator's next
        recall_vault hit).

        Tolerant of encoding issues: tries UTF-8 first, falls back to
        CP1252 / latin-1. PowerShell 5.1 in particular can send bodies
        in the console code page if the hook wrapper isn't careful.
        """
        # Loopback gate — accept only same-host connections. cloudflared
        # connects to 127.0.0.1 itself, but TUNNEL traffic arrives with
        # the Cloudflare edge in the X-Forwarded-For chain, so we reject
        # anything where the original client wasn't local.
        peer_ip = self.client_address[0] if self.client_address else ""
        if peer_ip not in ("127.0.0.1", "::1", "localhost"):
            return self._json_response(403, {"error": "claude_event accepts loopback connections only"})
        # Also refuse if any X-Forwarded-For chain is present — that
        # means cloudflared proxied us, i.e. the request came in via
        # the public tunnel.
        if self.headers.get("X-Forwarded-For") or self.headers.get("Cf-Connecting-Ip"):
            return self._json_response(403, {"error": "claude_event refuses tunnel-proxied requests"})

        from openjarvis.tools import agent_runner
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > 1_000_000:
                self._json_response(413, {"error": "payload too large"})
                return
            body = self.rfile.read(n) if n > 0 else b"{}"

            # Decode robustly — try the declared content-type charset first,
            # then UTF-8 with replacement so we never 500 on a bad byte.
            ctype = (self.headers.get("Content-Type") or "").lower()
            charset = "utf-8"
            if "charset=" in ctype:
                charset = ctype.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
            text = None
            for enc in (charset, "utf-8", "cp1252", "latin-1"):
                try:
                    text = body.decode(enc)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            if text is None:
                text = body.decode("utf-8", errors="replace")

            event = json.loads(text)
            agent_runner.record_claude_event(event)
            self._json_response(200, {"ok": True})
        except Exception as exc:
            logger.exception("/claude_event failed")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _handle_voice_turn(self) -> None:
        """Accept an audio upload, run the full Jarvis pipeline, return JSON."""
        if _ctx.stt_backend is None or _ctx.process_command is None:
            self._json_response(503, {"error": "Voice pipeline not initialised."})
            return

        # Only one voice turn at a time. 30s wait (was 5s) — paired
        # with voice_ack.emit_busy so the operator hears that the brain
        # is on a previous turn rather than getting a silent 503.
        acquired = _voice_turn_lock.acquire(timeout=_LOCK_ACQUIRE_TIMEOUT_S)
        if not acquired:
            logger.warning("/voice_turn rejected: lock held >30s")
            try:
                from openjarvis.cli import voice_ack
                voice_ack.emit_busy()
            except Exception:
                pass
            self._json_response(503, {"error": _BUSY_MSG})
            return
        try:
            self._handle_voice_turn_locked()
        finally:
            _voice_turn_lock.release()

    def _handle_voice_turn_locked(self) -> None:
        """Body of /voice_turn — runs under `_voice_turn_lock`."""
        # Read audio body
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0 or content_length > 10_000_000:  # 10 MB cap
            self._json_response(400, {"error": "Invalid audio size."})
            return

        audio_bytes = self.rfile.read(content_length)

        # Determine format from Content-Type header (browser sends audio/webm etc.)
        content_type = self.headers.get("Content-Type", "audio/webm").lower()
        if "webm" in content_type:
            fmt = "webm"
        elif "ogg" in content_type:
            fmt = "ogg"
        elif "mp4" in content_type or "m4a" in content_type:
            fmt = "m4a"
        elif "wav" in content_type:
            fmt = "wav"
        else:
            fmt = "webm"  # sensible default for most browsers

        try:
            # --- 1. STT ---
            _brain_state.update(state="thinking")
            result = _ctx.stt_backend.transcribe(
                audio_bytes, format=fmt, language="en"
            )
            transcript = (result.text or "").strip()

            if not transcript:
                _brain_state.update(state="idle")
                self._json_response(200, {
                    "transcript": "",
                    "response": "I didn't catch that, sir.",
                    "audio_b64": "",
                })
                return

            # --- 2. Process command (no wake word needed — user pressed the button) ---
            direct_response = _try_direct_markets_chat(transcript)
            if direct_response is not None:
                _brain_state.update(state="idle")
                try:
                    from openjarvis.tools.obsidian_brain import log_voice_turn
                    log_voice_turn(transcript, direct_response)
                except Exception:
                    logger.debug("daily journal capture failed", exc_info=True)
                try:
                    _chat_history.append_pair(transcript, direct_response)
                except Exception:
                    logger.debug("chat history append failed", exc_info=True)
                return self._json_response(200, {
                    "transcript": transcript,
                    "response": direct_response,
                    "audio_b64": "",
                })

            # Voice ack: "On it, sir." plays immediately. Without this,
            # the operator stares at 5-30s of silence while the LLM
            # tool chain runs and assumes nothing happened.
            try:
                from openjarvis.cli import voice_ack
                voice_ack.emit_thinking()
            except Exception:
                pass
            _raw = _run_with_deadline(
                _ctx.process_command, transcript,
                deadline_s=_PROCESS_DEADLINE_S,
            )
            if _raw is None:
                try:
                    from openjarvis.cli import voice_ack
                    voice_ack.emit_timeout()
                except Exception:
                    pass
                response_text = (
                    "That's running long, sir — I'll keep working on "
                    "it and write what I find to your vault."
                )
            else:
                response_text = _raw or ""

            # --- 3. TTS ---
            audio_b64 = ""
            if _ctx.tts_backend is not None and response_text:
                try:
                    tts_res = _ctx.tts_backend.synthesize(
                        response_text,
                        voice_id=_ctx.config.digest.voice_id or "fable",
                        speed=_ctx.config.digest.voice_speed,
                        output_format="mp3",
                    )
                    audio_b64 = base64.b64encode(tts_res.audio).decode("ascii")
                except Exception:
                    logger.exception("TTS failed")

            _brain_state.update(state="idle")

            # Auto-capture into the Obsidian daily journal so the brain
            # fills passively as you talk to Jarvis. Non-fatal if the
            # vault isn't reachable.
            try:
                from openjarvis.tools.obsidian_brain import log_voice_turn
                log_voice_turn(transcript, response_text)
            except Exception:
                logger.debug("daily journal capture failed", exc_info=True)

            # Belt-and-braces append (HUD dedupe collapses to one bubble).
            try:
                _chat_history.append_pair(transcript, response_text)
            except Exception:
                logger.debug("chat history append failed", exc_info=True)

            self._json_response(200, {
                "transcript": transcript,
                "response": response_text,
                "audio_b64": audio_b64,
            })

        except Exception as exc:
            logger.exception("/voice_turn failed")
            _brain_state.update(state="idle")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _json_response(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError) as exc:
            # Client (typically a browser) disconnected before we finished
            # writing — normal during page reloads / SSE reconnects / tab
            # closes. Log at debug, never as an exception with traceback.
            logger.debug("client disconnect during response: %s", exc)

    def do_OPTIONS(self) -> None:  # CORS preflight
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _handle_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        # Send initial state
        initial = json.dumps({"state": _brain_state.state, "energy": _brain_state.energy})
        self.wfile.write(f"data: {initial}\n\n".encode())
        self.wfile.flush()

        _brain_state.subscribe(self.wfile)
        try:
            # Keep the connection alive until the client disconnects
            while True:
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            _brain_state.unsubscribe(self.wfile)

    def _handle_vault_sse(self) -> None:
        """SSE stream of Obsidian read/write events for the second-brain viz."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        # Replay the recent ring buffer so a refreshed page sees the last
        # few events even if it joined mid-quiet-period.
        try:
            from openjarvis.tools.obsidian_brain import recent_events
            for ev in recent_events()[-8:]:
                self.wfile.write(("data: " + json.dumps(ev) + "\n\n").encode("utf-8"))
            self.wfile.flush()
        except Exception:
            pass

        _vault_bus.subscribe(self.wfile)
        try:
            while True:
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            _vault_bus.unsubscribe(self.wfile)

    # _handle_unifi_sse removed (UniFi bridge no longer active)

    def _handle_agent_stats_get(self) -> None:
        """GET /agent_stats[?id=<agent_id>][&days=N] — return outcome-
        aggregated stats from Phase L-1 outcomes module.
        Without ?id= → returns {agent_id: stats} for every agent that
        produced an outcome in the window. With ?id= → just that agent.
        Default window is 30 days, capped at 365."""
        try:
            from openjarvis.tools import outcomes
        except Exception:
            return self._json_response(503, {"error": "outcomes not initialised"})
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        try:
            days = max(1, min(365, int((q.get("days") or ["30"])[0])))
        except (ValueError, TypeError):
            days = 30
        agent_id = (q.get("id") or [None])[0]
        try:
            if agent_id:
                self._json_response(200, {
                    "stats": outcomes.agent_stats(agent_id, window_days=days),
                    "window_days": days,
                })
            else:
                self._json_response(200, {
                    "by_agent": outcomes.all_agent_stats(window_days=days),
                    "window_days": days,
                })
        except Exception:
            logger.exception("/agent_stats failed")
            self._json_response(500, {"error": "internal error", "ref": _err_ref()})

    def _handle_briefing_get(self) -> None:
        """GET /briefing[?date=YYYY-MM-DD] — return one AI-pulse briefing
        from Brain/Knowledge/ plus a list of all available dates so the
        HUD widget can offer a dropdown of past briefings.

        File pattern: '<YYYY-MM-DD> - AI pulse.md' produced daily 06:00 by
        the ai-researcher agent. If date= isn't supplied (or no exact
        match), falls back to most-recent available."""
        try:
            from openjarvis.tools.obsidian_brain import KNOWLEDGE_DIR
        except Exception:
            return self._json_response(503, {"error": "vault not initialised"})
        if not KNOWLEDGE_DIR.exists():
            return self._json_response(200, {
                "available": [], "date": None, "content": "",
                "note": "Knowledge dir not yet created.",
            })

        # Discover available pulses. Pattern is "<YYYY-MM-DD> - AI pulse.md".
        # Sort newest-first by the date in the filename.
        import re
        pulse_re = re.compile(r"^(\d{4}-\d{2}-\d{2}) - AI pulse\.md$", re.IGNORECASE)
        available: List[Dict[str, Any]] = []
        for p in KNOWLEDGE_DIR.iterdir():
            if not p.is_file():
                continue
            m = pulse_re.match(p.name)
            if not m:
                continue
            available.append({"date": m.group(1), "filename": p.name})
        available.sort(key=lambda x: x["date"], reverse=True)

        # Pick which one to return
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        wanted = (q.get("date") or [None])[0]
        chosen = None
        if wanted:
            chosen = next((a for a in available if a["date"] == wanted), None)
        if chosen is None and available:
            chosen = available[0]

        content = ""
        if chosen:
            try:
                # Defence-in-depth: never read outside KNOWLEDGE_DIR even if
                # filenames-from-disk look fine. Construct the path explicitly
                # from the validated date string + suffix.
                target = KNOWLEDGE_DIR / f"{chosen['date']} - AI pulse.md"
                if target.is_file() and target.resolve().is_relative_to(KNOWLEDGE_DIR.resolve()):
                    content = target.read_text(encoding="utf-8")
            except Exception:
                logger.exception("briefing read failed for %s", chosen)

        return self._json_response(200, {
            "available": available,
            "date": chosen["date"] if chosen else None,
            "content": content,
        })

    def _handle_digest_get(self) -> None:
        """GET /digest[?date=YYYY-MM-DD] — return one Jarvis learning digest
        from Brain/Knowledge/ plus a list of all available dates so the HUD
        widget can offer a dropdown of past digests.

        File pattern: '<YYYY-MM-DD> - Jarvis learning digest.md' produced
        daily by the learning-reviewer agent. If date= isn't supplied (or
        no exact match), falls back to most-recent available."""
        try:
            from openjarvis.tools.obsidian_brain import KNOWLEDGE_DIR
        except Exception:
            return self._json_response(503, {"error": "vault not initialised"})
        if not KNOWLEDGE_DIR.exists():
            return self._json_response(200, {
                "available": [], "date": None, "content": "",
                "note": "Knowledge dir not yet created.",
            })

        # Discover available digests. Pattern is
        # "<YYYY-MM-DD> - Jarvis learning digest.md".
        # Sort newest-first by the date in the filename.
        import re
        digest_re = re.compile(
            r"^(\d{4}-\d{2}-\d{2}) - Jarvis learning digest\.md$",
            re.IGNORECASE,
        )
        available: List[Dict[str, Any]] = []
        for p in KNOWLEDGE_DIR.iterdir():
            if not p.is_file():
                continue
            m = digest_re.match(p.name)
            if not m:
                continue
            available.append({"date": m.group(1), "filename": p.name})
        available.sort(key=lambda x: x["date"], reverse=True)

        # Pick which one to return
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        wanted = (q.get("date") or [None])[0]
        chosen = None
        if wanted:
            chosen = next((a for a in available if a["date"] == wanted), None)
        if chosen is None and available:
            chosen = available[0]

        content = ""
        if chosen:
            try:
                # Defence-in-depth: never read outside KNOWLEDGE_DIR even if
                # filenames-from-disk look fine. Construct the path explicitly
                # from the validated date string + suffix.
                target = KNOWLEDGE_DIR / f"{chosen['date']} - Jarvis learning digest.md"
                if target.is_file() and target.resolve().is_relative_to(KNOWLEDGE_DIR.resolve()):
                    content = target.read_text(encoding="utf-8")
            except Exception:
                logger.exception("digest read failed for %s", chosen)

        return self._json_response(200, {
            "available": available,
            "date": chosen["date"] if chosen else None,
            "content": content,
        })

    def _handle_markets_get(self) -> None:
        """Markets subsystem read endpoints. Day-1 surface:

            GET /markets/watchlist   → {items:[...], count, paper_portfolio:{...}}
            GET /markets/today       → {date, briefing_md, generated_at, status}
                                       — Day-1 returns placeholder; LLM
                                       pipeline ships next session.
            GET /markets/health      → store + sources health snapshot

        All endpoints sit behind the same PIN auth as other GETs (handled
        upstream in _dispatch_get). Failure modes return 200 + an
        ``error`` field so the HUD doesn't see a hard 500.
        """
        from urllib.parse import urlparse
        path_only = urlparse(self.path).path
        sub = path_only[len("/markets/"):]
        try:
            from openjarvis.markets import store
        except Exception as exc:
            return self._json_response(200, {
                "ok": False,
                "error": "markets subsystem not initialised: %s" % exc,
            })
        try:
            if sub == "watchlist":
                items = store.watchlist_get()
                portfolio = store.paper_portfolio_get()
                return self._json_response(200, {
                    "ok": True,
                    "items": items,
                    "count": len(items),
                    "paper_portfolio": portfolio,
                })
            if sub == "today":
                # Placeholder until the LLM briefing pipeline ships.
                from datetime import date as _date
                return self._json_response(200, {
                    "ok": True,
                    "date": _date.today().isoformat(),
                    "status": "pending",
                    "briefing_md": (
                        "# Markets Briefing — pending\n\n"
                        "*The LLM briefing pipeline ships in the next "
                        "session. Day-1 build provides the data layer + "
                        "watchlist + tools only.*"
                    ),
                    "generated_at": None,
                })
            if sub == "health":
                from openjarvis.markets.sources import yf, kraken, coingecko
                return self._json_response(200, {
                    "ok": True,
                    "store": store.health(),
                    "sources": {
                        "yfinance": yf.is_available(),
                        "kraken": kraken.is_available(),
                        "coingecko": coingecko.is_available(),
                    },
                })
            if sub == "backfill_status":
                from openjarvis.markets import backfill
                return self._json_response(200, backfill.get_status())
            if sub == "today":
                # Override of the placeholder: serve the actual briefing
                # markdown if today's file exists in Brain/Trading/Research/.
                from datetime import date as _date
                today = _date.today().isoformat()
                try:
                    from openjarvis.tools.obsidian_brain import BRAIN_ROOT
                    p = (BRAIN_ROOT / "Trading" / "Research"
                         / f"{today} - market-research.md")
                    if p.is_file():
                        return self._json_response(200, {
                            "ok": True, "date": today,
                            "status": "ready",
                            "briefing_md": p.read_text(encoding="utf-8"),
                            "generated_at": p.stat().st_mtime,
                        })
                except Exception:
                    logger.debug("today briefing read failed", exc_info=True)
                return self._json_response(200, {
                    "ok": True, "date": today, "status": "pending",
                    "briefing_md": (
                        "# Markets Briefing — pending\n\n"
                        "*No briefing for today yet. Run backfill first if "
                        "this is your first launch (POST /markets/backfill), "
                        "then POST /markets/regenerate — or say "
                        "\"run today's briefing\".*"
                    ),
                    "generated_at": None,
                })
        except Exception:
            logger.exception("/markets/%s failed", sub)
            return self._json_response(500, {
                "error": "internal error", "ref": _err_ref(),
            })
        return self._json_response(404, {"error": "unknown markets endpoint"})

    def _handle_markets_post(self) -> None:
        """Markets subsystem write endpoints:

            POST /markets/backfill   → kicks off background 90-day backfill
                                       across the curated equity universe +
                                       top-100 crypto. Returns immediately
                                       with status; poll /markets/backfill_status.
            POST /markets/regenerate → runs the briefing pipeline now,
                                       blocks until complete (~30-60s),
                                       returns the briefing summary + path.
        """
        from urllib.parse import urlparse
        path_only = urlparse(self.path).path
        sub = path_only[len("/markets/"):]
        try:
            if sub == "backfill":
                from openjarvis.markets import backfill
                # Read body for optional overrides
                n = int(self.headers.get("Content-Length", 0))
                body_bytes = self.rfile.read(n) if n > 0 else b"{}"
                try:
                    body = json.loads(body_bytes.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    body = {}
                kwargs = {}
                if isinstance(body.get("max_crypto"), int):
                    kwargs["max_crypto"] = max(0, min(1000, body["max_crypto"]))
                if "include_equities" in body:
                    kwargs["include_equities"] = bool(body["include_equities"])
                if "include_crypto" in body:
                    kwargs["include_crypto"] = bool(body["include_crypto"])
                status = backfill.start_background(**kwargs)
                return self._json_response(200, {"ok": True, "status": status})
            if sub == "regenerate":
                from openjarvis.markets import financial_researcher
                result = financial_researcher.run()
                return self._json_response(200, {"ok": result.get("ok", False),
                                                 "result": result})
        except Exception:
            logger.exception("POST /markets/%s failed", sub)
            return self._json_response(500, {
                "error": "internal error", "ref": _err_ref(),
            })
        return self._json_response(404, {"error": "unknown markets endpoint"})

    # ------------------------------------------------------------------
    # Markets-Pro page (jarvis_web/markets.html) — separate dedicated
    # full-page UI for crypto chart analysis. Mirrors natum.app's three-
    # tab layout (Dashboard / Pulse / Analyze) but reuses our chart_
    # analyst pipeline + free CoinGecko/Kraken data + L-1 outcomes.
    # ------------------------------------------------------------------

    def _handle_markets_pro_get(self) -> None:
        """GET /markets-pro/{dashboard|pulse}."""
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        sub = parsed.path[len("/markets-pro/"):]
        qs = parse_qs(parsed.query or "")
        try:
            if sub == "dashboard":
                return self._json_response(200, _markets_pro_dashboard())
            if sub == "pulse":
                hide = (qs.get("hide_high_risk", ["0"])[0] or "0").lower()
                hide_flag = hide in ("1", "true", "yes", "on")
                return self._json_response(
                    200, _markets_pro_pulse(hide_high_risk=hide_flag),
                )
            if sub == "global":
                from openjarvis.markets.sources import coingecko
                return self._json_response(200, coingecko.fetch_global())
            if sub == "coins":
                return self._json_response(200, _markets_pro_coins_page(qs))
            if sub == "coins/categories":
                return self._json_response(200, _markets_pro_coin_categories())
            if sub == "paper/portfolio":
                return self._json_response(200, _markets_pro_paper_portfolio())
            if sub == "bot/schedules":
                return self._json_response(200, _markets_pro_paper_bot_list())
        except Exception:
            logger.exception("GET /markets-pro/%s failed", sub)
            return self._json_response(500, {
                "error": "internal error", "ref": _err_ref(),
            })
        return self._json_response(404, {"error": "unknown markets-pro endpoint"})

    def _handle_markets_pro_post(self) -> None:
        """POST /markets-pro/analyze — body:
            {image_b64, image_name?, ticker_hint?, timeframe?}
        Decodes the image, persists to Brain/Inbox/, runs chart_analyst,
        reads the rendered PNG, returns inline base64 + analysis."""
        from urllib.parse import urlparse
        path_only = urlparse(self.path).path
        sub = path_only[len("/markets-pro/"):]
        try:
            if sub not in (
                "analyze", "paper/buy", "paper/sell", "bot/backtest",
                "bot/schedule", "bot/cancel", "bot/run-due", "bot/approve-execution",
            ):
                return self._json_response(404, {
                    "error": "unknown markets-pro endpoint"})
            n = int(self.headers.get("Content-Length", 0))
            max_body = 25_000_000 if sub == "analyze" else 100_000
            if n <= 0 or n > max_body:
                return self._json_response(400, {"error": "request body required"})
            body_bytes = self.rfile.read(n)
            try:
                body = json.loads(body_bytes.decode("utf-8"))
            except json.JSONDecodeError:
                return self._json_response(400, {"error": "bad JSON"})
            if sub == "paper/buy":
                return self._json_response(200, _markets_pro_paper_buy(body))
            if sub == "paper/sell":
                return self._json_response(200, _markets_pro_paper_sell(body))
            if sub == "bot/backtest":
                return self._json_response(200, _markets_pro_bot_backtest(body))
            if sub == "bot/schedule":
                return self._json_response(200, _markets_pro_paper_bot_schedule(body))
            if sub == "bot/cancel":
                return self._json_response(200, _markets_pro_paper_bot_cancel(body))
            if sub == "bot/run-due":
                return self._json_response(200, _markets_pro_paper_bot_run_due(body))
            if sub == "bot/approve-execution":
                return self._json_response(200, _markets_pro_paper_bot_approve_execution(body))
            return self._json_response(200, _markets_pro_analyze(body))
        except Exception:
            logger.exception("POST /markets-pro/%s failed", sub)
            return self._json_response(500, {
                "error": "internal error", "ref": _err_ref(),
            })

    def _handle_tiktok_get(self) -> None:
        """TikTok subsystem read endpoints."""
        from openjarvis.tiktok.pipeline import get_pipeline_state
        import json as _json

        path_only = self.path.split("?")[0].rstrip("/")

        if path_only == "/tiktok":
            html_path = _jarvis_web_path("tiktok.html")
            if html_path.exists():
                self.path = "/tiktok.html"
                return super().do_GET()
            else:
                self._json_response(404, {"error": "TikTok dashboard not yet built"})
                return

        elif path_only == "/tiktok/state":
            self._json_response(200, get_pipeline_state())

        elif path_only == "/tiktok/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            state = get_pipeline_state()
            self.wfile.write(f"data: {_json.dumps(state)}\n\n".encode())
            self.wfile.flush()

        elif path_only == "/tiktok/oauth/callback":
            from openjarvis.tiktok.state import get_setting, set_setting
            from openjarvis.tiktok.tiktok_client import exchange_code, TikTokError
            import urllib.parse as _up
            qs = _up.parse_qs(self.path.split("?", 1)[-1])
            code = qs.get("code", [""])[0]
            if not code:
                self._json_response(400, {"error": "missing code"})
                return
            client_key = get_setting("tiktok_client_key", "")
            client_secret = get_setting("tiktok_client_secret", "")
            redirect_uri = get_setting("tiktok_redirect_uri", "")
            try:
                tokens = exchange_code(client_key, client_secret, code, redirect_uri)
                set_setting("tiktok_access_token", tokens.get("access_token", ""))
                set_setting("tiktok_refresh_token", tokens.get("refresh_token", ""))
                set_setting("tiktok_open_id", tokens.get("open_id", ""))
                self.send_response(302)
                self.send_header("Location", "/tiktok?tab=settings&connected=1")
                self.end_headers()
            except (TikTokError, Exception) as e:
                self._json_response(500, {"error": str(e)})

        else:
            self._json_response(404, {"error": "not found"})

    def _handle_tiktok_post(self) -> None:
        """TikTok subsystem write endpoints."""
        import json as _json
        from openjarvis.tiktok.state import (
            approve_video, reject_video, approve_comment, reject_comment,
            get_setting, set_setting, save_settings, load_settings, add_comment_reply,
            load_comments, load_trends,
        )
        from openjarvis.tiktok.pipeline import tiktok_publisher_entry

        path_only = self.path.split("?")[0].rstrip("/")
        length = int(self.headers.get("Content-Length", 0))
        body = _json.loads(self.rfile.read(length)) if length else {}

        if path_only.startswith("/tiktok/approve/"):
            vid_id = path_only.split("/")[-1]
            ok = approve_video(vid_id)
            self._json_response(200, {"ok": ok})

        elif path_only.startswith("/tiktok/reject/"):
            vid_id = path_only.split("/")[-1]
            reject_video(vid_id)
            self._json_response(200, {"ok": True})

        elif path_only.startswith("/tiktok/post/"):
            queue_id = path_only.split("/")[-1]
            result = tiktok_publisher_entry({"queue_id": queue_id})
            self._json_response(200, result)

        elif path_only == "/tiktok/trigger":
            from openjarvis.tiktok.trend_scorer import write_tiktok_trends
            threshold = get_setting("threshold", 70)
            try:
                items, note = write_tiktok_trends(threshold)
                trends = load_trends()
                top_score = trends[0].get("tiktok_score", 0) if trends else 0
                self._json_response(200, {
                    "ok": True,
                    "qualified": len(items),
                    "scanned": len(trends),
                    "top_score": top_score,
                    "threshold": threshold,
                    "note": note,
                })
            except Exception as e:
                self._json_response(500, {"error": str(e)})

        elif path_only == "/tiktok/settings":
            settings = load_settings()
            for key in ("kling_api_key", "kling_api_secret", "tiktok_client_key",
                        "tiktok_client_secret", "tiktok_redirect_uri",
                        "threshold", "rpm_gbp"):
                if key in body:
                    settings[key] = body[key]
            save_settings(settings)
            self._json_response(200, {"ok": True})

        elif path_only.startswith("/tiktok/comments/approve/"):
            reply_id = path_only.split("/")[-1]
            from openjarvis.tiktok.tiktok_client import post_comment, TikTokError
            access_token = get_setting("tiktok_access_token", "")
            comments = load_comments()
            entry = next((c for c in comments if c["id"] == reply_id), None)
            if not entry:
                self._json_response(404, {"error": "reply not found"})
                return
            try:
                post_comment(entry["video_id"], entry["draft_reply"], access_token)
                approve_comment(reply_id)
                self._json_response(200, {"ok": True})
            except TikTokError as e:
                self._json_response(500, {"error": str(e)})

        elif path_only.startswith("/tiktok/comments/reject/"):
            reply_id = path_only.split("/")[-1]
            reject_comment(reply_id)
            self._json_response(200, {"ok": True})

        elif path_only == "/tiktok/comments/draft":
            r = add_comment_reply(
                body.get("comment_id", ""),
                body.get("video_id", ""),
                body.get("commenter", ""),
                body.get("original_comment", ""),
                body.get("draft_reply", ""),
            )
            self._json_response(200, {"ok": True, "id": r["id"]})

        else:
            self._json_response(404, {"error": "not found"})

    def _handle_chat_sse(self) -> None:
        """SSE stream for the right-edge chat widget. Emits two event
        kinds: msg (a new operator/jarvis exchange line) and toggle (a
        widget-open/close hint from a voice fast-path)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        # No replay on subscribe — the widget seeds itself via the
        # /chat_history one-shot, then live-appends from this stream.
        # Avoids a double-render flicker when the widget opens.
        _chat_history.subscribe(self.wfile)
        try:
            while True:
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            _chat_history.unsubscribe(self.wfile)

    def _handle_orch_sse(self) -> None:
        """SSE stream of orchestry agent/task state."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        # Send initial snapshot so the UI can render immediately
        initial = json.dumps(orch_bridge.get_snapshot())
        try:
            self.wfile.write(f"data: {initial}\n\n".encode())
            self.wfile.flush()
        except Exception:
            return

        orch_bridge.subscribe(self.wfile)
        try:
            while True:
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            orch_bridge.unsubscribe(self.wfile)

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress access logs


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


_server_thread: Optional[threading.Thread] = None


def start_brain_server(open_browser: bool = True) -> None:
    """Start the brain visualization server in a background thread."""
    global _server_thread
    if _server_thread is not None:
        return  # already running

    # Audit 2026-04-26 C3: minimum-PIN-length advisory at startup. We
    # only WARN (don't refuse to boot) because a short PIN is still
    # better than no PIN, and refusing to start would surprise the
    # operator. Lockout (above) provides the actual brute-force
    # defence; this warning prompts the operator to lengthen the PIN
    # the next time they edit jarvis.bat.
    pin = _public_pin()
    if pin and len(pin) < _PIN_MIN_LENGTH:
        logger.warning(
            "OPENJARVIS_PUBLIC_PIN is only %d chars — recommend at least "
            "%d for brute-force resistance over the public tunnel. "
            "Lockout still protects you (8 wrong attempts/5min = 30min "
            "ban) but a longer PIN raises the bar significantly.",
            len(pin), _PIN_MIN_LENGTH,
        )
    elif not pin:
        logger.warning(
            "OPENJARVIS_PUBLIC_PIN is unset — every PIN-gated endpoint "
            "is currently OPEN. This is fine for local dev but DO NOT "
            "expose via Cloudflare tunnel without setting a PIN."
        )

    def _run() -> None:
        # ThreadingHTTPServer: each request gets its own thread so long-lived
        # SSE streams (/events) don't block new requests from cloudflared,
        # the phone page, or a second browser tab.
        server = ThreadingHTTPServer(("0.0.0.0", _PORT), _Handler)
        server.daemon_threads = True  # don't block interpreter shutdown
        server.serve_forever()

    _server_thread = threading.Thread(target=_run, daemon=True)
    _server_thread.start()

    # Start the orch (orchestry) bridge alongside the HTTP server so the
    # agent-flow HUD always has data. Degrades gracefully if orch CLI is absent.
    try:
        orch_bridge.start_orch_bridge()
    except Exception:
        logger.exception("orch bridge failed to start")

    # Start the graphify daily-rebuild thread (Path B follow-up,
    # 2026-04-29). Fires every midnight, walks the vault subset via the
    # in-process vault_graph extractor, writes graph.json. Replaces the
    # broken external graphify CLI invocation. Operator can override the
    # fire time via OPENJARVIS_GRAPHIFY_REBUILD_HOUR / _MINUTE env vars.
    try:
        from openjarvis.cli import graphify_bridge
        rebuild_hour = int(os.environ.get("OPENJARVIS_GRAPHIFY_REBUILD_HOUR", "0"))
        rebuild_minute = int(os.environ.get("OPENJARVIS_GRAPHIFY_REBUILD_MINUTE", "0"))
        graphify_bridge.start_daily_rebuild(hour=rebuild_hour, minute=rebuild_minute)
    except Exception:
        logger.exception("graphify daily-rebuild failed to start")

    # agentmemory episodic memory sidecar — warn if offline, never crash
    try:
        from openjarvis.tools.agentmemory_client import health as _am_health
        _am_url = os.environ.get("AGENTMEMORY_URL", "http://localhost:7730")
        if _am_health():
            logger.info("agentmemory sidecar online at %s", _am_url)
        else:
            logger.warning(
                "agentmemory sidecar offline — episodic memory unavailable. "
                "Start jarvis.bat to enable (port 7730)."
            )
    except Exception as exc:
        logger.warning("agentmemory health check failed: %s — continuing without episodic memory", exc)

    # Markets — daily briefing now fires via the vault scheduler reading
    # Brain/Scheduled/<date> - financial-researcher - daily markets
    # briefing.md (registered as a python-provider agent in agent_runner).
    # No daemon thread to start here — gives SCHEDULE-panel visibility
    # the daemon-thread approach didn't have.

    # UniFi bridge no longer started — operator removed it from the HUD.
    # unifi_bridge.py remains on disk if it's wanted back.

    local_ip = _get_local_ip()
    url = f"http://{local_ip}:{_PORT}/brain.html?host={local_ip}:{_PORT}"

    if open_browser:
        time.sleep(0.3)
        webbrowser.open(url)


def set_brain_state(state: str) -> None:
    """Update the brain visualization state."""
    _brain_state.update(state=state)


def set_brain_energy(energy: float) -> None:
    """Update the brain visualization energy level."""
    _brain_state.update(energy=energy)


__all__ = ["start_brain_server", "set_brain_state", "set_brain_energy"]

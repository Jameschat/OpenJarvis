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
import threading
import time
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, List, Optional

from openjarvis.cli import orch_bridge
# UniFi bridge removed from active use 2026-04-26 — operator didn't find
# the panel useful. unifi_bridge.py remains on disk if it's wanted back.

logger = logging.getLogger(__name__)

_WEB_DIR = Path(__file__).resolve().parent.parent.parent.parent / "jarvis_web"
_PORT = 7710


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
_PROCESS_DEADLINE_S = 25


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
                          "/manifest.webmanifest"):
            # Static shell — auth happens client-side via the modal
            if path_only in ("/", "/brain", "/brain.html"):
                self.path = "/brain.html"
            elif path_only in ("/phone", "/phone.html"):
                self.path = "/phone.html"
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
        elif self.path.startswith("/markets/"):
            # Markets subsystem — Day-1 read endpoints for the HUD panel.
            # /markets/watchlist  → operator's tracked tickers + cached prices
            # /markets/today      → placeholder for the 06:15 briefing artefact
            #                       (LLM pipeline ships next session)
            # /markets/health     → SQLite + ingestion source health
            self._handle_markets_get()
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
        elif self.path == "/music/status":
            self._handle_music_status()
        elif self.path in ("/", "/brain", "/brain.html"):
            self.path = "/brain.html"
            super().do_GET()
        elif self.path in ("/phone", "/phone.html"):
            self.path = "/phone.html"
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
        else:
            self.send_error(404, "Not Found")

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
                from openjarvis.markets.sources import yf, kraken
                return self._json_response(200, {
                    "ok": True,
                    "store": store.health(),
                    "sources": {
                        "yfinance": yf.is_available(),
                        "kraken": kraken.is_available(),
                    },
                })
        except Exception:
            logger.exception("/markets/%s failed", sub)
            return self._json_response(500, {
                "error": "internal error", "ref": _err_ref(),
            })
        return self._json_response(404, {"error": "unknown markets endpoint"})

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

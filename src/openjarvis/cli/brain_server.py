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
from openjarvis.cli import unifi_bridge

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

# Serialize concurrent /voice_turn calls. faster-whisper's model isn't safe
# to invoke from two threads at once — a second request arriving while the
# first is still inside transcribe() can hang indefinitely. This lock ensures
# each turn runs to completion before the next begins.
_voice_turn_lock = threading.Lock()


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

_PIN_SESSIONS: Dict[str, float] = {}  # token -> expires_at (unix seconds)
_PIN_SESSIONS_LOCK = threading.Lock()
_SESSION_TTL = 30 * 86400              # 30 days


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
                self._json_response(500, {"error": str(exc)})
        elif self.path == "/vault/openapi.json":
            # OpenAPI schema for the ChatGPT Custom GPT "Add Action" flow
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
                self._json_response(500, {"error": str(exc)})
        elif self.path == "/unifi":
            self._json_response(200, unifi_bridge.get_snapshot())
        elif self.path.startswith("/unifi_events"):
            self._handle_unifi_sse()
        elif self.path == "/schedule":
            try:
                from openjarvis.tools import agent_runner
                self._json_response(200, {"schedules": agent_runner.list_scheduled()})
            except Exception as exc:
                logger.exception("/schedule list failed")
                self._json_response(500, {"error": str(exc)})
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
            self._json_response(500, {"error": str(exc)})

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
            self._json_response(500, {"error": str(exc)})

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
            self._json_response(500, {"error": str(exc)})

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
            self._json_response(500, {"error": str(exc)})

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
            self._json_response(500, {"error": str(exc)})

    def _handle_chat(self) -> None:
        """POST /chat — body: {text, files: [{name, type, content_b64}]}.

        Like /voice_turn but driven by the chat composer in Mission Control.
        Saves any attached files to Brain/Inbox/, reads text-ish files into
        the prompt as context, runs through the same process_command pipeline.
        """
        if _ctx.process_command is None:
            return self._json_response(503, {"error": "Voice pipeline not initialised."})

        # Reuse the voice-turn lock so chat + mic + menu don't collide
        acquired = _voice_turn_lock.acquire(timeout=30)
        if not acquired:
            return self._json_response(503, {"error": "Busy — try again."})
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
            response_text = _ctx.process_command(effective) or ""

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

            self._json_response(200, {
                "transcript":   text,
                "response":     response_text,
                "audio_b64":    audio_b64,
                "attachments":  saved,
            })
        except Exception as exc:
            logger.exception("/chat failed")
            _brain_state.update(state="idle")
            self._json_response(500, {"error": str(exc)})
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
        # concurrent mic turn.
        acquired = _voice_turn_lock.acquire(timeout=30)
        if not acquired:
            self._json_response(503, {"error": "Busy — try again."})
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
            response_text = _ctx.process_command(text) or ""

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
            self._json_response(500, {"error": str(exc)})
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
            self._json_response(500, {"error": str(exc)})

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
            self._json_response(500, {"error": str(exc)})

    def _handle_cancel_all(self) -> None:
        """Terminate every running task + cancel all queued todos."""
        from openjarvis.tools import agent_runner
        try:
            n = agent_runner.cancel_all_running()
            self._json_response(200, {"ok": True, "cancelled": n})
        except Exception as exc:
            logger.exception("/agents/cancel_all failed")
            self._json_response(500, {"error": str(exc)})

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
            self._json_response(500, {"error": str(exc)})

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
            self._json_response(500, {"error": str(exc)})

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
        # Constant-time comparison + small delay to deter brute force
        time.sleep(0.4)
        if not hmac.compare_digest(submitted, pin):
            return self._json_response(401, {"ok": False, "error": "wrong PIN"})
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

        If ``OPENJARVIS_VAULT_TOKEN`` env var is set, requests must carry
        ``Authorization: Bearer <token>``. If the env var is unset, the
        endpoints are open (useful while testing locally).
        """
        expected = os.environ.get("OPENJARVIS_VAULT_TOKEN", "").strip()
        if not expected:
            return True
        auth = self.headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            return auth[7:].strip() == expected
        return False

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
            self._json_response(500, {"error": str(exc)})

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
            self._json_response(500, {"error": str(exc)})

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
            from openjarvis.tools.obsidian_brain import DEFAULT_VAULT
            # Resolve by exact stem match within the vault, recursively
            from pathlib import Path as _P
            target = None
            for md in _P(DEFAULT_VAULT).rglob("*.md"):
                if any(part.startswith(".") for part in md.parts):
                    continue
                if md.stem == name:
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
            self._json_response(500, {"error": str(exc)})

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
            from openjarvis.tools.obsidian_brain import DEFAULT_VAULT, BRAIN_ROOT
            base = (BRAIN_ROOT / folder) if folder else BRAIN_ROOT
            if not base.exists():
                return self._json_response(404, {"error": f"folder '{folder}' not found"})
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
            self._json_response(500, {"error": str(exc)})

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
                p = DAILY_DIR / f"{date}.md"
                content = p.read_text(encoding="utf-8") if p.exists() else None
                if content is not None:
                    obsidian_brain._emit_event("read", f"journal: {date}", kind="daily", source="chatgpt")
            if content is None:
                return self._json_response(404, {"error": f"no journal for {date}"})
            self._json_response(200, {"date": date, "content": content})
        except Exception as exc:
            logger.exception("/vault/journal failed")
            self._json_response(500, {"error": str(exc)})

    def _handle_claude_event(self) -> None:
        """Ingest a Claude Code hook payload.

        Called by a small wrapper script registered as a hook in
        ~/.claude/settings.json. Each Claude Code session becomes a live
        card in mission control for the duration of its activity.

        Tolerant of encoding issues: tries UTF-8 first, falls back to
        CP1252 / latin-1. PowerShell 5.1 in particular can send bodies
        in the console code page if the hook wrapper isn't careful.
        """
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
            self._json_response(500, {"error": str(exc)})

    def _handle_voice_turn(self) -> None:
        """Accept an audio upload, run the full Jarvis pipeline, return JSON."""
        if _ctx.stt_backend is None or _ctx.process_command is None:
            self._json_response(503, {"error": "Voice pipeline not initialised."})
            return

        # Only one voice turn at a time. If another turn is already in flight,
        # wait up to 30s for it to finish; if it doesn't, reject with 503 so
        # the client can recover instead of hanging forever.
        acquired = _voice_turn_lock.acquire(timeout=30)
        if not acquired:
            logger.warning("/voice_turn rejected: another turn held the lock for >30s")
            self._json_response(503, {"error": "Voice pipeline busy — try again."})
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
            response_text = _ctx.process_command(transcript) or ""

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

            self._json_response(200, {
                "transcript": transcript,
                "response": response_text,
                "audio_b64": audio_b64,
            })

        except Exception as exc:
            logger.exception("/voice_turn failed")
            _brain_state.update(state="idle")
            self._json_response(500, {"error": str(exc)})

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

    def _handle_unifi_sse(self) -> None:
        """SSE stream of UniFi snapshot updates for the NETWORKS panel."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            initial = json.dumps(unifi_bridge.get_snapshot())
            self.wfile.write(("data: " + initial + "\n\n").encode("utf-8"))
            self.wfile.flush()
        except Exception:
            return
        unifi_bridge.subscribe(self.wfile)
        try:
            while True:
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            unifi_bridge.unsubscribe(self.wfile)

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

    # Start the UniFi bridge — read-only network status from api.ui.com.
    # Dormant unless OPENJARVIS_UNIFI_KEY env var is set.
    try:
        unifi_bridge.start_unifi_bridge()
    except Exception:
        logger.exception("unifi bridge failed to start")

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

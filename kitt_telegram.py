"""J.A.R.V.I.S. Telegram Bot — access J.A.R.V.I.S. from your phone with live scanner."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("kitt-telegram")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not TOKEN:
    print("Set TELEGRAM_BOT_TOKEN environment variable first.")
    sys.exit(1)

WEB_PORT = 7710
WEB_DIR = Path(__file__).parent / "kitt_web"


# ---------------------------------------------------------------------------
# SSE state broadcaster — streams J.A.R.V.I.S. state to the web mini app
# ---------------------------------------------------------------------------
class Statebroadcaster:
    """Thread-safe state broadcaster for SSE clients."""

    def __init__(self) -> None:
        self._state = "idle"
        self._energy = 0.0
        self._queues: List[asyncio.Queue] = []
        self._lock = threading.Lock()

    def set_state(self, state: str) -> None:
        self._state = state
        self._broadcast()

    def set_energy(self, energy: float) -> None:
        self._energy = max(0.0, min(1.0, energy))
        self._broadcast()

    def _broadcast(self) -> None:
        msg = json.dumps({"state": self._state, "energy": self._energy})
        with self._lock:
            for q in self._queues:
                try:
                    q.put_nowait(msg)
                except asyncio.QueueFull:
                    pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        with self._lock:
            self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._queues = [x for x in self._queues if x is not q]


broadcaster = Statebroadcaster()


# ---------------------------------------------------------------------------
# Web server — serves the scanner HTML + SSE endpoint
# ---------------------------------------------------------------------------
def start_web_server() -> None:
    """Run a tiny HTTP + SSE server in a background thread."""
    from http.server import HTTPServer, SimpleHTTPRequestHandler
    import io

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(WEB_DIR), **kwargs)

        def do_GET(self) -> None:
            if self.path.startswith("/events"):
                self._handle_sse()
            else:
                super().do_GET()

        def _handle_sse(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            q = broadcaster.subscribe()
            try:
                # Send initial state
                initial = json.dumps({"state": broadcaster._state, "energy": broadcaster._energy})
                self.wfile.write(f"data: {initial}\n\n".encode())
                self.wfile.flush()

                while True:
                    try:
                        # Poll the queue (blocking with timeout)
                        import select
                        # Use a simple polling loop since we can't use asyncio here
                        for _ in range(100):
                            try:
                                msg = q.get_nowait()
                                self.wfile.write(f"data: {msg}\n\n".encode())
                                self.wfile.flush()
                            except asyncio.QueueEmpty:
                                pass
                            time.sleep(0.05)
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        break
            finally:
                broadcaster.unsubscribe(q)

        def log_message(self, format: str, *args: Any) -> None:
            pass  # Suppress access logs

    server = HTTPServer(("0.0.0.0", WEB_PORT), Handler)
    logger.info(f"Web server running on http://0.0.0.0:{WEB_PORT}")
    server.serve_forever()


# ---------------------------------------------------------------------------
# Telegram bot
# ---------------------------------------------------------------------------
def main() -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
    from telegram.ext import (
        ApplicationBuilder,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )

    from openjarvis.cli._tool_names import resolve_tool_names
    from openjarvis.cli.voice_cmd import (
        _try_calendar,
        _try_lights,
        _try_macro,
        _try_sonos,
    )
    from openjarvis.core.config import load_config
    from openjarvis.core.types import Message, Role
    from openjarvis.engine import get_engine
    from openjarvis.intelligence import register_builtin_models

    class _FakeConsole:
        def print(self, *a, **k):
            pass

    fake_console = _FakeConsole()

    config = load_config()
    register_builtin_models()

    resolved = get_engine(config, None)
    if resolved is None:
        print("No inference engine available.")
        sys.exit(1)

    engine_name, engine = resolved
    model = config.intelligence.default_model
    if not model:
        from openjarvis.engine import discover_engines, discover_models

        all_engines = discover_engines(config)
        all_models = discover_models(all_engines)
        engine_models = all_models.get(engine_name, [])
        model = engine_models[0] if engine_models else None

    if not model:
        print("No model available.")
        sys.exit(1)

    # Agent setup
    agent = None
    agent_key = config.agent.default_agent
    if agent_key and agent_key != "none":
        try:
            import openjarvis.agents  # noqa: F401

            from openjarvis.core.events import EventBus
            from openjarvis.core.registry import AgentRegistry

            if AgentRegistry.contains(agent_key):
                agent_cls = AgentRegistry.get(agent_key)
                kwargs: dict = {"bus": EventBus()}

                if getattr(agent_cls, "accepts_tools", False):
                    tool_names_list = resolve_tool_names(
                        None,
                        getattr(config.tools, "enabled", None),
                        getattr(config.agent, "tools", None),
                    )
                    if tool_names_list:
                        import openjarvis.tools  # noqa: F401

                        from openjarvis.core.registry import ToolRegistry
                        from openjarvis.tools._stubs import BaseTool

                        tool_instances = []
                        for tname in tool_names_list:
                            if ToolRegistry.contains(tname):
                                tcls = ToolRegistry.get(tname)
                                if isinstance(tcls, type) and issubclass(tcls, BaseTool):
                                    tool_instances.append(tcls())
                                elif isinstance(tcls, BaseTool):
                                    tool_instances.append(tcls)
                        if tool_instances:
                            kwargs["tools"] = tool_instances
                    kwargs["max_turns"] = config.agent.max_turns

                kwargs["system_prompt"] = (
                    "You are J.A.R.V.I.S. (Just A Rather Very Intelligent System). "
                    "Occasionally address the user as sir. Be concise for mobile chat."
                )
                agent = agent_cls(engine, model, **kwargs)
        except Exception as exc:
            logger.warning(f"Agent setup failed: {exc}")

    histories: dict[int, list] = {}

    # Get local IP for web app URL
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "localhost"

    web_url = f"http://{local_ip}:{WEB_PORT}"
    logger.info(f"Scanner web app: {web_url}")

    # --- Handlers ---

    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "Open J.A.R.V.I.S. Scanner",
                web_app=WebAppInfo(url=f"{web_url}/?host={local_ip}:{WEB_PORT}"),
            )],
        ])
        await update.message.reply_text(
            "J.A.R.V.I.S. online, Michael.\n\n"
            "Tap the button below to open the scanner display, "
            "or just type a message.",
            reply_markup=keyboard,
        )

    async def cmd_scanner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "Open J.A.R.V.I.S. Scanner",
                web_app=WebAppInfo(url=f"{web_url}/?host={local_ip}:{WEB_PORT}"),
            )],
        ])
        await update.message.reply_text("Scanner display:", reply_markup=keyboard)

    async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        histories.pop(chat_id, None)
        await update.message.reply_text("Memory cleared, sir.")

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = update.message.text.strip()
        if not text:
            return

        chat_id = update.effective_chat.id
        logger.info(f"[{chat_id}] {text}")

        broadcaster.set_state("thinking")

        # Fast paths
        result = _try_macro(text, fake_console)
        if result:
            broadcaster.set_state("idle")
            await update.message.reply_text(result)
            return

        result = _try_sonos(text, fake_console)
        if result:
            broadcaster.set_state("idle")
            await update.message.reply_text(result)
            return

        result = _try_lights(text, fake_console)
        if result:
            broadcaster.set_state("idle")
            await update.message.reply_text(result)
            return

        result = _try_calendar(text, fake_console)
        if result:
            broadcaster.set_state("idle")
            await update.message.reply_text(result)
            return

        # LLM path
        if chat_id not in histories:
            histories[chat_id] = [
                Message(
                    role=Role.SYSTEM,
                    content=(
                        "You are J.A.R.V.I.S., a sophisticated AI assistant. "
                        "Occasionally address the user as sir. Be concise for mobile chat."
                    ),
                )
            ]

        history = histories[chat_id]
        history.append(Message(role=Role.USER, content=text))

        if len(history) > 30:
            history[:] = history[:1] + history[-20:]

        try:
            if agent is not None:
                response = agent.run(text)
                content = response.content if hasattr(response, "content") else str(response)
            else:
                gen_result = engine.generate(history, model=model)
                content = (
                    gen_result.get("content", "")
                    if isinstance(gen_result, dict)
                    else str(gen_result)
                )

            history.append(Message(role=Role.ASSISTANT, content=content))
            broadcaster.set_state("idle")
            await update.message.reply_text(content)
            logger.info(f"[{chat_id}] -> {content[:100]}")

        except Exception as exc:
            broadcaster.set_state("idle")
            await update.message.reply_text(f"Error: {exc}")
            logger.error(f"Error: {exc}")

    # --- Voice message handler ---
    async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle voice messages — download, transcribe, process."""
        voice = update.message.voice or update.message.audio
        if not voice:
            return

        chat_id = update.effective_chat.id
        broadcaster.set_state("listening")
        logger.info(f"[{chat_id}] Voice message received ({voice.duration}s)")

        try:
            # Download the voice file
            file = await context.bot.get_file(voice.file_id)
            audio_bytes = await file.download_as_bytearray()

            broadcaster.set_state("thinking")

            # Transcribe with STT backend
            from openjarvis.speech._discovery import get_speech_backend

            stt = get_speech_backend(config)
            if stt is None:
                await update.message.reply_text("No speech-to-text backend available.")
                broadcaster.set_state("idle")
                return

            # Telegram voice messages are OGG/OPUS format
            result = stt.transcribe(bytes(audio_bytes), format="ogg", language="en")
            text = result.text.strip()

            if not text:
                await update.message.reply_text("I couldn't make out what you said, Michael.")
                broadcaster.set_state("idle")
                return

            logger.info(f"[{chat_id}] Transcribed: {text}")
            await update.message.reply_text(f"I heard: {text}")

            # Process through the same handler as text
            update.message.text = text
            await handle_message(update, context)

        except Exception as exc:
            broadcaster.set_state("idle")
            await update.message.reply_text(f"Voice processing error: {exc}")
            logger.error(f"Voice error: {exc}")

    # Start web server in background
    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()

    # Build and run Telegram bot
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("scanner", cmd_scanner))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("J.A.R.V.I.S. Telegram bot running!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

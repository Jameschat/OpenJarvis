"""Pre-synthesised voice acks.

Short audio clips Jarvis plays at the start of a turn ("On it, sir.")
to signal "I heard you, working on it" while the LLM tool chain runs.
Without this, the operator stares at silence for 5-30s on a complex
turn and assumes nothing happened — the most-cited frustration with
the current pipeline.

Acks are TTS-synthesised lazily on first use (zero startup cost),
cached in memory keyed by phrase, and broadcast as
``{kind: "speak_now", audio_b64, phrase}`` events on the chat_history
SSE bus. The HUD listens and plays the mp3 immediately.

Non-fatal everywhere: a TTS failure, a missing backend, or a broken
SSE bus must never break an upstream voice/chat turn — we silently
no-op and the turn proceeds as it did before this module existed.
"""

from __future__ import annotations

import base64
import logging
import random
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Short, conversational. Voice persona is "British butler" so they
# sound natural, not robotic. Variety prevents the operator from
# noticing the same clip every turn.
_THINKING_PHRASES = [
    "On it, sir.",
    "One moment.",
    "Working on it.",
    "Looking now.",
    "Right away.",
]

# Said when the brain is still processing the previous turn and a new
# request comes in. Distinct from the thinking ack so the operator
# knows their utterance wasn't lost — it's just queued.
_BUSY_PHRASES = [
    "Still on the previous one — hang on.",
    "Just finishing the last request, sir.",
]

# Said when the tool chain blew past the deadline.
_TIMEOUT_PHRASES = [
    "That's taking longer than expected, sir.",
]


_cache: Dict[str, str] = {}
_cache_lock = threading.Lock()
_tts_backend: Optional[Any] = None
_voice_id: str = "fable"
_speed: float = 1.0
_configured = False


def configure(tts_backend: Any, voice_id: str = "fable",
              speed: float = 1.0) -> None:
    """Wire in the TTS backend used by the rest of voice_cmd. Safe to
    call multiple times — last call wins. Called from
    ``brain_server.set_voice_context`` so the voice loop's own setup
    file doesn't need to change."""
    global _tts_backend, _voice_id, _speed, _configured
    _tts_backend = tts_backend
    _voice_id = voice_id or "fable"
    _speed = float(speed) if speed else 1.0
    _configured = True


def is_configured() -> bool:
    return _configured and _tts_backend is not None


def _synthesise(phrase: str) -> Optional[str]:
    if _tts_backend is None:
        return None
    try:
        res = _tts_backend.synthesize(
            phrase, voice_id=_voice_id, speed=_speed, output_format="mp3",
        )
        return base64.b64encode(res.audio).decode("ascii")
    except Exception:
        logger.warning("voice_ack synth failed for %r", phrase, exc_info=True)
        return None


def _get_or_make(phrase: str) -> Optional[str]:
    with _cache_lock:
        cached = _cache.get(phrase)
    if cached is not None:
        return cached
    audio = _synthesise(phrase)
    if audio is None:
        return None
    with _cache_lock:
        _cache[phrase] = audio
    return audio


def _broadcast(phrase: str, audio_b64: str) -> None:
    try:
        from openjarvis.cli.brain_server import _chat_history
    except Exception:
        return
    try:
        _chat_history._broadcast({
            "kind": "speak_now",
            "audio_b64": audio_b64,
            "phrase": phrase,
            "ts": time.time(),
        })
    except Exception:
        logger.debug("voice_ack broadcast failed", exc_info=True)


def emit_thinking() -> None:
    """Pick a random thinking ack and broadcast it. Called at the start
    of every voice/chat/text turn after input validation but before the
    LLM tool chain runs. Synth happens once per phrase, then cached —
    typical call cost after warm-up is ~50µs."""
    if not is_configured():
        return
    phrase = random.choice(_THINKING_PHRASES)
    audio = _get_or_make(phrase)
    if audio:
        _broadcast(phrase, audio)


def emit_busy() -> None:
    """Played when the lock is held by a previous turn — operator hears
    'still on the previous one' instead of staring at a stuck UI."""
    if not is_configured():
        return
    phrase = random.choice(_BUSY_PHRASES)
    audio = _get_or_make(phrase)
    if audio:
        _broadcast(phrase, audio)


def emit_timeout() -> None:
    """Played when the tool chain exceeds the deadline. Caller should
    also write a vault marker so the operator can grep for stalled
    turns."""
    if not is_configured():
        return
    phrase = random.choice(_TIMEOUT_PHRASES)
    audio = _get_or_make(phrase)
    if audio:
        _broadcast(phrase, audio)


def warmup_async() -> None:
    """Optionally pre-synthesise every ack on a background thread so
    the very first emit doesn't pay the synth latency. Idempotent. No-
    op if not configured. Safe to call from server startup."""
    if not is_configured():
        return
    def _go() -> None:
        for phrase in _THINKING_PHRASES + _BUSY_PHRASES + _TIMEOUT_PHRASES:
            try:
                _get_or_make(phrase)
            except Exception:
                pass
    threading.Thread(target=_go, daemon=True, name="voice_ack-warmup").start()


__all__ = [
    "configure", "is_configured",
    "emit_thinking", "emit_busy", "emit_timeout",
    "warmup_async",
]

"""Smart LLM fallback for J.A.R.V.I.S. voice + chat.

When the user asks something that escapes every fast-path (vault recall,
team task, sonos, lights, etc.), we used to fall through to the local
``qwen3:8b`` Ollama engine. That model is slow + weak, especially with the
larger system prompts we now inject (vault context block can be 2-4 KB).

This module provides a single function ``generate_fallback`` that:

1. Prefers OpenAI's ``gpt-4o-mini`` (or a model overridden via
   ``OPENJARVIS_FALLBACK_MODEL`` env var) when ``OPENAI_API_KEY`` is set —
   1-2s response, much smarter, costs ~£0.001 per query
2. Falls back to the original local engine if the OpenAI client isn't
   available or the env var is unset

The interface matches the existing engine pattern (takes a list of
``Message`` objects, returns a string), so the call sites in
``voice_cmd.py`` only change one line each.
"""

from __future__ import annotations

import logging
import os
from typing import Any, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Default model when OPENAI_API_KEY is set. Cheap + fast + smart enough.
DEFAULT_OPENAI_MODEL = os.environ.get("OPENJARVIS_FALLBACK_MODEL", "gpt-4o-mini")

# Reasonable cap so a runaway response doesn't burn budget
MAX_OUTPUT_TOKENS = 600


_openai_client: Optional[Any] = None
_openai_init_attempted = False


def _get_openai_client() -> Optional[Any]:
    """Lazy-init the OpenAI client. Returns None if no key or library."""
    global _openai_client, _openai_init_attempted
    if _openai_init_attempted:
        return _openai_client
    _openai_init_attempted = True
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        from openai import OpenAI
        _openai_client = OpenAI()
        logger.info("LLM fallback: OpenAI %s ready", DEFAULT_OPENAI_MODEL)
    except Exception as exc:
        logger.warning("LLM fallback: OpenAI client init failed: %s", exc)
        _openai_client = None
    return _openai_client


def _messages_to_openai(messages: Sequence) -> List[dict]:
    """Convert OpenJarvis ``Message`` objects to OpenAI's chat.completions
    format. Tolerant of either a Message dataclass with .role.value/.content
    or already-dict messages."""
    out = []
    for m in messages:
        if isinstance(m, dict):
            out.append(m)
            continue
        role = getattr(m, "role", None)
        # Message.role is a Role enum — get its string value
        role_str = getattr(role, "value", None) or str(role) or "user"
        if role_str == "tool":
            role_str = "user"   # OpenAI's chat API doesn't accept "tool" without tool_call_id
        content = getattr(m, "content", "") or ""
        out.append({"role": role_str, "content": content})
    return out


def generate_fallback(messages: Sequence, fallback_engine: Any = None,
                      fallback_model: str = "") -> str:
    """Generate a response. Prefers OpenAI gpt-4o-mini when available,
    otherwise delegates to the supplied fallback engine.

    Args:
        messages: list of Message objects (or dicts)
        fallback_engine: the original engine to use if OpenAI isn't available
        fallback_model: model name for the fallback engine
    """
    client = _get_openai_client()
    if client is not None:
        try:
            resp = client.chat.completions.create(
                model=DEFAULT_OPENAI_MODEL,
                messages=_messages_to_openai(messages),
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=0.7,
            )
            content = (resp.choices[0].message.content or "").strip() if resp.choices else ""
            if content:
                return content
            logger.warning("OpenAI returned empty content — falling back to local engine")
        except Exception as exc:
            logger.warning("OpenAI call failed (%s) — falling back to local engine", exc)

    # Fallback path: original engine
    if fallback_engine is None:
        return "Sir, I'm not able to reason about that right now — no LLM available."
    try:
        result = fallback_engine.generate(messages, model=fallback_model)
        if isinstance(result, dict):
            return result.get("content", "") or ""
        return str(result)
    except Exception as exc:
        logger.exception("local fallback engine failed")
        return f"Error, sir: {exc}"


__all__ = ["generate_fallback", "DEFAULT_OPENAI_MODEL"]

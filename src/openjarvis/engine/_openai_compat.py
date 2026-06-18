"""Shared base for OpenAI-compatible ``/v1/`` engines."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any, Dict, List

import httpx

from openjarvis.core.types import Message
from openjarvis.engine._base import (
    EngineConnectionError,
    InferenceEngine,
    estimate_prompt_tokens,
    messages_to_dicts,
)
from openjarvis.engine._stubs import StreamChunk

logger = logging.getLogger(__name__)

# Qwen/Hermes-style tool call emitted as PLAIN TEXT, e.g.:
#   <function=file_write>
#   <parameter=path>E:/x/index.html</parameter>
#   <parameter=content><h1>Hi</h1></parameter>
#   </function>
# llama.cpp's native tool-call parser sometimes fails to structure this into
# `tool_calls` (observed with larger tool sets) and returns finish_reason="stop"
# with the raw XML in `content`. Without this fallback the agent never sees a
# tool call and just prints the XML — so "build/code" requests do nothing.
_FUNCTION_BLOCK_RE = re.compile(r"<function=([^>\s]+)\s*>(.*?)</function>", re.DOTALL)
_PARAMETER_RE = re.compile(r"<parameter=([^>\s]+)\s*>(.*?)</parameter>", re.DOTALL)


def _tools_as_text_instruction(tools: List[Dict[str, Any]]) -> str:
    """Render OpenAI tool schemas as a plain-text instruction telling the model
    to emit the Qwen <function=...> format. Used as a fallback when the server's
    native structured tool-calling fails (it can't JSON-parse large content like
    an HTML file). The XML format needs no JSON escaping, so big content is safe."""
    lines = [
        "You can call tools. To call a tool, reply with ONLY this exact format "
        "(no prose, no code fences):",
        "<function=TOOL_NAME>",
        "<parameter=PARAM_NAME>VALUE</parameter>",
        "</function>",
        "",
        "Available tools:",
    ]
    for t in tools:
        fn = t.get("function", t) if isinstance(t, dict) else {}
        name = fn.get("name", "")
        desc = (fn.get("description") or "").strip()
        params = (fn.get("parameters") or {}).get("properties") or {}
        plist = ", ".join(params.keys())
        lines.append(f"- {name}({plist}): {desc}")
    return "\n".join(lines)


def parse_text_tool_calls(content: str) -> List[Dict[str, str]] | None:
    """Parse Qwen/Hermes ``<function=...>`` text tool calls into the structured
    form the agent loop expects. Returns None when no such block is present."""
    if not content or "<function=" not in content:
        return None
    calls: List[Dict[str, str]] = []
    for idx, match in enumerate(_FUNCTION_BLOCK_RE.finditer(content)):
        name = match.group(1).strip()
        body = match.group(2)
        args: Dict[str, str] = {}
        for pmatch in _PARAMETER_RE.finditer(body):
            args[pmatch.group(1).strip()] = pmatch.group(2).strip("\r\n").strip()
        if not name:
            continue
        calls.append(
            {
                "id": f"call_{idx}",
                "name": name,
                "arguments": json.dumps(args, ensure_ascii=False),
            }
        )
    return calls or None


class _OpenAICompatibleEngine(InferenceEngine):
    """Base for engines that serve the OpenAI ``/v1/chat/completions`` API."""

    engine_id: str = ""
    _default_host: str = "http://localhost:8000"
    _api_prefix: str = "/v1"

    def __init__(self, host: str | None = None, *, timeout: float = 600.0) -> None:
        import os

        env_key = f"{self.engine_id.upper()}_HOST"
        self._host = (host or os.environ.get(env_key) or self._default_host).rstrip("/")
        self._client = httpx.Client(base_url=self._host, timeout=timeout)

    # -- InferenceEngine interface ------------------------------------------

    def generate(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages_to_dicts(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            **kwargs,
        }
        # Default to tool_choice=auto when tools are provided
        if "tools" in payload and "tool_choice" not in payload:
            payload["tool_choice"] = "auto"
        try:
            url = f"{self._api_prefix}/chat/completions"
            resp = self._client.post(url, json=payload)
            # Native structured tool-calling can fail two ways on a llama.cpp
            # lane: the server rejects the tools payload (400), or — worse — it
            # accepts it but can't JSON-parse the model's tool-call arguments
            # when they contain large/complex content like an HTML file (500
            # "Failed to parse tool call arguments"). Either way, fall back to
            # text-mode tools: drop the structured `tools` field, inject a
            # plain-text instruction describing the tools + the Qwen
            # <function=...> format, and let parse_text_tool_calls() recover the
            # call. That format needs no JSON escaping, so big content is safe.
            tools_payload = payload.get("tools")
            if tools_payload and resp.status_code in (400, 500):
                payload.pop("tools", None)
                payload.pop("tool_choice", None)
                msgs = list(payload.get("messages") or [])
                msgs.append(
                    {"role": "system", "content": _tools_as_text_instruction(tools_payload)}
                )
                payload["messages"] = msgs
                resp = self._client.post(url, json=payload)
            resp.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise EngineConnectionError(
                f"{self.engine_id} engine not reachable at {self._host}"
            ) from exc
        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return {
                "content": "",
                "usage": data.get("usage", {}),
                "model": data.get("model", model),
                "finish_reason": "error",
            }
        choice = choices[0]
        usage = data.get("usage", {})
        # Ensure prompt_tokens reflects the full prompt size (including
        # system prompt and all conversation history).
        # OpenAI-compat APIs (vLLM, SGLang) report full counts — KV
        # caching is transparent, so evaluated == full.
        reported_prompt = usage.get("prompt_tokens", 0)
        estimated_prompt = estimate_prompt_tokens(messages)
        prompt_tokens = max(reported_prompt, estimated_prompt)
        completion_tokens = usage.get("completion_tokens", 0)
        result: Dict[str, Any] = {
            "content": choice["message"].get("content") or "",
            "usage": {
                "prompt_tokens": prompt_tokens,
                "prompt_tokens_evaluated": reported_prompt or prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            "model": data.get("model", model),
            "finish_reason": choice.get("finish_reason", "stop"),
        }
        # Extract tool calls if present
        raw_tool_calls = choice["message"].get("tool_calls", [])
        if raw_tool_calls:
            result["tool_calls"] = [
                {
                    "id": tc.get("id", ""),
                    "name": tc.get("function", {}).get("name", ""),
                    "arguments": tc.get("function", {}).get("arguments", "{}"),
                }
                for tc in raw_tool_calls
            ]
        else:
            # Fallback: the model emitted a <function=...> tool call as plain
            # text that the server didn't structure. Parse it so the agent loop
            # can actually execute the tool instead of printing the XML.
            parsed = parse_text_tool_calls(result["content"])
            if parsed:
                result["tool_calls"] = parsed
                result["content"] = ""
                result["finish_reason"] = "tool_calls"
        return result

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages_to_dicts(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            **kwargs,
        }
        # Default to tool_choice=auto when tools are provided
        if "tools" in payload and "tool_choice" not in payload:
            payload["tool_choice"] = "auto"
        try:
            url = f"{self._api_prefix}/chat/completions"
            with self._client.stream("POST", url, json=payload) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_str = line[len("data:") :].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise EngineConnectionError(
                f"{self.engine_id} engine not reachable at {self._host}"
            ) from exc

    async def stream_full(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> AsyncIterator["StreamChunk"]:
        """Yield StreamChunks with content, tool_calls, and finish_reason."""
        msg_dicts = messages_to_dicts(messages)
        payload: Dict[str, Any] = {
            "model": model,
            "messages": msg_dicts,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            **kwargs,
        }
        if "tools" in payload and "tool_choice" not in payload:
            payload["tool_choice"] = "auto"
        try:
            url = f"{self._api_prefix}/chat/completions"
            with self._client.stream("POST", url, json=payload) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_str = line[len("data:") :].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    choice = chunk.get("choices", [{}])[0]
                    delta = choice.get("delta", {})
                    finish = choice.get("finish_reason")
                    content = delta.get("content")
                    tool_calls = delta.get("tool_calls")
                    usage = chunk.get("usage")

                    if content or tool_calls or finish or usage:
                        yield StreamChunk(
                            content=content,
                            tool_calls=tool_calls,
                            finish_reason=finish,
                            usage=usage,
                        )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise EngineConnectionError(
                f"{self.engine_id} engine not reachable at {self._host}"
            ) from exc

    def list_models(self) -> List[str]:
        try:
            resp = self._client.get(f"{self._api_prefix}/models")
            resp.raise_for_status()
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
        ) as exc:
            logger.warning(
                "Failed to list models from %s at %s: %s",
                self.engine_id,
                self._host,
                exc,
            )
            return []
        data = resp.json()
        return [m["id"] for m in data.get("data", [])]

    def health(self) -> bool:
        try:
            resp = self._client.get(f"{self._api_prefix}/models", timeout=2.0)
            return resp.status_code == 200
        except Exception as exc:
            logger.debug(
                "%s health check failed at %s: %s",
                self.engine_id,
                self._host,
                exc,
            )
            return False

    def close(self) -> None:
        self._client.close()


__all__ = ["_OpenAICompatibleEngine"]

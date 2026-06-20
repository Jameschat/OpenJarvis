"""Bridge sync agent.run() + EventBus events to an async SSE generator.

Subscribes to EventBus callbacks that push events into an asyncio.Queue,
runs agent.run() in a background thread, and yields SSE-formatted strings
from the queue for consumption by FastAPI's StreamingResponse.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import AsyncGenerator

from fastapi.responses import StreamingResponse

from openjarvis.agents._stubs import AgentContext, BaseAgent
from openjarvis.core.events import Event, EventBus, EventType
from openjarvis.server.models import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    DeltaMessage,
    StreamChoice,
    UsageInfo,
)
from openjarvis.server.stream_events import EVENT_SSE_NAMES

# Sentinel signalling that the agent thread has finished
_DONE = object()


def _estimate_prompt_tokens(messages: list) -> int:
    """Estimate prompt tokens from request messages (incl. system, user, context).

    Uses ~4 chars per token heuristic. Ensures system and all context are counted.
    """
    total = 0
    for m in messages:
        text = getattr(m, "content", None) or ""
        if isinstance(text, str):
            total += max(1, len(text) // 4)
        # Tool call messages may have structured content; still count what we can
        if hasattr(m, "tool_calls") and m.tool_calls:
            for tc in m.tool_calls:
                fn = tc.get("function") if isinstance(tc, dict) else {}
                args = fn.get("arguments", "") if isinstance(fn, dict) else ""
                total += max(1, len(str(args)) // 4)
    return total


class AgentStreamBridge:
    """Bridge between a synchronous agent and an async SSE stream.

    Pattern:
    1. Subscribe EventBus callbacks that push events into an asyncio.Queue
       via ``loop.call_soon_threadsafe()``.
    2. Run ``agent.run()`` in a thread via ``asyncio.to_thread()``.
    3. Async generator reads from queue and yields SSE-formatted strings.
    4. Unsubscribe from EventBus in ``finally`` block.
    """

    def __init__(
        self,
        agent: BaseAgent,
        bus: EventBus,
        model: str,
        request: ChatCompletionRequest,
    ) -> None:
        self._agent = agent
        self._bus = bus
        self._model = model
        self._request = request
        self._chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        self._queue: asyncio.Queue = asyncio.Queue()
        self._callbacks: dict[EventType, object] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_callback(self, event_type: EventType):
        """Create a callback that pushes the event onto the async queue."""
        loop = asyncio.get_event_loop()

        def _cb(event: Event) -> None:
            loop.call_soon_threadsafe(self._queue.put_nowait, event)

        self._callbacks[event_type] = _cb
        return _cb

    def _subscribe_all(self) -> None:
        """Subscribe to all relevant EventBus event types."""
        for et in EVENT_SSE_NAMES:
            self._bus.subscribe(et, self._make_callback(et))

    def _unsubscribe_all(self) -> None:
        """Remove all registered subscriptions."""
        for et, cb in self._callbacks.items():
            self._bus.unsubscribe(et, cb)
        self._callbacks.clear()

    def _format_named_event(self, name: str, data: dict) -> str:
        """Format an SSE event with an explicit ``event:`` field."""
        return f"event: {name}\ndata: {json.dumps(data)}\n\n"

    def _run_agent(self) -> object:
        """Execute the agent synchronously (called via asyncio.to_thread)."""
        ctx = AgentContext()
        # Build conversation context from prior messages
        if len(self._request.messages) > 1:
            from openjarvis.core.types import Message, Role

            for m in self._request.messages[:-1]:
                role = Role(m.role) if m.role in {r.value for r in Role} else Role.USER
                ctx.conversation.add(
                    Message(
                        role=role,
                        content=m.content or "",
                        name=m.name,
                        tool_call_id=m.tool_call_id,
                    )
                )

        input_text = (
            self._request.messages[-1].content if self._request.messages else ""
        )

        # Override agent model for this request if the caller specified one
        original_model = self._agent._model
        if self._model:
            self._agent._model = self._model
        try:
            return self._agent.run(input_text, context=ctx)
        finally:
            self._agent._model = original_model

    # ------------------------------------------------------------------
    # Public streaming interface
    # ------------------------------------------------------------------

    async def stream(self) -> AsyncGenerator[str, None]:
        """Async generator that yields SSE-formatted strings."""
        self._subscribe_all()

        # Upfront escalation (safe — decided BEFORE the agent runs, so no rerun
        # and no double tool side effects). Pick the model by complexity. Fully
        # guarded: any failure falls back to the requested model and never breaks
        # chat. Only escalates to an AVAILABLE stronger target.
        _esc = None
        try:
            import os as _os

            # Hard kill-switch (default OFF). Escalation to a cloud brain on a
            # streaming chat is risky: an ABORTED request leaves the agent thread
            # running (Python can't cancel threads), and if the cloud call hangs
            # — stale/invalid key, network — those threads pile up and exhaust the
            # to_thread pool, wedging the WHOLE backend (every endpoint hangs).
            # Only escalate when explicitly enabled, regardless of inherited keys.
            _esc_on = _os.environ.get("OPENJARVIS_ESCALATION", "0").strip().lower() in (
                "1",
                "true",
                "on",
            )
            if _esc_on:
                from openjarvis.learning.routing.complexity import score_complexity
                from openjarvis.learning.routing.escalation import choose_initial_model

                _input = self._request.messages[-1].content if self._request.messages else ""
                _tier = score_complexity(_input or "").tier
                _pref = _os.environ.get("OPENJARVIS_ESCALATION_PREFERENCE", "balanced")
                _cloud = bool(
                    _os.environ.get("OPENAI_API_KEY")
                    or _os.environ.get("ANTHROPIC_API_KEY")
                    or _os.environ.get("GEMINI_API_KEY")
                    or _os.environ.get("GOOGLE_API_KEY")
                )
                _model, _from, _reason = choose_initial_model(
                    self._model, _tier, _pref, cloud_available=_cloud, remote_available=False
                )
                if _from and _model != self._model:
                    self._model = _model
                    _esc = {"from": _from, "to": _model, "reason": _reason}
        except Exception:
            _esc = None

        # Kick off agent.run() in a background thread (uses the chosen model)
        loop = asyncio.get_event_loop()
        agent_task = asyncio.ensure_future(asyncio.to_thread(self._run_agent))

        def _on_done(fut):
            loop.call_soon_threadsafe(self._queue.put_nowait, _DONE)

        agent_task.add_done_callback(_on_done)

        try:
            # Send initial role chunk (OpenAI-compatible)
            first_chunk = ChatCompletionChunk(
                id=self._chunk_id,
                model=self._model,
                choices=[
                    StreamChoice(
                        delta=DeltaMessage(role="assistant"),
                    )
                ],
            )
            yield f"data: {first_chunk.model_dump_json()}\n\n"

            # Escalation note (if we upgraded the model) + which brain answers.
            from openjarvis.learning.routing.escalation import resolve_brain

            if _esc:
                yield self._format_named_event("escalation", _esc)
            _brain = resolve_brain(self._model)
            yield self._format_named_event(
                "routing",
                {
                    "brain": _brain["brain"],
                    "model": _brain["model"],
                    "lane": _brain["lane"],
                    "health": "ok",
                },
            )

            # Drain queue until the agent finishes
            while True:
                item = await self._queue.get()

                if item is _DONE:
                    break

                if isinstance(item, Event):
                    sse_name = EVENT_SSE_NAMES.get(item.event_type)
                    if sse_name:
                        yield self._format_named_event(sse_name, item.data)

            # Agent is done -- retrieve result
            try:
                agent_result = agent_task.result()
            except Exception as exc:
                import logging

                logger = logging.getLogger("openjarvis.server")
                logger.error("Agent stream error: %s", exc, exc_info=True)

                error_str = str(exc)
                if "context length" in error_str.lower() or (
                    "400" in error_str and "too long" in error_str.lower()
                ):
                    error_content = (
                        "The input is too long for the model's context window. "
                        "Please try a shorter message."
                    )
                elif "400" in error_str:
                    error_content = f"The model returned an error: {error_str}"
                else:
                    error_content = f"Sorry, an error occurred: {error_str}"
                error_chunk = ChatCompletionChunk(
                    id=self._chunk_id,
                    model=self._model,
                    choices=[
                        StreamChoice(
                            delta=DeltaMessage(content=error_content),
                            finish_reason="stop",
                        )
                    ],
                )
                yield f"data: {error_chunk.model_dump_json()}\n\n"
                yield "data: [DONE]\n\n"
                return

            # Emit tool results metadata if any
            tool_results_data = []
            for tr in agent_result.tool_results:
                tool_results_data.append(
                    {
                        "tool_name": tr.tool_name,
                        "success": tr.success,
                        "output": tr.content,
                        "latency_ms": tr.latency_seconds * 1000,
                    }
                )

            if tool_results_data:
                yield self._format_named_event(
                    "tool_results",
                    {"results": tool_results_data},
                )

            # Stream the agent's ACTUAL answer.
            #
            # We deliberately do NOT re-generate from the engine here. The agent
            # has already done the real work this turn — chosen and called tools,
            # read their results, and grounded its answer in them. Re-streaming a
            # fresh generation on only ``self._request.messages`` (the bare user
            # question, with none of this turn's tool calls/results/system prompt)
            # produces a brand-new, ungrounded answer that ignores the tools — the
            # "weather tool succeeds but Jarvis says it has no real-time data" bug.
            # The displayed prose MUST be the agent's own content. Chunk it for a
            # progressive streaming feel (no artificial per-word latency).
            content = agent_result.content or ""
            if content:
                _CHUNK = 24
                for _i in range(0, len(content), _CHUNK):
                    chunk = ChatCompletionChunk(
                        id=self._chunk_id,
                        model=self._model,
                        choices=[
                            StreamChoice(
                                delta=DeltaMessage(content=content[_i : _i + _CHUNK]),
                            )
                        ],
                    )
                    yield f"data: {chunk.model_dump_json()}\n\n"
                    await asyncio.sleep(0.008)

            # Final chunk: finish_reason + usage
            prompt_tokens = agent_result.metadata.get("prompt_tokens", 0)
            completion_tokens = agent_result.metadata.get(
                "completion_tokens",
                0,
            )
            total_tokens = agent_result.metadata.get("total_tokens", 0)
            if total_tokens == 0:
                # Fallback: estimate from request messages (incl. system) + content
                completion_tokens = max(len(content) // 4, 1)
                prompt_tokens = _estimate_prompt_tokens(self._request.messages)
                total_tokens = prompt_tokens + completion_tokens

            final_chunk = ChatCompletionChunk(
                id=self._chunk_id,
                model=self._model,
                choices=[
                    StreamChoice(
                        delta=DeltaMessage(),
                        finish_reason="stop",
                    )
                ],
            )
            final_data = json.loads(final_chunk.model_dump_json())
            final_data["usage"] = UsageInfo(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            ).model_dump()
            yield f"data: {json.dumps(final_data)}\n\n"

            yield "data: [DONE]\n\n"

        except Exception:
            # On error, cancel the agent task if still running
            if not agent_task.done():
                agent_task.cancel()
            raise
        finally:
            self._unsubscribe_all()


async def create_agent_stream(
    agent: BaseAgent,
    bus: EventBus,
    model: str,
    request: ChatCompletionRequest,
) -> StreamingResponse:
    """Create an AgentStreamBridge and return a FastAPI StreamingResponse."""
    bridge = AgentStreamBridge(agent, bus, model, request)
    return StreamingResponse(
        bridge.stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


__all__ = ["AgentStreamBridge", "create_agent_stream"]

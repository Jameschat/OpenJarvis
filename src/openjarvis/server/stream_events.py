"""Canonical SSE event-name constants shared by the backend stream bridge.

The frontend stream reducer (frontend/src/lib/streamReducer.ts) keys on these
exact strings. Keep the two in sync. This module is the single source of truth
for the EventType -> SSE-name mapping.
"""

from __future__ import annotations

from openjarvis.core.events import EventType

# Existing events
SSE_AGENT_TURN_START = "agent_turn_start"
SSE_INFERENCE_START = "inference_start"
SSE_INFERENCE_END = "inference_end"
SSE_TOOL_CALL_START = "tool_call_start"
SSE_TOOL_CALL_END = "tool_call_end"

# New events (emitted by later phases)
SSE_PLAN = "plan"
SSE_THINKING_DELTA = "thinking_delta"
SSE_FILE_EDIT = "file_edit"
SSE_ESCALATION = "escalation"
SSE_ROUTING = "routing"
SSE_CITATION = "citation"
SSE_VERIFICATION = "verification"

EVENT_SSE_NAMES: dict[EventType, str] = {
    EventType.AGENT_TURN_START: SSE_AGENT_TURN_START,
    EventType.INFERENCE_START: SSE_INFERENCE_START,
    EventType.INFERENCE_END: SSE_INFERENCE_END,
    EventType.TOOL_CALL_START: SSE_TOOL_CALL_START,
    EventType.TOOL_CALL_END: SSE_TOOL_CALL_END,
    EventType.PLAN_UPDATE: SSE_PLAN,
    EventType.THINKING_DELTA: SSE_THINKING_DELTA,
    EventType.FILE_EDIT: SSE_FILE_EDIT,
    EventType.ESCALATION: SSE_ESCALATION,
    EventType.ROUTING: SSE_ROUTING,
    EventType.CITATION: SSE_CITATION,
    EventType.VERIFICATION: SSE_VERIFICATION,
}

__all__ = [
    "SSE_AGENT_TURN_START", "SSE_INFERENCE_START", "SSE_INFERENCE_END",
    "SSE_TOOL_CALL_START", "SSE_TOOL_CALL_END", "SSE_PLAN",
    "SSE_THINKING_DELTA", "SSE_FILE_EDIT", "SSE_ESCALATION", "SSE_ROUTING",
    "SSE_CITATION", "SSE_VERIFICATION", "EVENT_SSE_NAMES",
]

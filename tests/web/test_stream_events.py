from openjarvis.core.events import EventType
from openjarvis.server import stream_events as se


def test_sse_name_constants_present():
    # Canonical SSE event-name strings the frontend reducer keys on.
    assert se.SSE_PLAN == "plan"
    assert se.SSE_THINKING_DELTA == "thinking_delta"
    assert se.SSE_FILE_EDIT == "file_edit"
    assert se.SSE_ESCALATION == "escalation"
    assert se.SSE_ROUTING == "routing"
    assert se.SSE_CITATION == "citation"
    assert se.SSE_VERIFICATION == "verification"
    assert se.SSE_TOOL_CALL_START == "tool_call_start"
    assert se.SSE_TOOL_CALL_END == "tool_call_end"


def test_event_map_covers_all_streamed_types_and_resolves():
    # Every EventType in the map must exist and map to a non-empty SSE name.
    for et, name in se.EVENT_SSE_NAMES.items():
        assert isinstance(et, EventType)
        assert isinstance(name, str) and name


def test_new_event_types_are_mapped():
    for et in (
        EventType.PLAN_UPDATE,
        EventType.THINKING_DELTA,
        EventType.FILE_EDIT,
        EventType.ESCALATION,
        EventType.ROUTING,
        EventType.CITATION,
        EventType.VERIFICATION,
    ):
        assert et in se.EVENT_SSE_NAMES

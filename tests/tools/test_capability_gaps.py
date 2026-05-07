import json

from openjarvis.cli import tool_use
from openjarvis.tools import capability_gaps


def test_record_gap_writes_structured_json(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_LEARNING_HOME", str(tmp_path))

    path = capability_gaps.record_gap(
        capability="debug iOS simulator UI",
        trigger="operator asked to inspect simulator",
        context="no simulator inspection tool was available",
        severity="medium",
    )

    assert path is not None
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["type"] == "capability-gap"
    assert data["capability"] == "debug iOS simulator UI"
    assert data["severity"] == "medium"
    assert data["status"] == "open"
    assert data["gap_id"].startswith("g_")


def test_recent_gaps_returns_newest_first(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_LEARNING_HOME", str(tmp_path))

    capability_gaps.record_gap("first gap", "trigger", "context")
    capability_gaps.record_gap("second gap", "trigger", "context")

    gaps = capability_gaps.recent_gaps(window_days=7)
    assert [g["capability"] for g in gaps] == ["second gap", "first gap"]


def test_tool_use_record_capability_gap(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_LEARNING_HOME", str(tmp_path))

    raw = tool_use._tool_record_capability_gap(
        capability="control a 3D printer",
        trigger="operator asked Jarvis to print a part",
        context="no printer integration exists",
        severity="high",
    )

    data = json.loads(raw)
    assert data["ok"] is True
    assert data["capability"] == "control a 3D printer"

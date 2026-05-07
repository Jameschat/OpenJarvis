import json

from openjarvis.tools import capability_queue


def test_build_queue_prioritizes_repeated_and_high_severity_gaps():
    gap_summary = {
        "total": 4,
        "repeated": [{"capability": "inspect mobile UI", "count": 3}],
        "recent": [
            {
                "capability": "inspect mobile UI",
                "trigger": "operator asked to inspect phone HUD",
                "severity": "medium",
            },
            {
                "capability": "control live payments",
                "trigger": "operator asked Jarvis to pay an invoice",
                "severity": "high",
            },
            {
                "capability": "summarize PDFs",
                "trigger": "operator asked for PDF summary",
                "severity": "low",
            },
        ],
    }

    queue = capability_queue.build_queue(gap_summary)

    assert [item["capability"] for item in queue[:2]] == [
        "inspect mobile UI",
        "control live payments",
    ]
    assert queue[0]["action"] == "scout"
    assert queue[0]["next_agent"] == "capability-scout"
    assert queue[0]["priority"] > queue[-1]["priority"]


def test_build_queue_uses_scout_recommendation_for_next_action():
    gap_summary = {
        "total": 1,
        "repeated": [],
        "recent": [
            {
                "capability": "browser automation",
                "trigger": "operator asked for website inspection",
                "severity": "medium",
            }
        ],
    }
    scout_results = {
        "browser automation": {
            "best_name": "browser-use/browser-use",
            "recommendation": "prototype first",
            "score": 88,
            "source": "https://github.com/browser-use/browser-use",
        }
    }

    queue = capability_queue.build_queue(gap_summary, scout_results=scout_results)

    assert queue[0]["action"] == "prototype"
    assert queue[0]["next_agent"] == "architect"
    assert queue[0]["scout"]["best_name"] == "browser-use/browser-use"


def test_write_queue_json_persists_ranked_items(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_LEARNING_HOME", str(tmp_path))
    items = [
        {
            "capability": "browser automation",
            "priority": 72,
            "action": "scout",
            "next_agent": "capability-scout",
        }
    ]

    path = capability_queue.write_queue_json(items, date_str="2026-05-07")

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["type"] == "capability-queue"
    assert data["date"] == "2026-05-07"
    assert data["items"][0]["capability"] == "browser automation"


def test_build_queue_report_renders_guardrail():
    items = [
        {
            "capability": "browser automation",
            "priority": 72,
            "action": "scout",
            "next_agent": "capability-scout",
            "reason": "medium severity; 2 occurrences",
        }
    ]

    md = capability_queue.build_queue_report(items, date_str="2026-05-07")

    assert "# Jarvis capability queue - 2026-05-07" in md
    assert "browser automation" in md
    assert "must not install" in md


def test_capability_queue_agent_registered():
    from openjarvis.tools import agent_runner

    agents = {a["id"]: a for a in agent_runner.list_agents()}
    assert agents["capability-queue"]["provider"] == "python"
    assert (
        agents["capability-queue"]["python_entry"]
        == "openjarvis.tools.capability_queue:run_as_agent_task"
    )

from openjarvis.tools import learning_reviewer


def test_build_learning_digest_mentions_failures_and_gaps():
    outcomes = [
        {
            "type": "agent-task",
            "agent_id": "browser-pilot",
            "status": "failed",
            "error": "fetch_url could not inspect JS-heavy app",
            "prompt_summary": "research local app",
        }
    ]
    gaps = {
        "total": 1,
        "repeated": [],
        "recent": [
            {
                "capability": "inspect JS-heavy app",
                "trigger": "operator asked for browser inspection",
                "severity": "medium",
            }
        ],
    }

    md = learning_reviewer.build_learning_digest(
        date_str="2026-05-07",
        outcomes=outcomes,
        gap_summary=gaps,
    )

    assert "# Jarvis learning digest - 2026-05-07" in md
    assert "browser-pilot" in md
    assert "inspect JS-heavy app" in md


def test_learning_reviewer_agent_registered():
    from openjarvis.tools import agent_runner

    agents = {a["id"]: a for a in agent_runner.list_agents()}
    assert agents["learning-reviewer"]["provider"] == "python"
    assert (
        agents["learning-reviewer"]["python_entry"]
        == "openjarvis.tools.learning_reviewer:run_as_agent_task"
    )


def test_learning_digest_names_capability_scout_for_repeated_gaps():
    md = learning_reviewer.build_learning_digest(
        date_str="2026-05-07",
        outcomes=[],
        gap_summary={
            "total": 2,
            "repeated": [{"capability": "inspect local mobile UI", "count": 2}],
            "recent": [],
        },
    )

    assert "capability-scout" in md
    assert "inspect local mobile UI" in md

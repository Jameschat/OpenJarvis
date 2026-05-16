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


def test_learning_digest_flags_repeated_agent_failures():
    """Two+ failures of the same agent_id in the window should surface as a
    repeated-failure pattern with an explicit fix-the-underlying-cause action,
    even when no formal capability gap was recorded."""
    outcomes = [
        {
            "type": "agent-task",
            "agent_id": "financial-researcher",
            "status": "failed",
            "error": "no historical bars in store — run backfill first",
            "prompt_summary": "06:15 markets brief",
        },
        {
            "type": "agent-task",
            "agent_id": "financial-researcher",
            "status": "failed",
            "error": "no historical bars in store — run backfill first",
            "prompt_summary": "06:15 markets brief",
        },
        {
            "type": "agent-task",
            "agent_id": "financial-researcher",
            "status": "failed",
            "error": "no historical bars in store — run backfill first",
            "prompt_summary": "06:15 markets brief",
        },
        # An unrelated single failure should NOT appear in repeated-failures.
        {
            "type": "agent-task",
            "agent_id": "ai-researcher",
            "status": "failed",
            "error": "github API rate-limited",
            "prompt_summary": "AI pulse",
        },
    ]
    gaps = {"total": 0, "repeated": [], "recent": []}

    md = learning_reviewer.build_learning_digest(
        date_str="2026-05-08",
        outcomes=outcomes,
        gap_summary=gaps,
    )

    assert "## Repeated Failures" in md
    assert "**financial-researcher** failed 3x" in md
    # The unrelated single failure does not get a repeated-failures line.
    assert "**ai-researcher** failed" not in md.split("## Repeated Failures")[1].split("##")[0]
    # Recommended action surfaces the underlying cause and the agent.
    assert "Investigate `financial-researcher`" in md
    assert "run backfill first" in md


def test_learning_digest_no_repeated_failures_when_each_unique():
    """A single failure per agent should not trip repeated-failure detection."""
    outcomes = [
        {"type": "agent-task", "agent_id": "a", "status": "failed", "error": "x"},
        {"type": "agent-task", "agent_id": "b", "status": "failed", "error": "y"},
    ]
    md = learning_reviewer.build_learning_digest(
        date_str="2026-05-08",
        outcomes=outcomes,
        gap_summary={"total": 0, "repeated": [], "recent": []},
    )
    assert "No agent failed twice or more" in md


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

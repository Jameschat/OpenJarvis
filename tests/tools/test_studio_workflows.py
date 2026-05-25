from openjarvis.tools import studio_workflows


def test_selector_routes_bug_to_debug():
    decision = studio_workflows.select_workflow(
        "Fix the DCA backtest HTTP 500 and add a regression test"
    )

    assert decision["workflow"] == "debug"
    assert decision["verification"]["required"] is True
    assert "reproduce" in decision["next_steps"][0].lower()


def test_selector_routes_research_to_qwen_workflow():
    decision = studio_workflows.select_workflow(
        "Research the best tools for local Qwen agent memory"
    )

    assert decision["workflow"] == "qwen_workflow"
    assert decision["model"] == "qwen3.6-27b-local"


def test_selector_routes_large_build_to_spec():
    decision = studio_workflows.select_workflow(
        "Build a complete Codex replica with projects, plugins, automations, memory, and task loops"
    )

    assert decision["workflow"] == "spec"
    assert decision["requires_operator_approval"] is True


def test_selector_marks_external_mutation_for_approval():
    decision = studio_workflows.select_workflow(
        "Install this package and connect my exchange account"
    )

    assert decision["requires_operator_approval"] is True
    assert any(
        "external" in item.lower() or "account" in item.lower()
        for item in decision["risks"]
    )

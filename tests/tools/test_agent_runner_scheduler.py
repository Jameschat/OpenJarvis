from datetime import datetime
from types import SimpleNamespace


def test_next_future_run_at_rolls_past_missed_daily_runs():
    from openjarvis.tools import agent_runner

    next_at = agent_runner._next_future_run_at(
        "2026-05-10T02:00:00",
        "daily",
        datetime.fromisoformat("2026-05-12T18:49:00"),
    )

    assert next_at == "2026-05-13T02:00:00"


def test_should_skip_missed_recurring_only_when_catch_up_disabled():
    from openjarvis.tools import agent_runner

    assert agent_runner._should_skip_missed_recurring(
        {"recurrence": "daily", "catch_up": False}
    )
    assert not agent_runner._should_skip_missed_recurring(
        {"recurrence": "daily", "catch_up": True}
    )
    assert not agent_runner._should_skip_missed_recurring(
        {"recurrence": "once", "catch_up": False}
    )


def test_brain_context_includes_handoff_and_active_project_files(monkeypatch, tmp_path):
    from openjarvis.tools import agent_runner

    brain = tmp_path / "Brain"
    project = brain / "Projects" / "openjarvis"
    project.mkdir(parents=True)
    (brain / "00 Session Handoff.md").write_text("# Handoff\n\nShared current state", encoding="utf-8")
    (project / "STATE.md").write_text("# State\n\nCurrent task", encoding="utf-8")
    (project / "CONTEXT.md").write_text("# Context\n\nStable paths", encoding="utf-8")
    (project / "ROADMAP.md").write_text("# Roadmap\n\nPhase 2", encoding="utf-8")

    import openjarvis.tools

    fake_ob = SimpleNamespace(DEFAULT_VAULT=brain, BRAIN_ROOT=brain)
    monkeypatch.setitem(__import__("sys").modules, "openjarvis.tools.obsidian_brain", fake_ob)
    monkeypatch.setattr(openjarvis.tools, "obsidian_brain", fake_ob, raising=False)
    monkeypatch.setattr(agent_runner, "PROJECTS_ROOT", brain / "Projects")

    context = agent_runner._build_brain_context()

    assert "00 Session Handoff.md" in context
    assert "Shared current state" in context
    assert "STATE.md" in context
    assert "CONTEXT.md" in context
    assert "ROADMAP.md" in context
    assert "Phase 2" in context

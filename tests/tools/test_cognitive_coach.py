from __future__ import annotations

import json


def test_cognitive_check_uses_memory_and_challenges_assumptions(monkeypatch):
    from openjarvis.tools import cognitive_coach

    monkeypatch.setattr(
        cognitive_coach,
        "_vault_hits",
        lambda prompt, limit=4: [
            {"source": "vault", "path": "Decisions/test.md", "snippet": "We decided to use paper trading before live execution."}
        ],
    )
    monkeypatch.setattr(cognitive_coach, "_episodic_hits", lambda prompt, limit=3: [])
    monkeypatch.setattr(cognitive_coach, "_codegraph_signal", lambda prompt: None)

    result = cognitive_coach.cognitive_check(
        "Should we build live trading bots now?",
        mode="decision",
        stakes="high",
    )

    assert result["ok"] is True
    assert result["mode"] == "decision"
    assert result["memory_signals"][0]["source"] == "vault"
    assert "smallest reversible decision" in result["better_question"]
    assert any("Backtest" in item for item in result["assumption_checks"])
    assert "decision note" in result["next_action"]


def test_cognitive_check_includes_codegraph_signal_for_code_questions(monkeypatch):
    from openjarvis.tools import cognitive_coach

    monkeypatch.setattr(cognitive_coach, "_vault_hits", lambda prompt, limit=4: [])
    monkeypatch.setattr(cognitive_coach, "_episodic_hits", lambda prompt, limit=3: [])
    monkeypatch.setattr(
        cognitive_coach,
        "_codegraph_signal",
        lambda prompt: {"source": "codegraph", "online": True, "files": 10, "nodes": 20, "edges": 30},
    )

    result = cognitive_coach.cognitive_check("Make this Jarvis module better", mode="plan")

    assert any(item["source"] == "codegraph" for item in result["memory_signals"])
    assert "CodeGraph" in result["next_action"]


def test_daily_review_writes_cognitive_note(tmp_path, monkeypatch):
    from openjarvis.tools import cognitive_coach

    brain = tmp_path / "Brain"
    daily = brain / "Daily"
    daily.mkdir(parents=True)
    (daily / "2026-05-21.md").write_text(
        "# Day\n\nWe should evolve Jarvis memory into better decision coaching. This needs a safe first test.",
        encoding="utf-8",
    )

    from openjarvis.tools import obsidian_brain
    monkeypatch.setattr(obsidian_brain, "BRAIN_ROOT", brain)
    md = cognitive_coach.build_daily_review("2026-05-21")

    assert "# Jarvis cognitive review - 2026-05-21" in md
    assert "Tomorrow's Thinking Rule" in md


def test_cognitive_coach_agent_registered():
    from openjarvis.tools import agent_runner

    agents = {a["id"]: a for a in agent_runner.list_agents()}
    assert agents["cognitive-coach"]["provider"] == "python"
    assert agents["cognitive-coach"]["python_entry"] == "openjarvis.tools.cognitive_coach:run_as_agent_task"


def test_tool_use_cognitive_check_schema_and_dispatch(monkeypatch):
    from openjarvis.cli import tool_use

    names = [s["function"]["name"] for s in tool_use.TOOL_SCHEMAS]
    assert "cognitive_check" in names
    assert "cognitive_check" in tool_use._TOOL_DISPATCH

    monkeypatch.setattr(
        "openjarvis.tools.cognitive_coach.cognitive_check",
        lambda prompt, mode="coach", stakes="medium": {"ok": True, "prompt_summary": prompt, "mode": mode, "stakes": stakes},
    )
    result = json.loads(tool_use._tool_cognitive_check("pressure test this", mode="pressure_test", stakes="high"))

    assert result["ok"] is True
    assert result["mode"] == "pressure_test"
    assert result["stakes"] == "high"

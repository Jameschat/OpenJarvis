"""Tests for the proactive morning briefing (Phase 7 #4)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from openjarvis.tools import morning_briefing


@pytest.fixture()
def homes(tmp_path, monkeypatch):
    vault = tmp_path / "Brain"
    (vault / "Daily").mkdir(parents=True)
    (vault / "Study" / "web-dev").mkdir(parents=True)
    monkeypatch.setattr(morning_briefing, "_vault_root", lambda: vault)
    monkeypatch.setenv("OPENJARVIS_OUTCOMES_HOME", str(tmp_path / "outcomes"))
    monkeypatch.setattr(
        morning_briefing, "_WATCHDOG_REPORT", tmp_path / "watchdog_last.json"
    )
    monkeypatch.setattr(
        morning_briefing, "_SHADOW_DIR", tmp_path / "routing_shadow"
    )
    return tmp_path


def _outcome(root: Path, agent_id: str, status: str, *, title="task", escalated_to=None, n=0):
    day = root / "outcomes" / time.strftime("%Y-%m-%d")
    day.mkdir(parents=True, exist_ok=True)
    rec = {
        "type": "agent-task",
        "task_id": f"t_{agent_id}_{n}",
        "agent_id": agent_id,
        "status": status,
        "title": title,
        "escalated_to": escalated_to,
        "ended_at": time.time() - 3600,
        "duration_seconds": 5.0,
    }
    (day / f"t_{agent_id}_{n}.json").write_text(json.dumps(rec), encoding="utf-8")


class TestSections:
    def test_outcomes_section_counts_and_failures(self, homes):
        for i in range(3):
            _outcome(homes, "qwen-builder", "done", n=i)
        _outcome(homes, "browser-pilot", "failed", title="Scrape thing", n=9)
        _outcome(homes, "qwen-builder", "done", escalated_to="t_child", title="Hard fix", n=8)
        md = morning_briefing.section_outcomes()
        assert "5 task" in md
        assert "4 done" in md and "1 failed" in md
        assert "Scrape thing" in md
        assert "Hard fix" in md and "escalat" in md.lower()

    def test_runtime_section_reads_watchdog_report(self, homes):
        morning_briefing._WATCHDOG_REPORT.write_text(
            json.dumps({"ok": True, "action": "none",
                        "summary": "Jarvis runtime ready: required services are online.",
                        "probe": {"ok": True, "content": "size-ok"}}),
            encoding="utf-8",
        )
        md = morning_briefing.section_runtime()
        assert "ready" in md
        assert "size-ok" in md

    def test_runtime_section_survives_missing_report(self, homes):
        md = morning_briefing.section_runtime()
        assert "no watchdog report" in md.lower()

    def test_shadow_section_counts_disagreements(self, homes):
        d = morning_briefing._SHADOW_DIR
        d.mkdir(parents=True)
        rows = [
            {"configured": "backend-dev", "recommended": "backend-dev", "would_differ": False},
            {"configured": "backend-dev", "recommended": "gpt-backend", "would_differ": True},
        ]
        (d / (time.strftime("%Y-%m-%d") + ".jsonl")).write_text(
            "\n".join(json.dumps(r) for r in rows), encoding="utf-8"
        )
        md = morning_briefing.section_shadow_routing()
        assert "2" in md and "1" in md

    def test_study_section_finds_latest_note(self, homes):
        note = morning_briefing._vault_root() / "Study" / "web-dev" / "2026-06-12 - css-grid.md"
        note.write_text("# grid", encoding="utf-8")
        md = morning_briefing.section_study()
        assert "css-grid" in md


class TestComposeAndRun:
    def test_compose_includes_all_sections_and_skips_none(self):
        md = morning_briefing.compose_briefing(
            {"Runtime": "all green", "Markets": None, "Overnight tasks": "5 tasks"}
        )
        assert "Runtime" in md and "all green" in md
        assert "Overnight tasks" in md
        assert "Markets" not in md
        assert md.startswith("#")

    def test_run_writes_daily_note_and_pushes_chat(self, homes, monkeypatch):
        pushed = []
        monkeypatch.setattr(morning_briefing, "_push_chat", lambda msg: pushed.append(msg) or True)
        _outcome(homes, "qwen-builder", "done", n=0)
        result = morning_briefing.run_as_agent_task()
        assert result["ok"] is True
        note = Path(result["note_path"])
        assert note.exists()
        assert "Morning briefing" in note.read_text(encoding="utf-8")
        assert len(pushed) == 1
        assert "briefing" in pushed[0].lower()

    def test_run_never_raises_on_broken_sources(self, homes, monkeypatch):
        monkeypatch.setattr(
            morning_briefing, "section_runtime",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(morning_briefing, "_push_chat", lambda msg: True)
        result = morning_briefing.run_as_agent_task()
        assert result["ok"] is True  # one broken section never sinks the brief

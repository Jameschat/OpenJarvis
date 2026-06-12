"""Tests for outcome-driven routing, shadow mode (Phase 7 #2)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjarvis.tools import outcome_router


def _write_outcome(root: Path, agent_id: str, status: str, duration: float = 10.0,
                   ts: float | None = None, n: int = 0) -> None:
    ts = ts or time.time()
    day = root / time.strftime("%Y-%m-%d", time.localtime(ts))
    day.mkdir(parents=True, exist_ok=True)
    rec = {
        "type": "agent-task",
        "task_id": f"t_{agent_id}_{status}_{n}",
        "agent_id": agent_id,
        "status": status,
        "duration_seconds": duration,
        "ended_at": ts,
    }
    (day / f"t_{agent_id}_{status}_{n}.json").write_text(
        json.dumps(rec), encoding="utf-8"
    )


@pytest.fixture()
def outcomes_home(tmp_path, monkeypatch):
    root = tmp_path / "outcomes"
    monkeypatch.setenv("OPENJARVIS_OUTCOMES_HOME", str(root))
    monkeypatch.setattr(outcome_router, "_SHADOW_DIR", tmp_path / "routing_shadow")
    return root


class TestCandidateStats:
    def test_aggregates_success_rate_and_duration(self, outcomes_home):
        for i in range(4):
            _write_outcome(outcomes_home, "backend-dev", "done", duration=20, n=i)
        _write_outcome(outcomes_home, "backend-dev", "failed", duration=5, n=9)
        stats = outcome_router.candidate_stats(["backend-dev"])
        s = stats["backend-dev"]
        assert s["total"] == 5
        assert s["succeeded"] == 4
        assert s["success_rate"] == pytest.approx(0.8)
        assert s["avg_duration_s"] == pytest.approx(17.0)

    def test_unknown_agent_has_zero_total(self, outcomes_home):
        stats = outcome_router.candidate_stats(["ghost-agent"])
        assert stats["ghost-agent"]["total"] == 0


class TestRecommendAgent:
    def test_picks_highest_success_rate_with_enough_samples(self, outcomes_home):
        for i in range(5):
            _write_outcome(outcomes_home, "backend-dev", "done", n=i)
        for i in range(4):
            _write_outcome(outcomes_home, "gpt-backend", "done", n=i)
        _write_outcome(outcomes_home, "gpt-backend", "failed", n=9)
        rec = outcome_router.recommend_agent(["gpt-backend", "backend-dev"])
        assert rec.agent_id == "backend-dev"
        assert "success" in rec.reason

    def test_insufficient_data_keeps_first_candidate(self, outcomes_home):
        _write_outcome(outcomes_home, "backend-dev", "done", n=0)
        rec = outcome_router.recommend_agent(["gpt-backend", "backend-dev"])
        assert rec.agent_id == "gpt-backend"
        assert "insufficient" in rec.reason

    def test_tie_on_success_rate_prefers_faster_agent(self, outcomes_home):
        for i in range(5):
            _write_outcome(outcomes_home, "backend-dev", "done", duration=60, n=i)
        for i in range(5):
            _write_outcome(outcomes_home, "gpt-backend", "done", duration=10, n=i)
        rec = outcome_router.recommend_agent(["backend-dev", "gpt-backend"])
        assert rec.agent_id == "gpt-backend"

    def test_min_samples_respected(self, outcomes_home):
        # perfect record but only 2 samples — below the default threshold
        for i in range(2):
            _write_outcome(outcomes_home, "gpt-backend", "done", n=i)
        for i in range(5):
            _write_outcome(outcomes_home, "backend-dev", "done", n=i)
        _write_outcome(outcomes_home, "backend-dev", "failed", n=9)
        rec = outcome_router.recommend_agent(["gpt-backend", "backend-dev"])
        assert rec.agent_id == "backend-dev"


class TestShadowLog:
    def test_shadow_escalation_logs_decision(self, outcomes_home):
        for i in range(5):
            _write_outcome(outcomes_home, "gpt-backend", "done", n=i)
        for i in range(3):
            _write_outcome(outcomes_home, "backend-dev", "failed", n=i)
        for i in range(3):
            _write_outcome(outcomes_home, "backend-dev", "done", n=i + 10)
        task = SimpleNamespace(id="t-orig")
        rec = outcome_router.shadow_route_escalation(task, "backend-dev")
        assert rec.agent_id == "gpt-backend"

        files = list(outcome_router._SHADOW_DIR.glob("*.jsonl"))
        assert len(files) == 1
        row = json.loads(files[0].read_text(encoding="utf-8").strip().splitlines()[-1])
        assert row["task_id"] == "t-orig"
        assert row["configured"] == "backend-dev"
        assert row["recommended"] == "gpt-backend"
        assert row["would_differ"] is True
        assert row["mode"] == "shadow"

    def test_shadow_never_raises(self, outcomes_home, monkeypatch):
        monkeypatch.setattr(
            outcome_router, "recommend_agent",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        rec = outcome_router.shadow_route_escalation(SimpleNamespace(id="t"), "backend-dev")
        assert rec is None

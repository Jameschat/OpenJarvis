"""Tests for study_agent: topic picker + state I/O.

The picker is the brain of the study-agent — it decides what to study next.
TDD because the rules around revisit-window + last-studied-time are subtle.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from openjarvis.tools import study_agent


CURRICULUM_BODY = """\
---
discipline: web-dev
revisit_after_days: 30
---

# Web Dev Curriculum

1. semantic-html-landmarks: header/nav/main/section/article/aside
2. css-grid-vs-flexbox: decision tree
3. react-server-components: RSC mental model
"""


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    """A throwaway vault layout matching the production one."""
    curriculum = tmp_path / "Curriculum"
    curriculum.mkdir()
    (curriculum / "web-dev.md").write_text(CURRICULUM_BODY, encoding="utf-8")
    return tmp_path


def test_pick_returns_first_unvisited_when_state_empty(vault_root: Path):
    now = datetime(2026, 5, 9, 2, 0, tzinfo=timezone.utc)
    topic = study_agent.pick_next_topic("web-dev", vault_root, now=now)
    assert topic is not None
    assert topic.slug == "semantic-html-landmarks"
    assert topic.discipline == "web-dev"
    assert topic.status == "unvisited"
    assert topic.description == "header/nav/main/section/article/aside"


def test_pick_skips_already_studied_within_revisit_window(vault_root: Path):
    now = datetime(2026, 5, 9, 2, 0, tzinfo=timezone.utc)
    # First topic studied yesterday — well within 30-day revisit window.
    state = {
        "version": 1,
        "disciplines": {
            "web-dev": {
                "studied": {
                    "semantic-html-landmarks": "2026-05-08T02:00:00+00:00"
                },
                "last_rotation_index": 0,
            }
        },
        "rotation_history": [],
    }
    (vault_root / "Curriculum" / "_state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )
    topic = study_agent.pick_next_topic("web-dev", vault_root, now=now)
    assert topic is not None
    assert topic.slug == "css-grid-vs-flexbox"
    assert topic.status == "unvisited"


def test_pick_returns_oldest_revisit_when_all_studied(vault_root: Path):
    now = datetime(2026, 7, 1, 2, 0, tzinfo=timezone.utc)
    state = {
        "version": 1,
        "disciplines": {
            "web-dev": {
                "studied": {
                    "semantic-html-landmarks": "2026-05-01T02:00:00+00:00",  # oldest
                    "css-grid-vs-flexbox":     "2026-05-15T02:00:00+00:00",
                    "react-server-components": "2026-05-30T02:00:00+00:00",
                },
                "last_rotation_index": 0,
            }
        },
        "rotation_history": [],
    }
    (vault_root / "Curriculum" / "_state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )
    topic = study_agent.pick_next_topic("web-dev", vault_root, now=now)
    assert topic is not None
    assert topic.slug == "semantic-html-landmarks"
    assert topic.status == "revisit"


def test_pick_returns_none_when_all_studied_and_within_revisit_window(vault_root: Path):
    """All topics visited, none old enough to revisit yet → caller should rotate."""
    now = datetime(2026, 5, 9, 2, 0, tzinfo=timezone.utc)
    state = {
        "version": 1,
        "disciplines": {
            "web-dev": {
                "studied": {
                    "semantic-html-landmarks": "2026-05-08T02:00:00+00:00",
                    "css-grid-vs-flexbox":     "2026-05-08T02:00:00+00:00",
                    "react-server-components": "2026-05-08T02:00:00+00:00",
                },
                "last_rotation_index": 0,
            }
        },
        "rotation_history": [],
    }
    (vault_root / "Curriculum" / "_state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )
    topic = study_agent.pick_next_topic("web-dev", vault_root, now=now)
    assert topic is None


def test_pick_returns_none_for_missing_curriculum(tmp_path: Path):
    (tmp_path / "Curriculum").mkdir()
    topic = study_agent.pick_next_topic("game-dev", tmp_path, now=datetime.now(timezone.utc))
    assert topic is None


def test_state_round_trip(vault_root: Path):
    """Writing and reading state preserves all fields."""
    now = datetime(2026, 5, 9, 2, 0, tzinfo=timezone.utc)
    study_agent.record_studied(
        discipline="web-dev",
        topic_slug="semantic-html-landmarks",
        vault_root=vault_root,
        now=now,
    )
    state = study_agent.load_state(vault_root)
    assert "web-dev" in state["disciplines"]
    assert state["disciplines"]["web-dev"]["studied"]["semantic-html-landmarks"].startswith("2026-05-09")
    assert len(state["rotation_history"]) == 1
    assert state["rotation_history"][0]["topic"] == "semantic-html-landmarks"


def test_gpu_busy_returns_false_when_nvidia_smi_missing(monkeypatch):
    """If nvidia-smi isn't on PATH (no NVIDIA driver / non-GPU host),
    treat as 'not busy' so the study agent can run anywhere for testing."""
    import subprocess
    def fake_run(*a, **kw):
        raise FileNotFoundError("nvidia-smi not found")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert study_agent.is_gpu_busy(threshold_pct=80) is False


def test_gpu_busy_true_when_vram_above_threshold(monkeypatch):
    import subprocess
    class _FakeResult:
        returncode = 0
        # 22000 MiB used out of 24576 MiB total = 89%
        stdout = "22000, 24576\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _FakeResult())
    assert study_agent.is_gpu_busy(threshold_pct=80) is True


def test_gpu_busy_false_when_vram_below_threshold(monkeypatch):
    import subprocess
    class _FakeResult:
        returncode = 0
        stdout = "5000, 24576\n"   # 20%
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _FakeResult())
    assert study_agent.is_gpu_busy(threshold_pct=80) is False

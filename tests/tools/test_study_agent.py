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


def test_build_study_prompt_includes_discipline_and_topic():
    topic = study_agent.Topic(
        discipline="web-dev",
        slug="semantic-html-landmarks",
        description="header/nav/main/section/article/aside",
        status="unvisited",
    )
    prompt = study_agent.build_study_prompt(topic)
    # Disciplines map to specific guidance — web-dev should mention MDN.
    assert "web-dev" in prompt or "web development" in prompt.lower()
    assert "semantic-html-landmarks" in prompt
    assert "MDN" in prompt
    # The output contract must be in the prompt — qwen3 needs to know
    # the markdown structure required.
    assert "## Key concepts" in prompt
    assert "## Worked example" in prompt
    assert "## Common pitfalls" in prompt
    assert "self-grade" in prompt.lower()


def test_build_study_prompt_revisit_status_changes_framing():
    topic = study_agent.Topic(
        discipline="web-dev",
        slug="semantic-html-landmarks",
        description="header/nav/main/section/article/aside",
        status="revisit",
    )
    prompt = study_agent.build_study_prompt(topic)
    # Revisit sessions should ask for newer/deeper material, not a repeat.
    assert "revisit" in prompt.lower() or "what's changed" in prompt.lower()


def test_write_study_note_creates_file_at_expected_path(vault_root):
    topic = study_agent.Topic(
        discipline="web-dev",
        slug="semantic-html-landmarks",
        description="header/nav/main/section/article/aside",
        status="unvisited",
    )
    qwen_response = (
        "## Key concepts\n- ...\n\n## Worked example\n```html\n...\n```\n\n"
        "## Common pitfalls\n- ...\n\n## Self-grade and reasoning\n4/5 — solid.\n\n"
        "## References\n- https://developer.mozilla.org/...\n"
    )
    now = datetime(2026, 5, 9, 2, 0, tzinfo=timezone.utc)
    path = study_agent.write_study_note(
        topic=topic,
        body=qwen_response,
        vault_root=vault_root,
        now=now,
    )
    assert path.exists()
    assert path.name == "2026-05-09 - semantic-html-landmarks.md"
    assert path.parent.name == "web-dev"
    content = path.read_text(encoding="utf-8")
    assert "type: study" in content
    assert "discipline: web-dev" in content
    assert "topic: semantic-html-landmarks" in content
    assert "model: qwen3:32b" in content
    assert "self_grade: 4" in content   # parsed from "4/5 — solid"
    assert "## Key concepts" in content


def test_write_study_note_self_grade_defaults_when_unparseable(vault_root):
    topic = study_agent.Topic(
        discipline="web-dev",
        slug="semantic-html-landmarks",
        description="...",
        status="unvisited",
    )
    body_without_grade = "## Key concepts\n- ...\n\n## References\n- ..."
    now = datetime(2026, 5, 9, 2, 0, tzinfo=timezone.utc)
    path = study_agent.write_study_note(
        topic=topic, body=body_without_grade, vault_root=vault_root, now=now,
    )
    content = path.read_text(encoding="utf-8")
    assert "self_grade: 0" in content   # 0 = unparseable, audit will flag

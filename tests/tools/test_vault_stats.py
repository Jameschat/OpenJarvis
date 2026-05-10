"""Tests for vault_stats: at-a-glance vault summary for the HUD button.

Pure-logic — uses tmp_path fixtures, no real vault touch.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openjarvis.tools import vault_stats


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A throwaway vault layout with a few notes spread across folders."""
    brain = tmp_path / "Brain"
    (brain / "Knowledge").mkdir(parents=True)
    (brain / "Decisions").mkdir(parents=True)
    (brain / "Daily").mkdir(parents=True)
    (brain / "Knowledge" / "a.md").write_text("# a\n" + "x" * 100, encoding="utf-8")
    (brain / "Knowledge" / "b.md").write_text("# b\n" + "y" * 200, encoding="utf-8")
    (brain / "Decisions" / "c.md").write_text("# c\n", encoding="utf-8")
    return brain


def test_summary_counts_all_md_files(vault: Path):
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    s = vault_stats.summary(vault, now=now)
    assert s["total_notes"] == 3


def test_summary_sums_bytes(vault: Path):
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    s = vault_stats.summary(vault, now=now)
    # Each file's content is small but non-zero. Just assert >0 and reasonable.
    assert s["total_bytes"] > 0
    assert s["total_bytes"] < 10_000


def test_summary_returns_iso_for_last_write(vault: Path):
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    s = vault_stats.summary(vault, now=now)
    assert s["last_write_iso"] is not None
    # ISO 8601 shape sanity check
    assert "T" in s["last_write_iso"]
    assert s["last_write_iso"].count("-") >= 2


def test_summary_counts_todays_daily_notes(vault: Path):
    """Daily notes for today should bump daily_today; older ones don't."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    today_str = "2026-05-10"
    yesterday_str = "2026-05-09"
    (vault / "Daily" / f"{today_str} - voice turn 1.md").write_text("today1", encoding="utf-8")
    (vault / "Daily" / f"{today_str} - voice turn 2.md").write_text("today2", encoding="utf-8")
    (vault / "Daily" / f"{yesterday_str} - voice.md").write_text("yesterday", encoding="utf-8")
    s = vault_stats.summary(vault, now=now)
    assert s["daily_today"] == 2


def test_summary_handles_empty_vault(tmp_path: Path):
    """An empty vault returns zeros, not an exception."""
    empty = tmp_path / "EmptyBrain"
    empty.mkdir()
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    s = vault_stats.summary(empty, now=now)
    assert s["total_notes"] == 0
    assert s["total_bytes"] == 0
    assert s["last_write_iso"] is None
    assert s["daily_today"] == 0


def test_summary_handles_nonexistent_root(tmp_path: Path):
    """A vault path that doesn't exist returns zeros, doesn't raise."""
    nonexistent = tmp_path / "missing"
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    s = vault_stats.summary(nonexistent, now=now)
    assert s["total_notes"] == 0
    assert s["total_bytes"] == 0
    assert s["last_write_iso"] is None
    assert s["daily_today"] == 0


def test_summary_skips_dotfiles_and_hidden_dirs(tmp_path: Path):
    """Files under .obsidian/ or .git/ should NOT count."""
    brain = tmp_path / "Brain"
    (brain / "Knowledge").mkdir(parents=True)
    (brain / ".obsidian").mkdir()
    (brain / "Knowledge" / "real.md").write_text("real", encoding="utf-8")
    (brain / ".obsidian" / "config.md").write_text("hidden", encoding="utf-8")
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    s = vault_stats.summary(brain, now=now)
    assert s["total_notes"] == 1  # the .obsidian one is excluded

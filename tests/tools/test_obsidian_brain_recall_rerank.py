"""Integration test for recall() reranking by retrieval frequency.

Sets up a tmp vault with three notes that all match a query equally,
then primes the retrieval log to make one of them frequently-retrieved,
and confirms recall() ranks that one first.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openjarvis.tools import obsidian_brain, retrieval_log


@pytest.fixture
def tmp_vault(tmp_path, monkeypatch):
    """Build a tmp vault with three notes that all match 'tiktok video'."""
    vault = tmp_path / "Brain"
    knowledge = vault / "Knowledge"
    knowledge.mkdir(parents=True)
    # Three notes — equal text content so vanilla recall scores them equally.
    for slug in ["one", "two", "three"]:
        (knowledge / f"{slug}.md").write_text(
            f"# {slug}\n\nThis is about a tiktok video and how to brief it.\n",
            encoding="utf-8",
        )
    # Point obsidian_brain at the tmp vault.
    monkeypatch.setattr(obsidian_brain, "DEFAULT_VAULT", vault)
    monkeypatch.setattr(obsidian_brain, "BRAIN_ROOT", vault)
    monkeypatch.setattr(obsidian_brain, "KNOWLEDGE_DIR", knowledge)
    # Disable the directory-creating side effect that would touch the real vault.
    monkeypatch.setattr(obsidian_brain, "_ensure_layout", lambda: None)
    return vault


@pytest.fixture
def tmp_log_dir(tmp_path, monkeypatch):
    """Redirect retrieval log to a tmp dir."""
    log_dir = tmp_path / "retrievals"
    monkeypatch.setattr(retrieval_log, "_default_log_dir", lambda: log_dir)
    return log_dir


def test_recall_reranks_by_retrieval_frequency(tmp_vault, tmp_log_dir):
    """Note 'two' has been retrieved 10× recently, the others 0×.
    With equal vanilla scores, 'two' should rank first."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc).timestamp()
    # Prime the log: 10 retrievals of note 'two', 0 of others.
    for i in range(10):
        retrieval_log.log_retrieval(
            note_path=Path("Brain/Knowledge/two.md"),
            query="tiktok video",
            now=now - i * 100,
            log_dir=tmp_log_dir,
        )
    hits = obsidian_brain.recall("tiktok video", limit=5)
    assert len(hits) == 3
    first_path = hits[0][0]
    assert first_path.name == "two.md"


def test_recall_logs_retrievals_after_returning(tmp_vault, tmp_log_dir):
    """Every hit returned by recall() should land in the retrieval log
    so future calls benefit from the frequency signal."""
    hits = obsidian_brain.recall("tiktok video", limit=3)
    assert len(hits) == 3
    # Now a single line per hit should exist in today's log.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = tmp_log_dir / f"{today}.jsonl"
    assert log_file.exists()
    lines = [l for l in log_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 3
    paths = sorted(json.loads(l)["path"] for l in lines)
    assert paths == sorted(str(p[0]).replace("\\", "/") for p in hits)


def test_recall_reranking_does_not_demote_hits(tmp_vault, tmp_log_dir):
    """Even with zero retrieval history, recall() should still return
    all matching notes (rerank multiplier is ≥1, never <1)."""
    hits = obsidian_brain.recall("tiktok video", limit=5)
    assert len(hits) == 3   # all three matches present

"""Tests for retrieval_log: append-only JSONL writer + windowed reader."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openjarvis.tools import retrieval_log


def test_log_retrieval_writes_jsonl_line(tmp_path):
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc).timestamp()
    retrieval_log.log_retrieval(
        note_path=Path("Brain/Knowledge/note-a.md"),
        query="how to do X",
        now=now,
        log_dir=tmp_path,
    )
    written = tmp_path / "2026-05-10.jsonl"
    assert written.exists()
    line = written.read_text(encoding="utf-8").strip()
    record = json.loads(line)
    assert record["path"] == "Brain/Knowledge/note-a.md"
    assert record["query"] == "how to do X"
    assert record["ts"] == pytest.approx(now)


def test_log_retrieval_appends_when_called_repeatedly(tmp_path):
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc).timestamp()
    for slug in ["a", "b", "c"]:
        retrieval_log.log_retrieval(
            note_path=Path(f"Brain/Knowledge/{slug}.md"),
            query="q",
            now=now + ord(slug[0]),  # distinct ts
            log_dir=tmp_path,
        )
    lines = (tmp_path / "2026-05-10.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    paths = [json.loads(l)["path"] for l in lines]
    assert paths == [
        "Brain/Knowledge/a.md",
        "Brain/Knowledge/b.md",
        "Brain/Knowledge/c.md",
    ]


def test_log_retrieval_tolerates_disk_failure(tmp_path, monkeypatch, caplog):
    """A read-only log dir or a full disk must NOT raise.
    Recall is on the operator's voice path — never throw."""
    bad_dir = tmp_path / "nonexistent" / "deep" / "path"
    # Simulate a disk failure on mkdir+write
    def failing_open(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr("builtins.open", failing_open)
    # No exception should propagate.
    retrieval_log.log_retrieval(
        note_path=Path("x.md"), query="q", now=1000.0, log_dir=bad_dir,
    )


def test_iter_retrievals_returns_records_within_window(tmp_path):
    """Within the rolling window, return all records; outside, drop them."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc).timestamp()
    day_seconds = 86400
    # Three records: 5 days ago, 15 days ago, 40 days ago
    file_today = tmp_path / "2026-05-10.jsonl"
    file_old = tmp_path / "2026-04-25.jsonl"
    file_oldest = tmp_path / "2026-03-31.jsonl"
    file_today.write_text(json.dumps({"path": "fresh.md", "query": "q", "ts": now - 5*day_seconds}) + "\n", encoding="utf-8")
    file_old.write_text(json.dumps({"path": "midage.md", "query": "q", "ts": now - 15*day_seconds}) + "\n", encoding="utf-8")
    file_oldest.write_text(json.dumps({"path": "ancient.md", "query": "q", "ts": now - 40*day_seconds}) + "\n", encoding="utf-8")

    records = list(retrieval_log.iter_retrievals(tmp_path, window_days=30, now=now))
    paths = sorted(r["path"] for r in records)
    assert paths == ["fresh.md", "midage.md"]


def test_iter_retrievals_skips_corrupt_lines(tmp_path):
    """A truncated / non-JSON line must not crash the reader."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc).timestamp()
    f = tmp_path / "2026-05-10.jsonl"
    f.write_text(
        json.dumps({"path": "good.md", "query": "q", "ts": now}) + "\n"
        + "{ this is not valid json\n"
        + json.dumps({"path": "good2.md", "query": "q", "ts": now}) + "\n",
        encoding="utf-8",
    )
    records = list(retrieval_log.iter_retrievals(tmp_path, window_days=30, now=now))
    assert sorted(r["path"] for r in records) == ["good.md", "good2.md"]


def test_iter_retrievals_returns_empty_when_dir_missing(tmp_path):
    nonexistent = tmp_path / "does-not-exist"
    records = list(retrieval_log.iter_retrievals(nonexistent, window_days=30, now=1000.0))
    assert records == []


def test_helpfulness_score_zero_for_never_retrieved(tmp_path):
    score = retrieval_log.helpfulness_score(
        Path("Brain/Knowledge/never.md"),
        log_dir=tmp_path,
        window_days=30,
        now=1000.0,
    )
    assert score == 0.0


def test_helpfulness_score_counts_retrievals_within_window(tmp_path):
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc).timestamp()
    f = tmp_path / "2026-05-10.jsonl"
    f.write_text(
        "\n".join([
            json.dumps({"path": "Brain/Knowledge/x.md", "query": "q1", "ts": now - 100}),
            json.dumps({"path": "Brain/Knowledge/x.md", "query": "q2", "ts": now - 200}),
            json.dumps({"path": "Brain/Knowledge/x.md", "query": "q3", "ts": now - 300}),
            json.dumps({"path": "Brain/Knowledge/y.md", "query": "q1", "ts": now - 100}),
        ]) + "\n",
        encoding="utf-8",
    )
    assert retrieval_log.helpfulness_score(
        Path("Brain/Knowledge/x.md"), log_dir=tmp_path, window_days=30, now=now,
    ) == 3.0
    assert retrieval_log.helpfulness_score(
        Path("Brain/Knowledge/y.md"), log_dir=tmp_path, window_days=30, now=now,
    ) == 1.0


def test_helpfulness_score_normalises_windows_paths(tmp_path):
    """Logs may contain either forward or back slashes (Windows); the
    scorer must match either when called with a Path."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc).timestamp()
    f = tmp_path / "2026-05-10.jsonl"
    f.write_text(
        json.dumps({"path": "Brain/Knowledge/x.md", "query": "q", "ts": now}) + "\n",
        encoding="utf-8",
    )
    # Path with backslashes (as Windows would create) must still match.
    score = retrieval_log.helpfulness_score(
        Path("Brain") / "Knowledge" / "x.md",
        log_dir=tmp_path, window_days=30, now=now,
    )
    assert score == 1.0


def test_helpfulness_score_excludes_outside_window(tmp_path):
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc).timestamp()
    day = 86400
    f_recent = tmp_path / "2026-05-10.jsonl"
    f_old = tmp_path / "2026-03-31.jsonl"
    f_recent.write_text(
        json.dumps({"path": "Brain/Knowledge/x.md", "query": "q", "ts": now - 5*day}) + "\n",
        encoding="utf-8",
    )
    f_old.write_text(
        json.dumps({"path": "Brain/Knowledge/x.md", "query": "q", "ts": now - 40*day}) + "\n",
        encoding="utf-8",
    )
    score = retrieval_log.helpfulness_score(
        Path("Brain/Knowledge/x.md"),
        log_dir=tmp_path, window_days=30, now=now,
    )
    assert score == 1.0  # only the recent one counts

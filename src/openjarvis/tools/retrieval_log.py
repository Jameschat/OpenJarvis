"""Append-only retrieval log + windowed reader.

Every time `obsidian_brain.recall()` returns a hit, we append one JSONL
line to `<log_dir>/<YYYY-MM-DD>.jsonl`. The reader walks recent files
and yields records within a rolling window — used by helpfulness scoring.

Design:
- One file per day (cheap rotation, easy to prune later)
- JSON lines (append-only, no rewriting, crash-safe)
- Disk failures are silent — recall is on the voice path, must never throw
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

logger = logging.getLogger(__name__)


def _default_log_dir() -> Path:
    return Path.home() / ".openjarvis" / "retrievals"


def log_retrieval(
    *,
    note_path: Path,
    query: str,
    now: float,
    log_dir: Optional[Path] = None,
) -> None:
    """Append a single retrieval event. Best-effort — never raises."""
    if log_dir is None:
        log_dir = _default_log_dir()
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.debug("retrieval_log: mkdir failed (non-fatal)", exc_info=True)
        return
    date_str = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
    target = log_dir / f"{date_str}.jsonl"
    line = json.dumps({
        "path": str(note_path).replace("\\", "/"),
        "query": query[:200],   # truncate verbose voice transcripts
        "ts": now,
    }, ensure_ascii=False)
    try:
        with open(target, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        logger.debug("retrieval_log: append failed (non-fatal)", exc_info=True)


def iter_retrievals(
    log_dir: Path,
    *,
    window_days: int = 30,
    now: float,
) -> Iterator[Dict[str, Any]]:
    """Yield retrieval records within the rolling window. Skips corrupt
    lines silently. Returns nothing if log_dir doesn't exist."""
    if not log_dir.exists():
        return
    cutoff = now - window_days * 86400
    for jsonl in sorted(log_dir.glob("*.jsonl")):
        try:
            with open(jsonl, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    ts = rec.get("ts")
                    if not isinstance(ts, (int, float)):
                        continue
                    if ts >= cutoff:
                        yield rec
        except OSError:
            continue


def helpfulness_score(
    note_path: Path,
    *,
    log_dir: Optional[Path] = None,
    window_days: int = 30,
    now: float,
) -> float:
    """Frequency-based usefulness score: count of retrievals in window.

    v1 proxy for "did this note help?" — the assumption is that notes
    retrieved often have proven their utility by being recalled
    repeatedly. Phase 3 will tighten this to retrieval × task-success
    correlation once per-task retrieval tracking is wired through.
    """
    if log_dir is None:
        log_dir = _default_log_dir()
    target = str(note_path).replace("\\", "/")
    count = 0
    for rec in iter_retrievals(log_dir, window_days=window_days, now=now):
        if rec.get("path") == target:
            count += 1
    return float(count)


__all__ = ["log_retrieval", "iter_retrievals", "helpfulness_score"]

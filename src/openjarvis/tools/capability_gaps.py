"""Structured records for missing Jarvis capabilities.

Capability gaps are not failures by themselves. They are learning inputs:
when Jarvis cannot currently complete a requested task, record the missing
capability so the learning reviewer can look for tools or integrations later.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
_write_lock = threading.Lock()


def _learning_root() -> Path:
    return Path(os.environ.get(
        "OPENJARVIS_LEARNING_HOME",
        str(Path.home() / ".openjarvis" / "learning"),
    ))


def _gaps_root() -> Path:
    return _learning_root() / "capability_gaps"


def _day_dir(ts: Optional[float] = None) -> Path:
    if ts is None:
        ts = time.time()
    return _gaps_root() / datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _write_record(record: Dict[str, Any]) -> Optional[Path]:
    try:
        day = _day_dir(record.get("created_at"))
        day.mkdir(parents=True, exist_ok=True)
        target = day / f"{record['gap_id']}.json"
        tmp = target.with_suffix(".json.tmp")
        with _write_lock:
            tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
            tmp.replace(target)
        return target
    except Exception:
        logger.exception("capability_gaps: failed to write gap")
        return None


def record_gap(
    capability: str,
    trigger: str,
    context: str = "",
    severity: str = "medium",
    source: str = "tool_use",
) -> Optional[Path]:
    severity = severity if severity in {"low", "medium", "high"} else "medium"
    now = time.time()
    gap_id = "g_" + datetime.fromtimestamp(now).strftime("%H%M%S") + "-" + uuid.uuid4().hex[:6]
    record = {
        "type": "capability-gap",
        "gap_id": gap_id,
        "capability": (capability or "").strip()[:240],
        "trigger": (trigger or "").strip()[:500],
        "context": (context or "").strip()[:1200],
        "severity": severity,
        "status": "open",
        "source": source,
        "created_at": now,
        "created_iso": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
    }
    if not record["capability"]:
        return None
    return _write_record(record)


def _read_record(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("capability_gaps: failed to read %s", path, exc_info=True)
        return None


def recent_gaps(window_days: int = 14, limit: int = 200) -> List[Dict[str, Any]]:
    root = _gaps_root()
    if not root.exists():
        return []
    cutoff = datetime.now() - timedelta(days=window_days)
    out: List[Dict[str, Any]] = []
    for day in root.iterdir():
        if not day.is_dir():
            continue
        try:
            day_dt = datetime.strptime(day.name, "%Y-%m-%d")
        except ValueError:
            continue
        if day_dt < cutoff:
            continue
        for path in day.glob("*.json"):
            rec = _read_record(path)
            if rec:
                out.append(rec)
    out.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    return out[:limit]


def summarize_gaps(window_days: int = 14) -> Dict[str, Any]:
    gaps = recent_gaps(window_days=window_days, limit=1000)
    by_capability: Dict[str, int] = {}
    for gap in gaps:
        cap = gap.get("capability") or "unknown"
        by_capability[cap] = by_capability.get(cap, 0) + 1
    repeated = [
        {"capability": cap, "count": count}
        for cap, count in sorted(by_capability.items(), key=lambda item: item[1], reverse=True)
        if count > 1
    ]
    return {
        "window_days": window_days,
        "total": len(gaps),
        "repeated": repeated,
        "recent": gaps[:20],
    }


__all__ = ["record_gap", "recent_gaps", "summarize_gaps"]

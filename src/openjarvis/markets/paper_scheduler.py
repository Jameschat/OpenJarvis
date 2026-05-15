"""Guarded paper-bot schedules for Markets Bot Lab.

This stores paper-only bot schedules and due checks. It does not place live
orders and defaults every bot to dry-run monitoring.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any


_VALID_STRATEGIES = {"dca", "grid", "signal", "dca_sweep", "grid_sweep"}


def _now() -> int:
    return int(time.time())


def _data_dir() -> Path:
    root = os.environ.get("OPENJARVIS_PAPER_BOT_DIR")
    path = Path(root) if root else Path.home() / ".openjarvis" / "markets" / "paper_bots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _clean_ticker(ticker: str) -> str:
    return (ticker or "").strip().upper().replace("/GBP", "").replace("-GBP", "")


def _clean_strategy(strategy: str) -> str:
    value = (strategy or "").strip().lower()
    if value not in _VALID_STRATEGIES:
        raise ValueError(f"strategy must be one of {sorted(_VALID_STRATEGIES)}")
    return value


def _path_for(bot_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", bot_id or "")
    if not safe:
        raise ValueError("bot id required")
    return _data_dir() / f"{safe}.json"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(bot: dict[str, Any]) -> Path:
    path = _path_for(bot["id"])
    path.write_text(json.dumps(bot, indent=2, sort_keys=True), encoding="utf-8")
    return path


def schedule_paper_bot(
    *,
    ticker: str,
    strategy: str,
    interval_minutes: int | float = 60,
    config: dict[str, Any] | None = None,
    name: str | None = None,
    execute_paper: bool = False,
    confirm_paper_execution: bool = False,
    now_ts: int | None = None,
) -> dict[str, Any]:
    """Create a guarded paper-bot schedule.

    ``execute_paper`` is intentionally blocked unless explicitly confirmed.
    Even confirmed execution remains paper-broker only; this module has no live
    exchange integration.
    """

    sym = _clean_ticker(ticker)
    if not sym:
        return {"ok": False, "error": "ticker required"}
    try:
        strat = _clean_strategy(strategy)
        interval = int(float(interval_minutes))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if interval < 5:
        return {"ok": False, "error": "interval_minutes must be at least 5"}
    if execute_paper and not confirm_paper_execution:
        return {"ok": False, "error": "paper execution requires explicit approval"}
    now = int(now_ts if now_ts is not None else _now())
    bot_id = "paperbot_" + uuid.uuid4().hex[:12]
    bot = {
        "id": bot_id,
        "name": (name or f"{sym} {strat} paper bot").strip(),
        "ticker": sym,
        "strategy": strat,
        "status": "active",
        "interval_minutes": interval,
        "config": config if isinstance(config, dict) else {},
        "execute_paper": bool(execute_paper),
        "dry_run": not bool(execute_paper),
        "paper_only": True,
        "no_live_orders": True,
        "created_at": now,
        "updated_at": now,
        "last_checked_at": None,
        "last_note": "",
        "next_run_at": now + interval * 60,
    }
    path = _write(bot)
    return {"ok": True, "bot": bot, "path": str(path)}


def list_paper_bots() -> dict[str, Any]:
    bots = []
    for path in sorted(_data_dir().glob("*.json")):
        try:
            bots.append(_read(path))
        except Exception:
            continue
    bots.sort(key=lambda item: (item.get("status") != "active", item.get("next_run_at") or 0))
    return {"ok": True, "bots": bots}


def cancel_paper_bot(bot_id: str) -> dict[str, Any]:
    try:
        path = _path_for(bot_id)
        bot = _read(path)
    except Exception:
        return {"ok": False, "error": "paper bot not found"}
    bot["status"] = "cancelled"
    bot["updated_at"] = _now()
    _write(bot)
    return {"ok": True, "bot": bot}


def due_paper_bots(*, now_ts: int | None = None) -> dict[str, Any]:
    now = int(now_ts if now_ts is not None else _now())
    due = [
        bot for bot in list_paper_bots()["bots"]
        if bot.get("status") == "active" and int(bot.get("next_run_at") or 0) <= now
    ]
    return {"ok": True, "now": now, "due": due}


def mark_paper_bot_checked(bot_id: str, *, now_ts: int | None = None, note: str = "") -> dict[str, Any]:
    try:
        path = _path_for(bot_id)
        bot = _read(path)
    except Exception:
        return {"ok": False, "error": "paper bot not found"}
    now = int(now_ts if now_ts is not None else _now())
    interval = int(bot.get("interval_minutes") or 60)
    bot["last_checked_at"] = now
    bot["last_note"] = note
    bot["next_run_at"] = now + interval * 60
    bot["updated_at"] = now
    _write(bot)
    return {"ok": True, "bot": bot}


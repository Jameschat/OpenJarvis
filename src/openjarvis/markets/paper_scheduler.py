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


def _run_backtest(bot: dict[str, Any]) -> dict[str, Any]:
    from openjarvis.markets import bot_lab

    ticker = bot["ticker"]
    config = bot.get("config") if isinstance(bot.get("config"), dict) else {}
    strategy = bot.get("strategy")
    if strategy == "dca":
        return bot_lab.backtest_dca_from_history(ticker, **config)
    if strategy == "grid":
        return bot_lab.backtest_grid_from_history(ticker, **config)
    if strategy == "signal":
        return bot_lab.backtest_signal_from_history(ticker, **config)
    if strategy == "dca_sweep":
        return bot_lab.sweep_dca_from_history(ticker, **config)
    if strategy == "grid_sweep":
        return bot_lab.sweep_grid_from_history(ticker, **config)
    return {"ok": False, "error": f"unsupported strategy {strategy}"}


def _note_for_result(result: dict[str, Any]) -> str:
    if not result.get("ok", False):
        return str(result.get("error") or "dry-run check failed")
    bits = [f"strategy={result.get('strategy', 'unknown')}"]
    if "roi_pct" in result:
        bits.append(f"roi={result.get('roi_pct')}%")
    if "max_drawdown_pct" in result:
        bits.append(f"drawdown={result.get('max_drawdown_pct')}%")
    if "runs" in result:
        bits.append(f"runs={result.get('runs')}")
    return "dry-run check: " + ", ".join(bits)


def run_due_paper_bots(*, now_ts: int | None = None) -> dict[str, Any]:
    """Run due scheduler checks in dry-run mode only.

    This evaluates saved strategies through Bot Lab backtests/sweeps and rolls
    schedules forward. It deliberately refuses bots configured for execution so
    a future paper-execution path requires a separate approval-gated change.
    """

    now = int(now_ts if now_ts is not None else _now())
    due = due_paper_bots(now_ts=now)["due"]
    results = []
    for bot in due:
        if bot.get("execute_paper"):
            result = {
                "ok": False,
                "bot_id": bot.get("id"),
                "ticker": bot.get("ticker"),
                "strategy": bot.get("strategy"),
                "executed_paper": False,
                "error": "scheduler supports dry-run checks only",
            }
            mark_paper_bot_checked(bot["id"], now_ts=now, note=result["error"])
            results.append(result)
            continue
        backtest = _run_backtest(bot)
        note = _note_for_result(backtest)
        mark_paper_bot_checked(bot["id"], now_ts=now, note=note)
        results.append({
            "ok": bool(backtest.get("ok", False)),
            "bot_id": bot.get("id"),
            "ticker": bot.get("ticker"),
            "strategy": bot.get("strategy"),
            "executed_paper": False,
            "backtest": backtest,
            "note": note,
        })
    return {"ok": True, "now": now, "checked": len(results), "results": results}

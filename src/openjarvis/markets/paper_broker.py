"""Paper-trading broker for Financial Jarvis.

Paper-only, self-use simulation. No broker API calls, no order execution.
Uses the existing markets SQLite database and writes a markdown journal
entry for every simulated open/close action.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.markets import store
from openjarvis.markets.sources import coingecko, kraken

logger = logging.getLogger(__name__)

FEE = 0.001


def _now() -> float:
    return time.time()


def _clean_ticker(ticker: str) -> str:
    return (ticker or "").strip().upper().replace("/GBP", "").replace("-GBP", "")


def _conn() -> sqlite3.Connection:
    store.paper_portfolio_get()
    c = sqlite3.connect(store.db_path())
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


def _json_notes(row: sqlite3.Row) -> Dict[str, Any]:
    raw = row["notes"] if "notes" in row.keys() else ""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def quote(ticker: str) -> Optional[Dict[str, Any]]:
    sym = _clean_ticker(ticker)
    if not sym:
        return None
    q = coingecko.fetch_quote(sym)
    if q is None and sym in ("BTC", "ETH"):
        q = kraken.fetch_quote(sym)
    if q:
        try:
            store.upsert_price_latest(
                ticker=q["ticker"], last=q["last"],
                change_pct=q.get("change_pct"),
                change_24h_pct=q.get("change_24h_pct"),
                volume=q.get("volume"), currency=q.get("currency"),
                source=q.get("source"), ts=q.get("ts"),
            )
        except Exception:
            logger.debug("paper quote cache update failed", exc_info=True)
    return q


def _journal_dir() -> Path:
    try:
        from openjarvis.tools.obsidian_brain import BRAIN_ROOT
        p = Path(BRAIN_ROOT) / "Trading" / "Journal"
    except Exception:
        p = Path.home() / "Obsidian" / "Claude" / "Brain" / "Trading" / "Journal"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_journal(action: str, payload: Dict[str, Any]) -> None:
    ticker = payload.get("ticker") or "UNKNOWN"
    day = datetime.now().date().isoformat()
    path = _journal_dir() / f"{day} - {ticker} - paper.md"
    line = (
        f"- {datetime.now().strftime('%H:%M:%S')} - {action}: "
        f"qty={payload.get('quantity'):.8g}, price={payload.get('price_native'):.8g} GBP, "
        f"gross={payload.get('gross_gbp'):.2f} GBP, fee={payload.get('commission_gbp'):.2f} GBP"
    )
    if payload.get("pnl_gbp") is not None:
        line += f", pnl={payload.get('pnl_gbp'):.2f} GBP"
    line += f", reason={payload.get('reason') or 'paper'}"
    if not path.exists():
        path.write_text(
            "---\n"
            "type: paper-trade-journal\n"
            f"date: {day}\n"
            f"ticker: {ticker}\n"
            "tags: [trading, paper-trading]\n"
            "---\n\n"
            f"# Paper journal - {ticker}\n\n"
            "Personal paper-trading log. No real broker order was placed.\n\n",
            encoding="utf-8",
        )
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _portfolio_row(c: sqlite3.Connection) -> Dict[str, Any]:
    row = c.execute("SELECT * FROM paper_portfolio WHERE id = 1").fetchone()
    if row is None:
        store.paper_portfolio_get()
        row = c.execute("SELECT * FROM paper_portfolio WHERE id = 1").fetchone()
    return dict(row)


def _set_cash(c: sqlite3.Connection, cash: float) -> None:
    c.execute(
        "UPDATE paper_portfolio SET cash_gbp = ?, last_updated_at = ? WHERE id = 1",
        (float(cash), _now()),
    )


def _open_buy_rows(c: sqlite3.Connection, ticker: Optional[str] = None) -> List[sqlite3.Row]:
    params: List[Any] = []
    where = "side = 'BUY'"
    if ticker:
        where += " AND ticker = ?"
        params.append(_clean_ticker(ticker))
    rows = c.execute(
        f"SELECT * FROM paper_trades WHERE {where} ORDER BY ts ASC",
        params,
    ).fetchall()
    out = []
    for row in rows:
        rec_id = row["rec_id"] or row["trade_id"]
        sold = c.execute(
            "SELECT 1 FROM paper_trades WHERE side = 'SELL' AND rec_id = ? LIMIT 1",
            (rec_id,),
        ).fetchone()
        if sold is None:
            out.append(row)
    return out


def paper_buy(ticker: str, gbp_amount: float, *,
              stop: Optional[float] = None,
              tp1: Optional[float] = None,
              tp2: Optional[float] = None) -> Dict[str, Any]:
    sym = _clean_ticker(ticker)
    if not sym:
        return {"ok": False, "error": "ticker required"}
    try:
        amount = float(gbp_amount)
    except Exception:
        return {"ok": False, "error": "gbp_amount must be numeric"}
    if amount <= 0:
        return {"ok": False, "error": "gbp_amount must be > 0"}
    q = quote(sym)
    if not q or q.get("last") is None:
        return {"ok": False, "error": f"no live quote for {sym}"}
    price = float(q["last"])
    fee = amount * FEE
    qty = (amount - fee) / price
    rec_id = "paper_" + uuid.uuid4().hex[:12]
    trade_id = rec_id + "_buy"
    now = _now()
    notes = {
        "status": "open", "stop": stop, "tp1": tp1, "tp2": tp2,
        "entry_price": price, "fee_rate": FEE,
    }
    with _conn() as c:
        pf = _portfolio_row(c)
        cash = float(pf.get("cash_gbp") or 0.0)
        if amount > cash:
            return {"ok": False, "error": "insufficient paper cash", "cash_gbp": cash}
        if _open_buy_rows(c, sym):
            return {"ok": False, "error": f"paper position already open for {sym}"}
        c.execute(
            "INSERT INTO paper_trades "
            "(trade_id, rec_id, ticker, market, side, ts, quantity, price_native, "
            " currency, fx_rate_gbp, gross_gbp, commission_gbp, notes) "
            "VALUES (?, ?, ?, 'CRYPTO', 'BUY', ?, ?, ?, 'GBP', 1, ?, ?, ?)",
            (trade_id, rec_id, sym, now, qty, price, amount, fee, json.dumps(notes)),
        )
        _set_cash(c, cash - amount)
    payload = {
        "ok": True, "position_id": rec_id, "trade_id": trade_id,
        "ticker": sym, "quantity": qty, "price_native": price,
        "gross_gbp": amount, "commission_gbp": fee,
        "stop": stop, "tp1": tp1, "tp2": tp2,
    }
    _write_journal("BUY", payload)
    return payload


def paper_sell(ticker: str, *, reason: str = "manual",
               price: Optional[float] = None) -> Dict[str, Any]:
    sym = _clean_ticker(ticker)
    if not sym:
        return {"ok": False, "error": "ticker required"}
    with _conn() as c:
        rows = _open_buy_rows(c, sym)
        if not rows:
            return {"ok": False, "error": f"no open paper position for {sym}"}
        buy = rows[0]
        q = quote(sym) if price is None else None
        mark = float(price if price is not None else (q or {}).get("last") or buy["price_native"])
        qty = float(buy["quantity"])
        gross = qty * mark
        fee = gross * FEE
        proceeds = gross - fee
        cost = float(buy["gross_gbp"])
        pnl = proceeds - cost
        rec_id = buy["rec_id"] or buy["trade_id"]
        trade_id = rec_id + "_sell_" + uuid.uuid4().hex[:6]
        notes = {
            "status": reason or "closed_manually", "reason": reason,
            "entry_price": buy["price_native"], "exit_price": mark,
            "pnl_gbp": pnl, "fee_rate": FEE,
        }
        pf = _portfolio_row(c)
        cash = float(pf.get("cash_gbp") or 0.0)
        c.execute(
            "INSERT INTO paper_trades "
            "(trade_id, rec_id, ticker, market, side, ts, quantity, price_native, "
            " currency, fx_rate_gbp, gross_gbp, commission_gbp, notes) "
            "VALUES (?, ?, ?, 'CRYPTO', 'SELL', ?, ?, ?, 'GBP', 1, ?, ?, ?)",
            (trade_id, rec_id, sym, _now(), qty, mark, gross, fee, json.dumps(notes)),
        )
        _set_cash(c, cash + proceeds)
    payload = {
        "ok": True, "position_id": rec_id, "trade_id": trade_id,
        "ticker": sym, "quantity": qty, "price_native": mark,
        "gross_gbp": gross, "commission_gbp": fee,
        "proceeds_gbp": proceeds, "pnl_gbp": pnl, "reason": reason,
    }
    _write_journal("SELL", payload)
    return payload


def paper_portfolio() -> Dict[str, Any]:
    with _conn() as c:
        pf = _portfolio_row(c)
        cash = float(pf.get("cash_gbp") or 0.0)
        positions = []
        open_value = 0.0
        for buy in _open_buy_rows(c):
            notes = _json_notes(buy)
            sym = buy["ticker"]
            q = quote(sym)
            mark = float((q or {}).get("last") or buy["price_native"])
            qty = float(buy["quantity"])
            value = qty * mark * (1 - FEE)
            cost = float(buy["gross_gbp"])
            pnl = value - cost
            open_value += value
            positions.append({
                "position_id": buy["rec_id"] or buy["trade_id"],
                "ticker": sym,
                "quantity": qty,
                "entry_price": float(buy["price_native"]),
                "mark_price": mark,
                "cost_gbp": cost,
                "value_gbp": value,
                "unrealised_pnl_gbp": pnl,
                "unrealised_pnl_pct": (pnl / cost * 100.0) if cost else None,
                "stop": notes.get("stop"),
                "tp1": notes.get("tp1"),
                "tp2": notes.get("tp2"),
                "opened_at": float(buy["ts"]),
            })
        sells = c.execute(
            "SELECT * FROM paper_trades WHERE side = 'SELL' ORDER BY ts DESC"
        ).fetchall()
        closed = []
        wins = 0
        realised = 0.0
        for row in sells:
            notes = _json_notes(row)
            pnl = float(notes.get("pnl_gbp") or 0.0)
            realised += pnl
            if pnl > 0:
                wins += 1
            closed.append({
                "trade_id": row["trade_id"], "position_id": row["rec_id"],
                "ticker": row["ticker"], "closed_at": float(row["ts"]),
                "exit_price": float(row["price_native"]), "pnl_gbp": pnl,
                "reason": notes.get("reason") or notes.get("status") or "closed",
            })
        equity = cash + open_value
        total_closed = len(sells)
        return {
            "ok": True,
            "starting_gbp": float(pf.get("notional_starting_gbp") or 0.0),
            "cash_gbp": cash,
            "open_value_gbp": open_value,
            "equity_gbp": equity,
            "realised_pnl_gbp": realised,
            "open_positions": positions,
            "closed_trades": closed[:50],
            "closed_count": total_closed,
            "win_rate": (wins / total_closed * 100.0) if total_closed else None,
        }


def check_open_positions() -> Dict[str, Any]:
    closed = []
    for pos in paper_portfolio().get("open_positions", []):
        mark = pos.get("mark_price")
        stop = pos.get("stop")
        tp1 = pos.get("tp1")
        if mark is None:
            continue
        if stop is not None and mark <= float(stop):
            closed.append(paper_sell(pos["ticker"], reason="stop_hit", price=mark))
        elif tp1 is not None and mark >= float(tp1):
            closed.append(paper_sell(pos["ticker"], reason="tp1_hit", price=mark))
    return {"ok": True, "closed": closed, "closed_count": len(closed)}


__all__ = [
    "FEE", "quote", "paper_buy", "paper_sell", "paper_portfolio",
    "check_open_positions",
]

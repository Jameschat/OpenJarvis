"""SQLite store for the markets subsystem.

Lives at ``~/.openjarvis/markets/markets.db`` (WAL mode). Holds the
operator's watchlist, cached price snapshots, news cache, paper-trade
log, and (from session 2) recommendations + outcomes.

Schema is intentionally narrower than the Backend Architect's full
spec — Day-1 paper-trading mode drops tax-lot ledger, real-position
fields, and broker-specific columns. Add them later if the project
graduates to real trading.

All public functions are sync + thread-safe via a per-connection
lock + WAL. Caller is the asyncio daemon (next session), the LLM
tool layer, or the HTTP API — none currently hold long-running
transactions, so contention is negligible at single-operator scale.

Best-effort logging on every write — a database failure must NEVER
break the upstream voice/chat turn that triggered it.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

_HOME_MARKETS = Path(os.path.expanduser("~/.openjarvis/markets"))
_DB_PATH = _HOME_MARKETS / "markets.db"

# Single shared connection guarded by a re-entrant lock. SQLite handles
# multi-thread reads under WAL fine; the lock ensures writes serialise
# without surprising the caller. Re-entrant so a transaction can call
# helpers that reacquire.
_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None


def _ensure_dirs() -> None:
    _HOME_MARKETS.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is not None:
            return _conn
        _ensure_dirs()
        c = sqlite3.connect(
            _DB_PATH,
            check_same_thread=False,
            isolation_level=None,   # autocommit; explicit BEGIN where needed
        )
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")
        _conn = c
        _migrate(c)
        return c


@contextmanager
def _cursor():
    conn = _connect()
    with _lock:
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()


# ---------------------------------------------------------------------------
# Schema migrations — additive only, idempotent.
# ---------------------------------------------------------------------------

_SCHEMA_VERSION = 1

_DDL_V1 = [
    # ----- watchlist: tickers the operator wants Jarvis to follow -----
    """
    CREATE TABLE IF NOT EXISTS watchlist (
        ticker        TEXT PRIMARY KEY,
        market        TEXT NOT NULL,            -- US | UK | CRYPTO
        added_at      REAL NOT NULL,
        notional_pct  REAL,                     -- target % of paper portfolio
        notes         TEXT
    )
    """,
    # ----- prices_latest: most-recent quote per ticker -----
    """
    CREATE TABLE IF NOT EXISTS prices_latest (
        ticker        TEXT PRIMARY KEY,
        ts            REAL NOT NULL,
        last          REAL,
        change_pct    REAL,
        change_24h_pct REAL,
        volume        REAL,
        currency      TEXT,
        source        TEXT,
        stale         INTEGER DEFAULT 0
    )
    """,
    # ----- prices: OHLCV history (one row per minute or per candle) -----
    """
    CREATE TABLE IF NOT EXISTS prices (
        ticker        TEXT NOT NULL,
        ts            REAL NOT NULL,
        open          REAL,
        high          REAL,
        low           REAL,
        close         REAL,
        volume        REAL,
        source        TEXT,
        PRIMARY KEY (ticker, ts)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_prices_ticker_ts
        ON prices (ticker, ts DESC)
    """,
    # ----- news cache (next session — created now so it exists) -----
    """
    CREATE TABLE IF NOT EXISTS news (
        id            TEXT PRIMARY KEY,         -- hash(url + ts)
        ticker        TEXT,
        ts            REAL NOT NULL,
        headline      TEXT NOT NULL,
        url           TEXT NOT NULL,
        source        TEXT,
        summary       TEXT,
        sentiment     REAL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_news_ticker_ts
        ON news (ticker, ts DESC)
    """,
    # ----- paper portfolio: notional capital + cash position -----
    """
    CREATE TABLE IF NOT EXISTS paper_portfolio (
        id                    INTEGER PRIMARY KEY CHECK (id = 1),
        notional_starting_gbp REAL NOT NULL,
        cash_gbp              REAL NOT NULL,
        created_at            REAL NOT NULL,
        last_updated_at       REAL NOT NULL
    )
    """,
    # ----- paper_trades: every hypothetical buy/sell -----
    """
    CREATE TABLE IF NOT EXISTS paper_trades (
        trade_id      TEXT PRIMARY KEY,
        rec_id        TEXT,                      -- nullable: ad-hoc trades have none
        ticker        TEXT NOT NULL,
        market        TEXT NOT NULL,
        side          TEXT NOT NULL,             -- BUY | SELL
        ts            REAL NOT NULL,
        quantity      REAL NOT NULL,
        price_native  REAL NOT NULL,
        currency      TEXT NOT NULL,
        fx_rate_gbp   REAL NOT NULL,
        gross_gbp     REAL NOT NULL,
        commission_gbp REAL DEFAULT 0,
        notes         TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_paper_trades_ticker_ts
        ON paper_trades (ticker, ts DESC)
    """,
    # ----- recommendations (next session — schema mirrors outcome
    # index in ~/.openjarvis/outcomes/<date>/rec_<id>.json) -----
    """
    CREATE TABLE IF NOT EXISTS recommendations (
        rec_id          TEXT PRIMARY KEY,
        issued_at       REAL NOT NULL,
        ticker          TEXT NOT NULL,
        market          TEXT NOT NULL,
        side            TEXT NOT NULL,
        conviction      INTEGER,                 -- 1-5
        entry_ref_price REAL,
        stop_price      REAL,
        target_price    REAL,
        target_price_2  REAL,
        horizon_days    INTEGER,
        thesis          TEXT,
        sources_json    TEXT,                    -- JSON array of citation URLs
        signal_ids_json TEXT,                    -- JSON array of signal IDs
        status          TEXT DEFAULT 'open'      -- open | tp1 | tp2 | stop | horizon | dismissed
    )
    """,
    # ----- meta: schema version + misc kv -----
    """
    CREATE TABLE IF NOT EXISTS meta (
        key           TEXT PRIMARY KEY,
        value         TEXT
    )
    """,
]


def _migrate(c: sqlite3.Connection) -> None:
    """Run all DDL statements; idempotent. Bumps stored schema_version."""
    for ddl in _DDL_V1:
        c.execute(ddl)
    c.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
        (str(_SCHEMA_VERSION),),
    )


# ---------------------------------------------------------------------------
# Watchlist DAO
# ---------------------------------------------------------------------------

_VALID_MARKETS = ("US", "UK", "CRYPTO")


def watchlist_add(ticker: str, market: str = "US",
                  notional_pct: Optional[float] = None,
                  notes: Optional[str] = None) -> bool:
    """Add or update a ticker on the watchlist. Idempotent.

    Returns True on success, False on validation failure.
    """
    ticker = (ticker or "").strip().upper()
    market = (market or "US").strip().upper()
    if not ticker or market not in _VALID_MARKETS:
        return False
    try:
        with _cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO watchlist "
                "(ticker, market, added_at, notional_pct, notes) "
                "VALUES (?, ?, ?, ?, ?)",
                (ticker, market, time.time(), notional_pct, notes),
            )
        return True
    except Exception:
        logger.exception("watchlist_add failed for %s", ticker)
        return False


def watchlist_remove(ticker: str) -> bool:
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return False
    try:
        with _cursor() as cur:
            cur.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
            return cur.rowcount > 0
    except Exception:
        logger.exception("watchlist_remove failed for %s", ticker)
        return False


def watchlist_get() -> List[Dict[str, Any]]:
    """Return all watchlist entries with their latest cached price (if any)."""
    try:
        with _cursor() as cur:
            cur.execute(
                """
                SELECT w.ticker, w.market, w.added_at, w.notional_pct, w.notes,
                       p.last, p.change_pct, p.change_24h_pct, p.ts AS price_ts,
                       p.currency, p.stale
                FROM watchlist w
                LEFT JOIN prices_latest p ON p.ticker = w.ticker
                ORDER BY w.market, w.ticker
                """
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        logger.exception("watchlist_get failed")
        return []


# ---------------------------------------------------------------------------
# Prices DAO
# ---------------------------------------------------------------------------

def upsert_price_latest(ticker: str, last: float, *,
                        change_pct: Optional[float] = None,
                        change_24h_pct: Optional[float] = None,
                        volume: Optional[float] = None,
                        currency: Optional[str] = None,
                        source: Optional[str] = None,
                        ts: Optional[float] = None,
                        stale: bool = False) -> bool:
    if not ticker or last is None:
        return False
    ticker = ticker.strip().upper()
    ts_use = ts if ts is not None else time.time()
    try:
        with _cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO prices_latest "
                "(ticker, ts, last, change_pct, change_24h_pct, volume, "
                " currency, source, stale) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ticker, ts_use, last, change_pct, change_24h_pct,
                 volume, currency, source, 1 if stale else 0),
            )
        return True
    except Exception:
        logger.exception("upsert_price_latest failed for %s", ticker)
        return False


def get_price_latest(ticker: str) -> Optional[Dict[str, Any]]:
    if not ticker:
        return None
    ticker = ticker.strip().upper()
    try:
        with _cursor() as cur:
            cur.execute(
                "SELECT * FROM prices_latest WHERE ticker = ?",
                (ticker,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception:
        logger.exception("get_price_latest failed for %s", ticker)
        return None


# ---------------------------------------------------------------------------
# Historical OHLCV bars (the `prices` table)
# ---------------------------------------------------------------------------

def insert_history_bars(ticker: str, bars: Iterable[Dict[str, Any]],
                        source: str = "yfinance") -> int:
    """Bulk-insert OHLCV bars. Idempotent — duplicate (ticker, ts)
    pairs silently replace. Returns the number of rows written."""
    if not ticker:
        return 0
    ticker = ticker.strip().upper()
    rows = []
    for b in bars or []:
        try:
            rows.append((
                ticker, float(b["ts"]),
                float(b.get("open")) if b.get("open") is not None else None,
                float(b.get("high")) if b.get("high") is not None else None,
                float(b.get("low")) if b.get("low") is not None else None,
                float(b["close"]),
                float(b.get("volume")) if b.get("volume") is not None else None,
                source,
            ))
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return 0
    try:
        with _cursor() as cur:
            cur.executemany(
                "INSERT OR REPLACE INTO prices "
                "(ticker, ts, open, high, low, close, volume, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)
    except Exception:
        logger.exception("insert_history_bars failed for %s", ticker)
        return 0


def get_history(ticker: str, *,
                since_ts: Optional[float] = None,
                limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Fetch OHLCV bars for a ticker, oldest-first by default. If
    ``since_ts`` is provided, only bars at-or-after that timestamp are
    returned. ``limit`` clamps the row count (most-recent if applied)."""
    if not ticker:
        return []
    ticker = ticker.strip().upper()
    try:
        with _cursor() as cur:
            if limit is not None:
                # Most-recent N, then re-sort ascending for the caller
                if since_ts is not None:
                    cur.execute(
                        "SELECT * FROM prices "
                        "WHERE ticker = ? AND ts >= ? "
                        "ORDER BY ts DESC LIMIT ?",
                        (ticker, since_ts, int(limit)),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM prices WHERE ticker = ? "
                        "ORDER BY ts DESC LIMIT ?",
                        (ticker, int(limit)),
                    )
                rows = [dict(r) for r in cur.fetchall()]
                rows.reverse()
                return rows
            if since_ts is not None:
                cur.execute(
                    "SELECT * FROM prices "
                    "WHERE ticker = ? AND ts >= ? "
                    "ORDER BY ts ASC",
                    (ticker, since_ts),
                )
            else:
                cur.execute(
                    "SELECT * FROM prices WHERE ticker = ? ORDER BY ts ASC",
                    (ticker,),
                )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        logger.exception("get_history failed for %s", ticker)
        return []


def history_count(ticker: str) -> int:
    if not ticker:
        return 0
    try:
        with _cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM prices WHERE ticker = ?",
                        (ticker.strip().upper(),))
            return cur.fetchone()[0] or 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Paper portfolio DAO
# ---------------------------------------------------------------------------

DEFAULT_NOTIONAL_GBP = 10_000.0


def paper_portfolio_get() -> Dict[str, Any]:
    """Return the paper-portfolio singleton, initialising on first call."""
    try:
        with _cursor() as cur:
            cur.execute("SELECT * FROM paper_portfolio WHERE id = 1")
            row = cur.fetchone()
            if row is not None:
                return dict(row)
            now = time.time()
            cur.execute(
                "INSERT INTO paper_portfolio "
                "(id, notional_starting_gbp, cash_gbp, "
                " created_at, last_updated_at) "
                "VALUES (1, ?, ?, ?, ?)",
                (DEFAULT_NOTIONAL_GBP, DEFAULT_NOTIONAL_GBP, now, now),
            )
            cur.execute("SELECT * FROM paper_portfolio WHERE id = 1")
            return dict(cur.fetchone())
    except Exception:
        logger.exception("paper_portfolio_get failed")
        return {
            "id": 1,
            "notional_starting_gbp": DEFAULT_NOTIONAL_GBP,
            "cash_gbp": DEFAULT_NOTIONAL_GBP,
            "created_at": time.time(),
            "last_updated_at": time.time(),
        }


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def db_path() -> Path:
    return _DB_PATH


def health() -> Dict[str, Any]:
    """Quick health snapshot for the HUD status pill."""
    try:
        with _cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM watchlist")
            n_watch = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM prices_latest")
            n_prices = cur.fetchone()[0]
            cur.execute("SELECT MAX(ts) FROM prices_latest")
            last_ts = cur.fetchone()[0] or 0
        return {
            "ok": True,
            "watchlist_count": n_watch,
            "prices_cached": n_prices,
            "last_price_ts": last_ts,
            "last_price_age_seconds": (time.time() - last_ts) if last_ts else None,
            "db_path": str(_DB_PATH),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "db_path": str(_DB_PATH)}


__all__ = [
    "DEFAULT_NOTIONAL_GBP",
    "watchlist_add", "watchlist_remove", "watchlist_get",
    "upsert_price_latest", "get_price_latest",
    "insert_history_bars", "get_history", "history_count",
    "paper_portfolio_get",
    "db_path", "health",
]

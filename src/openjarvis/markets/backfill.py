"""Historical OHLCV backfill — pulls 90 days of bars for the
curated universe so the briefing generator has a baseline of trend
context on Day 1.

Universe (~140 instruments total):
  - US indices/ETFs: SPY, QQQ, IWM, DIA, VTI
  - US large-caps: top ~25 by mcap (AAPL, NVDA, MSFT, GOOGL, META,
                   AMZN, TSLA, AVGO, NFLX, CRM, …)
  - UK indices/ETFs: ISF.L (FTSE100), VMID.L (FTSE250)
  - UK large-caps: top ~15 by mcap on LSE (SHEL.L, AZN.L, HSBA.L,
                   ULVR.L, BP.L, GSK.L, RIO.L, BATS.L, …)
  - Crypto: top 100 by market cap (dynamic, via CoinGecko)

Polite to the free APIs: ~250ms gap between yfinance calls (Yahoo
v8 endpoint can take >50/min unauthenticated but we don't push it),
and CoinGecko gets ~2.5s gap to stay under the 30/min ceiling.

Total backfill wall-clock: ~5-6 minutes (140 sources × per-source
fetch latency + politeness gap). Result: ~12,000 OHLCV rows in
SQLite, ready for the signals layer.

Idempotent. Safe to re-run — bars upsert by (ticker, ts).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from openjarvis.markets import store
from openjarvis.markets.sources import yf, coingecko

logger = logging.getLogger(__name__)


# Curated equity universe — tunable by the operator later via voice
# ("add NVDA to the universe permanently"). Day-1 picks are big,
# liquid, broadly-known names operator can sanity-check intuitively.

US_INDICES = ["SPY", "QQQ", "IWM", "DIA", "VTI"]

US_LARGE_CAPS = [
    "AAPL", "NVDA", "MSFT", "GOOGL", "META",
    "AMZN", "TSLA", "AVGO", "NFLX", "CRM",
    "ORCL", "AMD", "ADBE", "PEP", "COST",
    "TMUS", "CSCO", "INTC", "QCOM", "TXN",
    "INTU", "AMAT", "MU", "PANW", "NOW",
]

UK_INDICES = ["ISF.L", "VMID.L"]

UK_LARGE_CAPS = [
    "SHEL.L", "AZN.L", "HSBA.L", "ULVR.L", "BP.L",
    "GSK.L", "RIO.L", "BATS.L", "RR.L", "LSEG.L",
    "DGE.L", "BARC.L", "VOD.L", "GLEN.L", "TSCO.L",
]


def equity_universe() -> List[Dict[str, str]]:
    """Return the equity universe as a list of {ticker, market} dicts."""
    out = []
    for t in US_INDICES + US_LARGE_CAPS:
        out.append({"ticker": t, "market": "US"})
    for t in UK_INDICES + UK_LARGE_CAPS:
        out.append({"ticker": t, "market": "UK"})
    return out


def crypto_universe(n: int = 1000) -> List[Dict[str, str]]:
    """Return the dynamic top-n crypto universe via CoinGecko.

    Default expanded to 1000 (was 100) so the briefing + pulse pages
    can reason over the long tail. The `fetch_top_n` cache makes
    repeated calls cheap; the first cold call paginates 4× with a
    polite 2.5s gap.
    """
    coins = coingecko.fetch_top_n(n) if n > 100 else coingecko.fetch_top_100()
    out = []
    for c in coins:
        sym = (c.get("symbol") or "").upper()
        if not sym:
            continue
        out.append({"ticker": sym, "market": "CRYPTO",
                    "coingecko_id": c.get("id")})
    return out


def backfill_one(ticker: str, market: str, *,
                 range_str: str = "3mo",
                 interval: str = "1d") -> Dict[str, Any]:
    """Fetch + persist history for a single ticker. Returns a small
    status dict suitable for the orchestrator log."""
    if market == "CRYPTO":
        bars = coingecko.fetch_history(ticker, range_str=range_str)
        source = "coingecko"
    else:
        bars = yf.fetch_history(ticker, range_str=range_str, interval=interval)
        source = "yfinance"
    if not bars:
        return {"ticker": ticker, "market": market, "ok": False,
                "rows": 0, "source": source, "error": "no_bars"}
    n = store.insert_history_bars(ticker, bars, source=source)
    return {"ticker": ticker, "market": market, "ok": n > 0,
            "rows": n, "source": source}


def run(*, range_str: str = "3mo",
        include_equities: bool = True,
        include_crypto: bool = True,
        equity_gap_s: float = 0.25,
        crypto_gap_s: float = 2.5,
        max_crypto: int = 1000) -> Dict[str, Any]:
    """Run the full backfill. Returns a summary dict.

    Politeness gaps are kept conservative — if yfinance starts 429ing
    bump ``equity_gap_s`` to 0.5; if CoinGecko 429s bump ``crypto_gap_s``
    to 3.0. The defaults work on a fresh-start cold run.
    """
    started = time.time()
    summary = {
        "started_at": started,
        "ok": True,
        "equities": {"attempted": 0, "succeeded": 0, "rows": 0,
                      "failures": []},
        "crypto":   {"attempted": 0, "succeeded": 0, "rows": 0,
                      "failures": []},
    }

    if include_equities:
        for inst in equity_universe():
            t, m = inst["ticker"], inst["market"]
            res = backfill_one(t, m, range_str=range_str)
            summary["equities"]["attempted"] += 1
            if res["ok"]:
                summary["equities"]["succeeded"] += 1
                summary["equities"]["rows"] += res["rows"]
            else:
                summary["equities"]["failures"].append(t)
            time.sleep(equity_gap_s)

    if include_crypto:
        coins = crypto_universe(max_crypto)[:max_crypto]
        for inst in coins:
            t, m = inst["ticker"], inst["market"]
            res = backfill_one(t, m, range_str=range_str)
            summary["crypto"]["attempted"] += 1
            if res["ok"]:
                summary["crypto"]["succeeded"] += 1
                summary["crypto"]["rows"] += res["rows"]
            else:
                summary["crypto"]["failures"].append(t)
            time.sleep(crypto_gap_s)

    summary["finished_at"] = time.time()
    summary["wall_seconds"] = round(summary["finished_at"] - started, 1)
    return summary


# ---------------------------------------------------------------------------
# Background runner — fire-and-forget so the HTTP endpoint doesn't block
# for ~5 minutes while the backfill grinds.
# ---------------------------------------------------------------------------

_bg_lock = threading.Lock()
_bg_status: Dict[str, Any] = {"state": "idle"}


def _set_status(**kwargs) -> None:
    with _bg_lock:
        _bg_status.update(kwargs)


def get_status() -> Dict[str, Any]:
    with _bg_lock:
        return dict(_bg_status)


def start_background(**kwargs) -> Dict[str, Any]:
    """Kick off a backfill on a daemon thread. If one is already
    running, returns its current status without starting another."""
    with _bg_lock:
        if _bg_status.get("state") == "running":
            return dict(_bg_status)
        _bg_status.clear()
        _bg_status.update({
            "state": "running",
            "started_at": time.time(),
            "summary": None,
        })

    def _runner():
        try:
            summary = run(**kwargs)
            _set_status(state="done", summary=summary,
                        finished_at=time.time())
        except Exception as exc:
            logger.exception("backfill background runner failed")
            _set_status(state="failed", error=str(exc),
                        finished_at=time.time())

    t = threading.Thread(target=_runner, daemon=True,
                         name="markets-backfill")
    t.start()
    return get_status()


__all__ = [
    "equity_universe", "crypto_universe",
    "backfill_one", "run",
    "start_background", "get_status",
    "US_INDICES", "US_LARGE_CAPS", "UK_INDICES", "UK_LARGE_CAPS",
]

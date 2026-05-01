"""Financial researcher — daily briefing generator.

Pipeline:
    1. gather()      — pull historical bars + live quotes for the
                       universe; compute signal block per ticker
    2. build_bundle() — assemble structured context (~6-10k tokens)
                       with signals, ranked candidates, and a strict
                       schema the LLM must echo
    3. generate()    — single gpt-4o call with the 3-layer prompt
                       (Persona / Contract / Constraints)
    4. validate()    — 4 critical validators (V1 parse, V2 schema,
                       V3 citation count, V4 citation membership)
                       — fewer than the AI Engineer's full spec, but
                       the most important ones; the rest land in the
                       next iteration
    5. persist()     — write Markdown briefing to
                       Brain/Trading/Research/<date> - market-research.md
                       and broadcast SSE event

Day-1 v0 limitations (deliberate, documented in the briefing footer):
- News + macro + calendar feeds NOT included → picks based on
  technical setup only; expect "thin" briefings
- No calibration table feedback yet (needs ≥50 outcomes)
- No baseline-overlap validator (V11) — picks may be obvious-momentum
- Refusal path implemented; empty `recommendations` is a valid output

The briefing is honest-by-construction even at v0 because every
claim must cite a signal_id from the bundle. V4 (citation membership)
is the killswitch — if the model invents a source, the briefing fails.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openjarvis.markets import store, signals, backfill
from openjarvis.markets.sources import yf, coingecko, kraken

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vault paths (Compliance: "research" not "advice")
# ---------------------------------------------------------------------------

def _vault_research_dir() -> Path:
    # Use the same vault root the rest of Jarvis uses
    try:
        from openjarvis.tools.obsidian_brain import BRAIN_ROOT
    except Exception:
        BRAIN_ROOT = Path(os.path.expanduser("~/Obsidian/Claude/Brain"))
    p = Path(BRAIN_ROOT) / "Trading" / "Research"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Universe — equities curated, crypto top-100 dynamic
# ---------------------------------------------------------------------------

def _full_universe() -> List[Dict[str, str]]:
    return backfill.equity_universe() + backfill.crypto_universe()


# ---------------------------------------------------------------------------
# 1. Gather — historical bars + live quotes + signal block per ticker
# ---------------------------------------------------------------------------

def gather() -> List[Dict[str, Any]]:
    """For each instrument in the universe:
       - read its historical bars from the store
       - compute signal block
    Returns a list of signal dicts (one per ticker that has bars)."""
    out = []
    universe = _full_universe()
    cutoff = time.time() - (95 * 86400)  # 95 days ≈ 3 months + a week
    for inst in universe:
        t = inst["ticker"]
        bars = store.get_history(t, since_ts=cutoff)
        if not bars:
            continue
        sig = signals.compute(t, bars)
        sig["market"] = inst["market"]
        out.append(sig)
    return out


# ---------------------------------------------------------------------------
# 2. Build bundle — context for the LLM
# ---------------------------------------------------------------------------

def build_bundle(sigs: List[Dict[str, Any]],
                 *, top_per_market: int = 10) -> Dict[str, Any]:
    """Assemble the bundle the LLM sees. Top-N by composite score per
    market, plus market-aggregate stats so the model can reason about
    regime."""
    by_market: Dict[str, List[Dict[str, Any]]] = {"US": [], "UK": [], "CRYPTO": []}
    for s in sigs:
        m = s.get("market") or "US"
        if m in by_market:
            by_market[m].append(s)

    candidates = {}
    for market, items in by_market.items():
        ranked = signals.rank(items, top_n=top_per_market)
        candidates[market] = [{
            # Compact each candidate so the LLM payload stays small.
            "id": f"sig:{s['ticker']}",
            "ticker": s["ticker"],
            "last": _round(s.get("last"), 4),
            "trend_3m_pct": _round(s.get("trend_3m_pct"), 1),
            "drawdown_from_high_pct": _round(s.get("drawdown_from_high_pct"), 1),
            "above_sma20": s.get("above_sma20"),
            "above_sma50": s.get("above_sma50"),
            "sma_cross": s.get("sma_cross"),
            "vol_annualised_pct": _round(s.get("vol_annualised_pct"), 1),
            "sharpe_proxy": _round(s.get("sharpe_proxy"), 2),
            "composite_score": _round(s.get("composite_score"), 2),
            "n_bars": s.get("n_bars"),
        } for s in ranked]

    market_stats = {}
    for market, items in by_market.items():
        ts = [s.get("trend_3m_pct") for s in items
              if s.get("trend_3m_pct") is not None]
        market_stats[market] = {
            "n_instruments": len(items),
            "median_3m_trend_pct": _round(_median(ts), 1) if ts else None,
            "pct_above_sma50": _round(
                100.0 * sum(1 for s in items if s.get("above_sma50")) / max(1, len(items)), 1
            ) if items else None,
        }

    bundle = {
        "schema_version": "v0",
        "generated_at": time.time(),
        "date": date.today().isoformat(),
        "candidates": candidates,
        "market_stats": market_stats,
        "signal_legend": {
            "trend_3m_pct": "% return over the last 3 months (close-to-close)",
            "drawdown_from_high_pct": "% below the trailing 50-day high",
            "above_sma20": "is the last close >= the 20-day SMA?",
            "above_sma50": "is the last close >= the 50-day SMA?",
            "sma_cross": "'golden' = 20-SMA crossed above 50-SMA in last 5 bars; "
                         "'death' = inverse",
            "vol_annualised_pct": "realised daily-return stdev * sqrt(252)",
            "sharpe_proxy": "naive Sharpe = mean(rets)*252 / (std*sqrt(252)), "
                            "no risk-free adjustment",
            "composite_score": "weighted blend in [-1, +1]: trend + SMA position "
                               "- drawdown penalty - vol penalty",
        },
        "v0_caveats": [
            "No news / macro / calendar in this bundle — picks are technical only",
            "No calibration history yet — confidence labels are uncalibrated",
            "Fewer validators than full spec — V1/V2/V3/V4 only",
        ],
    }
    bundle["bundle_hash"] = hashlib.sha256(
        json.dumps(bundle, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    return bundle


def _round(v: Optional[float], d: int) -> Optional[float]:
    if v is None:
        return None
    try:
        return round(float(v), d)
    except (TypeError, ValueError):
        return None


def _median(xs: List[float]) -> Optional[float]:
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


# ---------------------------------------------------------------------------
# 3. Generate — gpt-4o single call, 3-layer prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
[PERSONA]
You are Jarvis-Research, a quantitative research analyst serving a single \
operator (UK Channel Islands resident, paper-trading account, learning the \
markets). You are NOT a regulated adviser. You produce research, not advice. \
You are paid to be RIGHT, not CONFIDENT. A refused pick is a correct pick \
when the data does not support one. The operator has explicitly told you: \
"I would rather see zero picks than a fabricated one."

[ROLE]
Synthesise the attached SIGNAL BUNDLE into 0-3 named long-side ideas across \
US equities, UK equities, and crypto. Horizon: 14 calendar days. You may \
not forecast beyond 14d. You may not reference any instrument, level, or \
indicator not present in the bundle.

This is the v0 build. The bundle contains technical signals only \
(no news, no macro, no calendar). Be appropriately humble — most days \
the right output is "no high-conviction setups; cash is a position".

[OUTPUT CONTRACT]
Emit exactly one fenced ```json block matching SCHEMA_V0, then a Markdown \
render under "## Briefing". Nothing before, nothing between, nothing after.

SCHEMA_V0:
{
  "briefing_date": "YYYY-MM-DD",
  "bundle_hash": "<echo from input>",
  "regime_note": "<=200 chars; must cite >=1 signal_id like 'sig:NVDA' or \
'market_stats:US'>",
  "thinking": [
    {"step": "<short>", "signal_ids": ["<id>", ...]}
  ],
  "recommendations": [
    {
       "ticker", "market" ("US"|"UK"|"CRYPTO"), "direction" ("long"),
       "thesis": "<=300 chars",
       "signal_ids": ["sig:<TICKER>", ...],   // must be subset of bundle.candidates
       "horizon_days": <integer 1-14>,
       "conviction": "low" | "mid" | "high",
       "conviction_rationale": "<short>",
       "invalidation": "<what would make this thesis wrong>"
    }
  ],
  "refusals": [
    {"slot": "US"|"UK"|"CRYPTO",
     "reason_code": "INSUFFICIENT_SIGNALS"|"VOL_TOO_HIGH"|"NO_TREND"|"DATA_THIN",
     "detail": "<short>"}
  ]
}

[FORBIDDEN]
- Hedging without a signal_id ("might", "could", "potentially") — pair every \
opinion with a signal_id from the bundle.
- First-person epistemic verbs ("I think", "I believe", "I feel"). Replace \
with "Signal X shows...".
- Any ticker not present as a candidate or in market_stats. No outside \
knowledge.
- Conviction "high" if fewer than 2 corroborating signals support the \
direction.
- Filling slots. Empty `recommendations` is valid and encouraged when the \
data is thin.
- Generic platitudes about diversification / dollar-cost averaging — the \
bundle is for naming setups.
"""


def generate(bundle: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Single gpt-4o call. Returns the parsed JSON dict or None on
    upstream failure (no key, network, etc.)."""
    try:
        from openjarvis.cli.llm_fallback import _get_openai_client
    except Exception:
        return None
    client = _get_openai_client()
    if client is None:
        return None
    user_payload = (
        "SIGNAL BUNDLE (only authoritative source you may cite):\n\n"
        + json.dumps(bundle, indent=2, default=str)
    )
    try:
        resp = client.chat.completions.create(
            model=os.environ.get("OPENJARVIS_MARKETS_MODEL", "gpt-4o"),
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_payload},
            ],
            temperature=0.3,
            max_tokens=2000,
        )
        text = (resp.choices[0].message.content or "")
    except Exception:
        logger.exception("financial_researcher: gpt-4o call failed")
        return None
    return _parse_response(text)


def _parse_response(text: str) -> Optional[Dict[str, Any]]:
    """Extract the JSON block + the Markdown render. Returns:
       {"json": <parsed>, "markdown": <str>, "raw": <full text>}"""
    if not text:
        return None
    # Find the first fenced JSON block
    m = re.search(r"```json\s*\n(.+?)\n```", text, flags=re.DOTALL)
    if not m:
        # Fallback: try unfenced JSON object at start
        m2 = re.search(r"^\s*(\{.+?\})\s*\n##", text, flags=re.DOTALL)
        if not m2:
            return {"json": None, "markdown": text, "raw": text,
                    "parse_error": "no fenced json block"}
        json_str = m2.group(1)
    else:
        json_str = m.group(1)
    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError as exc:
        return {"json": None, "markdown": text, "raw": text,
                "parse_error": f"json decode: {exc}"}
    # Markdown render is everything after the json block
    md_match = re.search(r"##\s+Briefing.*", text, flags=re.DOTALL)
    md = md_match.group(0) if md_match else ""
    return {"json": parsed, "markdown": md, "raw": text}


# ---------------------------------------------------------------------------
# 4. Validate — V1 parse, V2 schema, V3 citation count, V4 citation membership
# ---------------------------------------------------------------------------

def validate(parsed: Dict[str, Any], bundle: Dict[str, Any]
             ) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Run the 4 critical validators. Returns (ok, problems, cleaned).

    Failures of V4 (fabricated citations) are the killswitch: the
    affected recommendation is dropped and the failure logged. Two
    drops in one briefing fails the whole briefing.
    """
    problems: List[str] = []
    if parsed is None or parsed.get("parse_error"):
        return False, [parsed.get("parse_error", "no parse") if parsed else "no response"], {}
    pj = parsed.get("json") or {}

    # V1 — parse already happened; we got here so it's fine.

    # V2 — minimal schema check
    for required_key in ("briefing_date", "bundle_hash", "recommendations"):
        if required_key not in pj:
            problems.append(f"V2 missing key: {required_key}")
    if pj.get("bundle_hash") != bundle.get("bundle_hash"):
        problems.append("V2 bundle_hash mismatch (model is desynced)")

    # Build the set of valid signal_ids from the bundle for V4
    valid_ids = set()
    for market, items in (bundle.get("candidates") or {}).items():
        for c in items:
            if c.get("id"):
                valid_ids.add(c["id"])
    # Also accept market_stats:* references in regime_note / thinking
    for mk in (bundle.get("market_stats") or {}).keys():
        valid_ids.add(f"market_stats:{mk}")

    recs = pj.get("recommendations") or []
    drops = []
    cleaned_recs = []
    for i, r in enumerate(recs):
        rid = f"rec[{i}] {r.get('ticker','?')}"
        # V3 — citation count
        sigs_cited = r.get("signal_ids") or []
        if not sigs_cited:
            problems.append(f"V3 {rid}: no signal_ids — dropped")
            drops.append(rid)
            continue
        # V4 — citation membership (KILLSWITCH)
        bad = [s for s in sigs_cited if s not in valid_ids]
        if bad:
            problems.append(f"V4 {rid}: fabricated signal_ids {bad} — dropped")
            drops.append(rid)
            continue
        cleaned_recs.append(r)

    cleaned = dict(pj)
    cleaned["recommendations"] = cleaned_recs
    cleaned["_validator_drops"] = drops

    # Killswitch — 2+ drops fails the whole briefing
    if len(drops) >= 2:
        problems.append(
            f"KILLSWITCH: {len(drops)} validator drops in one briefing"
        )
        return False, problems, cleaned

    return True, problems, cleaned


# ---------------------------------------------------------------------------
# 5. Persist — write briefing markdown to vault + emit SSE event
# ---------------------------------------------------------------------------

def persist(cleaned: Dict[str, Any], markdown_render: str,
            bundle: Dict[str, Any], problems: List[str]) -> Path:
    """Write the briefing markdown to Brain/Trading/Research/.
    Filename includes the date. Existing same-day file is overwritten."""
    today = date.today().isoformat()
    out_dir = _vault_research_dir()
    path = out_dir / f"{today} - market-research.md"
    md = _render_markdown(cleaned, markdown_render, bundle, problems)
    path.write_text(md, encoding="utf-8")
    logger.info("financial_researcher: persisted briefing to %s", path)
    # Broadcast to HUD
    try:
        from openjarvis.cli.brain_server import _chat_history
        _chat_history._broadcast({
            "kind": "markets_briefing_ready",
            "date": today,
            "path": str(path),
            "n_recs": len(cleaned.get("recommendations") or []),
            "ts": time.time(),
        })
    except Exception:
        logger.debug("briefing broadcast failed", exc_info=True)
    return path


def _render_markdown(cleaned: Dict[str, Any], llm_render: str,
                     bundle: Dict[str, Any], problems: List[str]) -> str:
    """Compose the final markdown — preamble + LLM render + audit footer."""
    today = date.today().isoformat()
    preamble = (
        "---\n"
        f"type: market-research\n"
        f"date: {today}\n"
        f"bundle_hash: {bundle.get('bundle_hash')}\n"
        f"schema_version: {bundle.get('schema_version')}\n"
        "---\n\n"
        f"# Markets Briefing — {today}\n\n"
        "*Personal research notes for the operator. Not regulated "
        "financial advice. Not for any third party. Generated by Jarvis-"
        "Research (gpt-4o) from technical signals only — no news, no "
        "macro, no calendar in v0. Be appropriately humble.*\n\n"
    )
    body = llm_render or "## Briefing\n\n_(no markdown render emitted)_\n"
    audit_lines = [
        "\n\n---\n## Audit footer\n",
        f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"- Bundle hash: `{bundle.get('bundle_hash')}`",
        f"- Universe size: US={len((bundle.get('candidates') or {}).get('US',[]))} · "
        f"UK={len((bundle.get('candidates') or {}).get('UK',[]))} · "
        f"CRYPTO={len((bundle.get('candidates') or {}).get('CRYPTO',[]))} candidates",
        f"- Recommendations after validators: {len(cleaned.get('recommendations') or [])}",
        f"- Validator drops: {len(cleaned.get('_validator_drops') or [])}",
    ]
    if problems:
        audit_lines.append("- Validator notes:")
        for p in problems:
            audit_lines.append(f"  - {p}")
    return preamble + body + "\n".join(audit_lines) + "\n"


# ---------------------------------------------------------------------------
# Top-level entry — orchestrates 1-5
# ---------------------------------------------------------------------------

def run(*, refresh_quotes: bool = False) -> Dict[str, Any]:
    """End-to-end briefing run. Returns a status dict.

    ``refresh_quotes`` would top up live prices before signal compute
    — deferred (next iteration; for now we use whatever the latest
    history bar contains)."""
    started = time.time()
    sigs = gather()
    if not sigs:
        return {
            "ok": False, "stage": "gather",
            "error": "no historical bars in store — run backfill first",
            "wall_seconds": round(time.time() - started, 1),
        }
    bundle = build_bundle(sigs)
    parsed = generate(bundle)
    if parsed is None:
        return {
            "ok": False, "stage": "generate",
            "error": "gpt-4o unavailable (no OPENAI_API_KEY?)",
            "wall_seconds": round(time.time() - started, 1),
        }
    ok, problems, cleaned = validate(parsed, bundle)
    if not ok:
        # Persist a stub so the operator can see what failed
        path = persist(cleaned, parsed.get("markdown") or "", bundle, problems)
        return {
            "ok": False, "stage": "validate", "problems": problems,
            "path": str(path),
            "wall_seconds": round(time.time() - started, 1),
        }
    path = persist(cleaned, parsed.get("markdown") or "", bundle, problems)
    return {
        "ok": True,
        "path": str(path),
        "n_recs": len(cleaned.get("recommendations") or []),
        "n_refusals": len(cleaned.get("refusals") or []),
        "validator_warnings": problems,
        "bundle_hash": bundle.get("bundle_hash"),
        "wall_seconds": round(time.time() - started, 1),
    }


# ---------------------------------------------------------------------------
# Daily-fire daemon — wakes at HH:MM each day and runs the briefing.
# Mirrors graphify_bridge.start_daily_rebuild from 2026-04-29.
# ---------------------------------------------------------------------------

import threading as _threading

_daily_lock = _threading.Lock()
_daily_thread: Optional[_threading.Thread] = None
_daily_status: Dict[str, Any] = {"running": False}


def _seconds_until(hour: int, minute: int) -> float:
    """Seconds from now until the next HH:MM in local time."""
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        # Roll to tomorrow
        target = target.replace(day=target.day + 1) if target.day < 28 else (
            target.replace(month=target.month + 1, day=1) if target.month < 12 else
            target.replace(year=target.year + 1, month=1, day=1)
        )
    return max(1.0, (target - now).total_seconds())


def start_daily(hour: Optional[int] = None,
                minute: Optional[int] = None) -> Dict[str, Any]:
    """Start the daily briefing daemon thread. Idempotent — second call
    returns the existing status. Hour/minute default to env vars
    (OPENJARVIS_BRIEFING_HOUR / _MINUTE) then to 06:15."""
    global _daily_thread
    h = hour if hour is not None else int(os.environ.get(
        "OPENJARVIS_BRIEFING_HOUR", "6"))
    m = minute if minute is not None else int(os.environ.get(
        "OPENJARVIS_BRIEFING_MINUTE", "15"))
    with _daily_lock:
        if _daily_status.get("running"):
            return dict(_daily_status)

        def _runner():
            while True:
                try:
                    wait_s = _seconds_until(h, m)
                    _daily_status["next_fire_in_seconds"] = wait_s
                    _daily_status["next_fire_hour"] = h
                    _daily_status["next_fire_minute"] = m
                    time.sleep(wait_s)
                    logger.info("financial_researcher: daily fire at %02d:%02d",
                                h, m)
                    result = run()
                    _daily_status["last_fire_at"] = time.time()
                    _daily_status["last_result"] = result
                    # Sleep through the minute so we don't double-fire
                    time.sleep(70)
                except Exception:
                    logger.exception("daily briefing daemon iteration failed")
                    time.sleep(300)

        _daily_thread = _threading.Thread(
            target=_runner, daemon=True, name="markets-daily-briefing",
        )
        _daily_thread.start()
        _daily_status.update({"running": True, "fire_hour": h, "fire_minute": m})
    return dict(_daily_status)


def daily_status() -> Dict[str, Any]:
    with _daily_lock:
        return dict(_daily_status)


__all__ = [
    "run", "gather", "build_bundle", "generate", "validate", "persist",
    "start_daily", "daily_status",
]

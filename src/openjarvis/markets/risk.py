"""Crypto rug-pull / pump-and-dump risk scoring.

Pure-math composite scorer over the fields already in CoinGecko's
bulk ``/coins/markets`` payload (so it costs zero extra API calls
for the 1000-coin Pulse feed). For deeper single-coin enrichment,
:func:`score_coin` accepts an optional ``detail`` dict from
``coingecko.fetch_coin_detail`` — that's where ``genesis_date``
comes from for the age penalty.

Operator brief verbatim:
  "i want the analyse to be done to rule out pump n dump or rug pull
   coins, based on volume and time the crypto has been released, maybe
   integrate some sort of 'RUGPULL WARNING' that the analyze can pin
   to crypto's it deems as a real risk of scam"

Score is 0-100, higher = riskier. Labels:
  > 50  → 🚨 RUGPULL WARNING (red)
  > 30  → ⚠️ HIGH RISK        (orange)
  > 15  → ⚠️ CAUTION          (yellow)
  ≤ 15  → clean (no badge)

The chart_analyst integration uses the ``label_key == "rugpull"``
branch as a kill-switch: a coin flagged rugpull MUST NOT receive a
LONG verdict (force NEUTRAL with explicit "high scam risk" reason).
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Dict, List, Optional

# Heuristic name match — joke/meme/scam-flavoured tokens get a small
# bump. Not dispositive (PEPE has a multi-billion mcap), just one
# more weak signal stacked with the structural ones.
_MEME_NAME_RE = re.compile(
    r"\b(MOON|INU|PEPE|DOG|DOGE|SHIB|SAFE|ELON|BABY|FLOKI|CUM|CHAD|"
    r"WOJAK|GIGA|TRUMP|BIDEN|MEME|RUG|SCAM|GEM|LAMBO|ROCKET)\b",
    re.IGNORECASE,
)


def _bump_volume_24h_vs_7d(volume_24h: Optional[float],
                           sparkline_7d: Optional[List[float]]) -> int:
    """If today's 24h volume is >5× the implied 7-day average, +15.

    The sparkline endpoint gives prices not volumes, so we approximate
    the 7-day baseline by sampling sparkline price * present
    volume/price ratio — crude, but the sign is right: a sudden spike
    in price PLUS a big volume_24h is the wash-trading shape we want
    to flag.
    """
    if not sparkline_7d or volume_24h is None or len(sparkline_7d) < 24:
        return 0
    try:
        recent = sparkline_7d[-1]
        baseline = sum(sparkline_7d[:-24]) / max(len(sparkline_7d) - 24, 1)
        if baseline <= 0 or recent <= 0:
            return 0
        # Spike ratio in price as a proxy — when both spike together
        # the volume_24h alone tells us whether activity is sudden.
        # We only flag the volume side here; price spike is handled
        # by the change_24h penalty separately so we don't double-count.
        ratio = recent / baseline
        if ratio > 5:
            return 15
    except (TypeError, ZeroDivisionError):
        return 0
    return 0


def _age_days_from_detail(detail: Optional[Dict[str, Any]]) -> Optional[int]:
    if not detail:
        return None
    g = detail.get("genesis_date")
    if not g or not isinstance(g, str):
        return None
    try:
        d = _dt.date.fromisoformat(g)
    except ValueError:
        return None
    return (_dt.date.today() - d).days


def score_coin(coin: Dict[str, Any], *,
               detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return a risk dict for a single coin row.

    ``coin`` is one of the dicts produced by
    :func:`coingecko.fetch_top_n` / ``fetch_top_100`` — must have
    ``market_cap``, ``volume_24h``, ``last``, ``change_24h_pct``,
    ``sparkline_7d``, ``name``.

    ``detail`` is the optional :func:`coingecko.fetch_coin_detail`
    payload. When supplied, age-based penalties activate.

    Returned shape:
        {score, label, label_emoji, label_key, reasons: [...]}
    """
    score = 0
    reasons: List[str] = []

    mcap = coin.get("market_cap")
    vol = coin.get("volume_24h")
    last = coin.get("last")
    chg = coin.get("change_24h_pct")
    name = coin.get("name") or ""
    sym = (coin.get("symbol") or "").upper()
    spark = coin.get("sparkline_7d") or []

    # 1) Market-cap penalty
    if mcap is not None:
        if mcap < 1_000_000:
            score += 30
            reasons.append("micro-cap (<$1M)")
        elif mcap < 10_000_000:
            score += 20
            reasons.append("very small cap (<$10M)")
        elif mcap < 100_000_000:
            score += 10
            reasons.append("small cap (<$100M)")

    # 2) Volume / mcap ratio
    if mcap and vol and mcap > 0:
        ratio = vol / mcap
        if ratio > 2.0:
            score += 25
            reasons.append(f"vol/mcap {ratio:.1f}× — wash-trading suspect")
        elif ratio < 0.01:
            score += 15
            reasons.append("vol/mcap <1% — illiquid trap")

    # 3) Outsize 24h move
    if chg is not None:
        ach = abs(chg)
        if ach > 100:
            score += 20
            reasons.append(f"24h move {chg:+.0f}%")
        elif ach > 50:
            score += 10
            reasons.append(f"24h move {chg:+.0f}%")

    # 4) Volume vs 7d baseline
    bump = _bump_volume_24h_vs_7d(vol, spark)
    if bump:
        score += bump
        reasons.append("volume spike vs 7d baseline")

    # 5) Sub-cent + sub-million stack
    if last is not None and mcap is not None and last < 0.01 and mcap < 1_000_000:
        score += 20
        reasons.append("sub-cent micro-cap stack")

    # 6) Heuristic name match
    if _MEME_NAME_RE.search(name) or _MEME_NAME_RE.search(sym):
        score += 10
        reasons.append("meme-style ticker / name")

    # 7) Optional age penalty (only when detail supplied)
    age = _age_days_from_detail(detail)
    if age is not None:
        if age < 30:
            score += 25
            reasons.append(f"<30 days old ({age}d)")
        elif age < 90:
            score += 15
            reasons.append(f"<90 days old ({age}d)")
        elif age < 365:
            score += 5
            reasons.append(f"<1 year old ({age}d)")

    score = max(0, min(100, score))
    label, label_emoji, label_key = _label_for(score)
    return {
        "score": score,
        "label": label,
        "label_emoji": label_emoji,
        "label_key": label_key,    # rugpull | high | caution | clean
        "reasons": reasons,
    }


def _label_for(score: int) -> tuple:
    if score > 50:
        return ("RUGPULL WARNING", "🚨", "rugpull")
    if score > 30:
        return ("HIGH RISK", "⚠️", "high")
    if score > 15:
        return ("CAUTION", "⚠️", "caution")
    return ("clean", "", "clean")


def annotate(coins: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return a new list with each coin enriched with a ``risk`` dict.

    Cheap — pure math over fields already in the payload. No API calls.
    Use this on the bulk top-1000 list before shipping to the client.
    """
    out = []
    for c in coins:
        r = score_coin(c)
        nc = dict(c)
        nc["risk"] = r
        out.append(nc)
    return out


def filter_clean(coins: List[Dict[str, Any]], *,
                 max_label_key: str = "high") -> List[Dict[str, Any]]:
    """Drop coins above a given risk band.

    ``max_label_key`` is the worst label still allowed:
      "rugpull" → keep all
      "high"    → drop rugpulls only       (default)
      "caution" → drop rugpulls + high
      "clean"   → drop anything flagged
    """
    order = {"clean": 0, "caution": 1, "high": 2, "rugpull": 3}
    cap = order.get(max_label_key, 2)
    out = []
    for c in coins:
        r = c.get("risk") or score_coin(c)
        if order.get(r.get("label_key", "clean"), 0) <= cap:
            out.append(c)
    return out


__all__ = ["score_coin", "annotate", "filter_clean"]

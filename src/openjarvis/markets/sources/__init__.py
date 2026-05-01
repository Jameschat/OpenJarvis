"""Market-data fetchers — one module per source.

Day-1 free-tier sources:
  - yf       yfinance (US + UK equities, free, fragile, ToS-grey)
  - kraken   Kraken public REST (crypto GBP pairs, free, no key)

Next session: rss (news), fred (macro), fmp (calendar).

Every source exposes a small surface: ``fetch_quote(ticker)`` returning
a normalised dict, plus an ``is_available()`` health check. Caller is
responsible for caching, retries, and backoff — sources are dumb
fetchers.
"""

"""markets — financial trader Jarvis subsystem.

Day-1 paper-trading shape: data ingestion + LLM tool surface + (next
session) gpt-4o briefing pipeline + HUD wiring. Per the master plan in
``Brain/Decisions/2026-04-30 - Financial Jarvis - master plan from 7
planning agents.md``.

Modules:
  - store        SQLite DAO at ~/.openjarvis/markets/markets.db
  - sources/     per-feed fetchers (yfinance, Kraken, RSS planned)
  - markets_tools  LLM-callable functions (registered in tool_use.py)

Day-1 is paper-only — no broker integration, no real positions, no tax
ledger. Outcome tracking IS the product: did the LLM's hypothetical
picks beat benchmarks?
"""

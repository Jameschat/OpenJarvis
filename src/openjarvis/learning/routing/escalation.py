"""Escalation policy — pure decisions for model/lane escalation.

Inputs: the complexity tier (from complexity.score_complexity), a quality score
(from qwen_quality_loop.assess_qwen_output), and the operator's preference. No
network or I/O — just the decision logic, so it is fully unit-testable. The live
routing path (Slice 4B) consumes these to pick a target and emit routing/
escalation events.
"""

from __future__ import annotations

from typing import Optional

# Escalation ladder, weakest → strongest.
LADDER = ["local_fast", "local_coder", "remote", "cloud"]

_TIER_RANK = {
    "trivial": 0,
    "simple": 1,
    "moderate": 2,
    "complex": 3,
    "very_complex": 4,
}

# Quality score below this → escalate. Lower preference = more tolerant of a
# weak local answer (stays local); higher = escalates eagerly for quality.
_THRESHOLD = {"local": 50, "balanced": 75, "best": 85}


def initial_target(tier: str, preference: str) -> str:
    """Pick the starting rung from the complexity tier + preference."""
    rank = _TIER_RANK.get(tier, 2)
    if preference == "local":
        return "local_fast"
    if preference == "best":
        return "remote" if rank >= 3 else "local_fast"
    # balanced: stay local unless the prompt is very complex
    return "remote" if rank >= 4 else "local_fast"


def should_escalate(score: int, needs_escalation: bool, preference: str) -> bool:
    """Decide whether a completed answer warrants escalation."""
    if needs_escalation:
        return True
    return score < _THRESHOLD.get(preference, 75)


def next_target(current: str) -> Optional[str]:
    """Return the next-stronger rung, or None if already at the top/unknown."""
    try:
        i = LADDER.index(current)
    except ValueError:
        return None
    return LADDER[i + 1] if i + 1 < len(LADDER) else None


def resolve_brain(model: str) -> dict:
    """Map a model id to a UI 'brain' descriptor {brain, lane, model} for the
    routing/brain-pill display. Pure; no I/O."""
    m = (model or "").lower()
    if m.startswith(("gpt-", "o1-", "o3-", "o4-", "chatgpt-")):
        return {"brain": "cloud", "lane": "openai", "model": model}
    if m.startswith("claude-"):
        return {"brain": "cloud", "lane": "anthropic", "model": model}
    if m.startswith("gemini-"):
        return {"brain": "cloud", "lane": "gemini", "model": model}
    if "remote" in m or "35b" in m:
        return {"brain": "remote", "lane": "35B", "model": model}
    if "coder" in m or "30b" in m:
        return {"brain": "local", "lane": "30B", "model": model}
    if "27b" in m or "qwen" in m:
        return {"brain": "local", "lane": "27B", "model": model}
    return {"brain": "local", "lane": "", "model": model}


__all__ = ["LADDER", "initial_target", "should_escalate", "next_target", "resolve_brain"]

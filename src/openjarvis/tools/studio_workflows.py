from __future__ import annotations

import re
from typing import Any

BUG_TERMS = {"bug", "fix", "error", "failed", "failure", "broken", "regression", "http 500"}
RESEARCH_TERMS = {"research", "find", "compare", "look up", "watchlist", "recommend"}
BUILD_TERMS = {"build", "create", "implement", "add", "make"}
LARGE_TERMS = {"complete", "full", "replica", "platform", "operating layer", "through to completion"}
PLANNING_TERMS = {"plan", "planning", "idea", "thinking", "project", "proposal"}
EXTERNAL_ACTION_RE = re.compile(
    r"\b("
    r"install|uninstall|connect\s+(?:my\s+)?(?:exchange|account|wallet)|"
    r"delete|remove|trade|buy|sell|spend|"
    r"(?:add|use|store|include|update)\s+(?:my\s+)?(?:key|secret|token|password)"
    r")\b",
    re.IGNORECASE,
)


def _has_any(text: str, terms: set[str]) -> bool:
    lower = text.lower()
    return any(term in lower for term in terms)


def select_workflow(prompt: str) -> dict[str, Any]:
    text = (prompt or "").strip()
    lower = text.lower()
    risks: list[str] = []
    approval = False
    if EXTERNAL_ACTION_RE.search(text):
        approval = True
        risks.append("External/account/destructive capability requires explicit operator approval.")

    if _has_any(text, BUG_TERMS):
        workflow = "debug"
        reason = "Bug or failure language requires reproduce -> root cause -> fix -> regression verification."
        next_steps = [
            "Reproduce the failure with the smallest command or request.",
            "Identify root cause.",
            "Patch and run regression verification.",
        ]
    elif _has_any(text, BUILD_TERMS) and _has_any(text, LARGE_TERMS):
        workflow = "spec"
        approval = True
        reason = "Large product build needs an approved spec and plan before execution."
        next_steps = [
            "Write/confirm spec.",
            "Create implementation plan.",
            "Execute in reviewed slices.",
        ]
    elif _has_any(text, RESEARCH_TERMS) or _has_any(text, PLANNING_TERMS):
        workflow = "qwen_workflow"
        reason = "Research/planning task is safe for local Qwen with memory context."
        next_steps = [
            "Build project context.",
            "Run Qwen research/planning workflow.",
            "Write memory summary.",
        ]
    elif re.search(r"\b(test|verify|review|audit)\b", lower):
        workflow = "verify"
        reason = "Request is verification-focused."
        next_steps = ["Collect evidence.", "Report pass/fail and residual risk."]
    else:
        workflow = "execute"
        reason = "Single direct task with normal verification."
        next_steps = ["Build context.", "Run task.", "Verify evidence.", "Write memory."]

    return {
        "workflow": workflow,
        "reason": reason,
        "model": "qwen3.6-27b-local",
        "requires_operator_approval": approval,
        "risks": risks,
        "verification": {"required": True, "method": "evidence"},
        "next_steps": next_steps,
    }

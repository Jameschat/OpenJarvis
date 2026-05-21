"""Memory-grounded cognitive coaching for Jarvis.

This layer does not add another memory store. It uses the existing vault,
agentmemory, Graphify/CodeGraph-visible context, and daily journals to turn
loose thinking into sharper questions, decisions, and next actions.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_CODE_TERMS = {
    "code", "app", "web", "website", "python", "javascript", "typescript",
    "bug", "test", "repo", "jarvis", "function", "module", "plugin",
}


def _tokens(text: str) -> List[str]:
    return [t.lower() for t in re.findall(r"[a-zA-Z0-9_]{3,}", text or "")]


def _split_sentences(text: str, limit: int = 6) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return [p.strip() for p in parts if p.strip()][:limit]


def _is_code_related(text: str) -> bool:
    toks = set(_tokens(text))
    return bool(toks & _CODE_TERMS)


def _vault_hits(prompt: str, limit: int = 4) -> List[Dict[str, str]]:
    try:
        from openjarvis.tools import obsidian_brain
        hits = obsidian_brain.recall(prompt, limit=limit)
    except Exception:
        return []
    out: List[Dict[str, str]] = []
    for path, snippet in hits:
        try:
            rel = path.relative_to(obsidian_brain.BRAIN_ROOT).as_posix()
        except Exception:
            rel = path.name
        out.append({"source": "vault", "path": rel, "snippet": snippet[:360]})
    return out


def _episodic_hits(prompt: str, limit: int = 3) -> List[Dict[str, str]]:
    try:
        from openjarvis.tools.agentmemory_client import search
        hits = search(prompt, limit=limit)
    except Exception:
        return []
    return [
        {
            "source": "agentmemory",
            "path": h.session_id,
            "snippet": h.snippet[:360],
        }
        for h in hits
    ]


def _codegraph_signal(prompt: str) -> Optional[Dict[str, Any]]:
    if not _is_code_related(prompt):
        return None
    try:
        from openjarvis.cli.brain_server import _codegraph_status
        status = _codegraph_status()
    except Exception:
        return None
    return {
        "source": "codegraph",
        "online": bool(status.get("online")),
        "files": int(status.get("files") or 0),
        "nodes": int(status.get("nodes") or 0),
        "edges": int(status.get("edges") or 0),
    }


def _memory_signals(prompt: str) -> Dict[str, Any]:
    vault = _vault_hits(prompt)
    episodic = _episodic_hits(prompt)
    codegraph = _codegraph_signal(prompt)
    return {
        "vault": vault,
        "episodic": episodic,
        "codegraph": codegraph,
        "count": len(vault) + len(episodic) + (1 if codegraph else 0),
    }


def _challenge_assumptions(prompt: str, mode: str) -> List[str]:
    lower = (prompt or "").lower()
    assumptions: List[str] = []
    if any(w in lower for w in ("should", "need", "must", "best")):
        assumptions.append("You may be treating a preference as a requirement. State what would break if you did nothing.")
    if any(w in lower for w in ("upgrade", "add", "build", "create", "switch")):
        assumptions.append("New capability is not automatically progress. Define the smallest proof that it improves Jarvis.")
    if any(w in lower for w in ("profit", "trading", "market", "bot")):
        assumptions.append("Backtest results can overfit. Separate historical fit, live paper behavior, and real execution risk.")
    if any(w in lower for w in ("autonomous", "self", "evolve", "learn")):
        assumptions.append("Autonomy needs measurable feedback loops, rollback paths, and approval gates for irreversible actions.")
    if mode == "decision":
        assumptions.append("A decision should name the reversible path and the condition that would change your mind.")
    if not assumptions:
        assumptions.append("The missing assumption is probably the success measure. Define what good looks like before acting.")
    return assumptions[:4]


def _better_question(prompt: str, mode: str) -> str:
    if mode == "pressure_test":
        return "What evidence would prove this idea is wrong, expensive, or not worth doing yet?"
    if mode == "decision":
        return "What is the smallest reversible decision that moves this forward without hiding the main risk?"
    if mode == "reflection":
        return "What pattern from recent memory am I repeating, and what would I do differently this time?"
    return "What outcome am I actually optimizing for, and what evidence from memory supports that path?"


def _next_action(prompt: str, memory: Dict[str, Any], mode: str) -> str:
    if memory["count"] == 0:
        return "Write a short Brain note defining the goal, constraints, and first evidence target before taking action."
    if mode == "decision":
        return "Create a decision note with options, risks, reversible first step, and review date."
    if mode == "pressure_test":
        return "Run one small test that could falsify the idea, then save the result to the Brain."
    if memory.get("codegraph"):
        return "Use CodeGraph first to inspect the affected code paths, then make the smallest tested change."
    return "Use the retrieved memory as evidence, pick one next action, and record what would change the plan."


def cognitive_check(
    prompt: str,
    mode: str = "coach",
    stakes: str = "medium",
) -> Dict[str, Any]:
    """Return a memory-grounded thinking frame for a prompt."""
    mode = (mode or "coach").lower()
    if mode not in {"coach", "decision", "pressure_test", "reflection", "plan"}:
        mode = "coach"
    memory = _memory_signals(prompt)
    snippets = memory["vault"] + memory["episodic"]
    signals = [
        {
            "source": item["source"],
            "path": item["path"],
            "signal": _split_sentences(item["snippet"], limit=1)[0] if item["snippet"] else "",
        }
        for item in snippets[:5]
    ]
    if memory.get("codegraph"):
        cg = memory["codegraph"]
        signals.append({
            "source": "codegraph",
            "path": "E:/Claude/OpenJarvis/.codegraph/codegraph.db",
            "signal": f"Source graph online: {cg['files']} files, {cg['nodes']} nodes, {cg['edges']} edges.",
        })

    return {
        "ok": True,
        "mode": mode,
        "stakes": stakes,
        "prompt_summary": (prompt or "").strip()[:500],
        "memory_signals": signals,
        "assumption_checks": _challenge_assumptions(prompt, mode),
        "better_question": _better_question(prompt, mode),
        "decision_frame": [
            "Goal: name the result, not the activity.",
            "Evidence: separate memory-backed facts from guesses.",
            "Risk: identify what could waste time, money, trust, or working systems.",
            "Reversibility: prefer the smallest rollback-safe test first.",
            "Review: decide when Jarvis should revisit the outcome.",
        ],
        "next_action": _next_action(prompt, memory, mode),
    }


def format_check_markdown(result: Dict[str, Any]) -> str:
    lines = [
        "## Cognitive check",
        "",
        f"- **Mode:** {result.get('mode')}",
        f"- **Stakes:** {result.get('stakes')}",
        f"- **Better question:** {result.get('better_question')}",
        "",
        "### Memory signals",
    ]
    signals = result.get("memory_signals") or []
    if signals:
        for item in signals:
            lines.append(f"- **{item.get('source')}** `{item.get('path')}`: {item.get('signal')}")
    else:
        lines.append("- No strong memory signal found. Treat this as a fresh assumption until evidence is added.")
    lines.extend(["", "### Assumption checks"])
    for item in result.get("assumption_checks") or []:
        lines.append(f"- {item}")
    lines.extend(["", "### Decision frame"])
    for item in result.get("decision_frame") or []:
        lines.append(f"- {item}")
    lines.extend(["", f"### Next action\n\n{result.get('next_action')}"])
    return "\n".join(lines) + "\n"


def build_daily_review(date_str: Optional[str] = None) -> str:
    try:
        from openjarvis.tools import obsidian_brain as ob
    except Exception:
        ob = None
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    daily_text = ""
    if ob is not None:
        p = ob.BRAIN_ROOT / "Daily" / f"{date_str}.md"
        if p.exists():
            daily_text = p.read_text(encoding="utf-8", errors="replace")
    prompts = _split_sentences(daily_text.replace("\n", " "), limit=8)
    checks = [
        cognitive_check(text, mode="reflection", stakes="low")
        for text in prompts[:5]
        if len(text) > 40
    ]
    lines = [
        "---",
        "type: knowledge",
        f"date: {date_str}",
        "tags: [jarvis, cognitive, thinking, memory]",
        "parent: [[00 Session Handoff]]",
        "---",
        "",
        f"# Jarvis cognitive review - {date_str}",
        "",
        "## Purpose",
        "",
        "Use memory to make tomorrow's thinking sharper: fewer vague goals, clearer risks, smaller tests.",
        "",
        "## Prompts",
        "",
    ]
    if not checks:
        lines.append("- No substantial daily entries found yet. Ask Jarvis to pressure-test one active decision.")
    for check in checks:
        lines.append(f"- {check['better_question']}")
        for assumption in check["assumption_checks"][:2]:
            lines.append(f"  - {assumption}")
    lines.extend([
        "",
        "## Tomorrow's Thinking Rule",
        "",
        "Before large changes, ask: goal, evidence, risk, reversibility, smallest safe test.",
        "",
        "_Compiled by `cognitive-coach`._",
    ])
    return "\n".join(lines) + "\n"


def write_daily_review(markdown: str, date_str: Optional[str] = None) -> Optional[Path]:
    try:
        from openjarvis.tools import obsidian_brain as ob
    except Exception:
        return None
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    target = ob.BRAIN_ROOT / "Knowledge" / f"{date_str} - Jarvis cognitive review.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown, encoding="utf-8")
    return target


def run(date_str: Optional[str] = None) -> Dict[str, Any]:
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    md = build_daily_review(date_str=date_str)
    path = write_daily_review(md, date_str=date_str)
    return {"ok": path is not None, "path": str(path) if path else None}


def run_as_agent_task(prompt: str = "") -> Dict[str, Any]:
    return run()


__all__ = [
    "cognitive_check",
    "format_check_markdown",
    "build_daily_review",
    "write_daily_review",
    "run",
    "run_as_agent_task",
]

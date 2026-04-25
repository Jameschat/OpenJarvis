"""Tool-using LLM mode for J.A.R.V.I.S.

Wraps gpt-4o-mini's native function-calling so the fallback brain can
take actions on its own — recall vault notes, save facts, list agents,
dispatch tasks — instead of just responding with text. The fast-paths in
``voice_cmd`` still short-circuit common phrases for instant deterministic
responses; this module only kicks in when control would otherwise reach
``llm_fallback.generate_fallback``.

Public surface:

``generate_with_tools(messages, fallback_engine, fallback_model)``
    Drop-in replacement for ``generate_fallback`` with the same signature.
    Falls back to plain ``generate_fallback`` when the OpenAI client is
    unavailable or any tool-loop step fails.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

from openjarvis.cli.llm_fallback import (
    DEFAULT_OPENAI_MODEL,
    MAX_OUTPUT_TOKENS,
    _get_openai_client,
    _messages_to_openai,
    generate_fallback,
)

logger = logging.getLogger(__name__)

# Cap loop iterations so a misbehaving model can't burn budget. Each
# iteration is one round-trip to gpt-4o-mini plus zero or more tool runs.
MAX_TOOL_ITERATIONS = 4


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI chat-completions tool format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "recall_vault",
            "description": (
                "Search the operator's Obsidian vault (second brain) for notes "
                "matching the query. Use whenever the operator references past "
                "decisions, facts, projects, people, or asks 'what do you know "
                "about X'. Returns ranked snippets with file paths."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords to search. Whole-word AND match.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of hits to return (default 5).",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_fact",
            "description": (
                "Save a fact to the vault as a new markdown note. Use when the "
                "operator says 'remember that X' or supplies information worth "
                "preserving across sessions. Do NOT use for transient task state."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short title (~60 chars). Becomes the note's filename.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full note body in markdown. Be specific and complete.",
                    },
                    "folder": {
                        "type": "string",
                        "description": "Vault subfolder. One of: Knowledge, Projects, People, Decisions.",
                        "enum": ["Knowledge", "Projects", "People", "Decisions"],
                        "default": "Knowledge",
                    },
                },
                "required": ["title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_agents",
            "description": (
                "List the available agent roster with their roles and current "
                "busy/idle status. Use before dispatching a task to pick the "
                "right agent and confirm they're free."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dispatch_agent",
            "description": (
                "Spawn a task on a named agent. The task runs asynchronously in "
                "its own workspace and writes a session note when complete. Use "
                "for substantive work like 'spin up an agent to research X' or "
                "'have the architect plan Y'. The operator does NOT need to "
                "wait for completion — confirm dispatch and move on."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent": {
                        "type": "string",
                        "description": "Agent id from list_agents (e.g. 'architect', 'backend-dev').",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Self-contained task description for the agent.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short label (~60 chars) for the task in the HUD.",
                    },
                },
                "required": ["agent", "prompt", "title"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool executors
# ---------------------------------------------------------------------------

def _tool_recall_vault(query: str, limit: int = 5) -> str:
    from openjarvis.tools import obsidian_brain
    hits = obsidian_brain.recall(query, limit=int(limit))
    if not hits:
        return json.dumps({"hits": [], "note": "no matches"})
    out = []
    for path, snippet in hits:
        try:
            rel = path.relative_to(obsidian_brain.BRAIN_ROOT).as_posix()
        except Exception:
            rel = path.name
        out.append({"path": rel, "snippet": snippet[:300]})
    return json.dumps({"hits": out})


def _tool_remember_fact(title: str, content: str, folder: str = "Knowledge") -> str:
    from openjarvis.tools import obsidian_brain
    if folder not in {"Knowledge", "Projects", "People", "Decisions"}:
        folder = "Knowledge"
    path = obsidian_brain.remember(content=content, title=title, folder=folder)
    if path is None:
        return json.dumps({"ok": False, "error": "vault unavailable"})
    try:
        rel = path.relative_to(obsidian_brain.BRAIN_ROOT).as_posix()
    except Exception:
        rel = path.name
    return json.dumps({"ok": True, "path": rel})


def _tool_list_agents() -> str:
    from openjarvis.tools import agent_runner
    snap = agent_runner.get_snapshot()
    busy_ids = {a.get("id") for a in (snap.get("agents") or []) if a.get("state") == "running"}
    roster = []
    for a in agent_runner.list_agents():
        roster.append({
            "id": a.get("id"),
            "role": a.get("role"),
            "provider": a.get("provider"),
            "busy": a.get("id") in busy_ids,
        })
    return json.dumps({"agents": roster})


def _tool_dispatch_agent(agent: str, prompt: str, title: str) -> str:
    from openjarvis.tools import agent_runner
    valid_ids = {a["id"] for a in agent_runner.list_agents()}
    if agent not in valid_ids:
        return json.dumps({
            "ok": False,
            "error": f"unknown agent '{agent}'",
            "valid": sorted(valid_ids),
        })
    task_id = agent_runner.add_task(title=title[:80], agent_id=agent, prompt=prompt)
    return json.dumps({"ok": True, "task_id": task_id, "agent": agent})


_TOOL_DISPATCH = {
    "recall_vault": _tool_recall_vault,
    "remember_fact": _tool_remember_fact,
    "list_agents": _tool_list_agents,
    "dispatch_agent": _tool_dispatch_agent,
}


# ---------------------------------------------------------------------------
# Bridge to the Agno-style ToolRegistry — exposes a curated whitelist of
# voice-friendly tools (apps, media, weather, crypto, etc.) to gpt-4o-mini.
#
# Excluded by design: shell_exec, browser_*, apply_patch, file_write,
# git_commit, code_interpreter — those have side effects that should be
# agent-dispatched (with the user's explicit "spin up an agent to..." voice
# command), not chosen autonomously by an LLM mid-conversation.
# ---------------------------------------------------------------------------

_AGNO_TOOL_WHITELIST = (
    "weather", "crypto", "calculator", "app_launcher",
    "music_control", "hue_lights", "lutron", "sonos", "calendar",
)

_agno_loaded = False
_agno_schemas: List[Dict[str, Any]] = []
_agno_instances: Dict[str, Any] = {}


def _load_agno_tools() -> None:
    """Instantiate whitelisted Agno tools once, cache schemas + instances."""
    global _agno_loaded
    if _agno_loaded:
        return
    _agno_loaded = True
    try:
        import openjarvis.tools  # noqa: F401  — triggers @register decorators
        from openjarvis.core.registry import ToolRegistry
    except Exception as exc:
        logger.warning("tool-use: agno bridge import failed: %s", exc)
        return
    for name in _AGNO_TOOL_WHITELIST:
        try:
            if not ToolRegistry.contains(name):
                continue
            cls_or_inst = ToolRegistry.get(name)
            inst = cls_or_inst() if isinstance(cls_or_inst, type) else cls_or_inst
            schema = inst.to_openai_function()
            _agno_schemas.append(schema)
            _agno_instances[schema["function"]["name"]] = inst
        except Exception as exc:
            logger.warning("tool-use: agno tool '%s' load failed: %s", name, exc)
    if _agno_instances:
        logger.info("tool-use: loaded %d agno tools (%s)",
                    len(_agno_instances), ", ".join(_agno_instances.keys()))


def _run_agno_tool(name: str, args: dict) -> str:
    inst = _agno_instances.get(name)
    if inst is None:
        return json.dumps({"error": f"unknown tool '{name}'"})
    try:
        result = inst.execute(**args)
    except TypeError as exc:
        return json.dumps({"error": f"bad arguments: {exc}"})
    except Exception as exc:
        logger.exception("agno tool '%s' raised", name)
        return json.dumps({"error": str(exc)})
    content = getattr(result, "content", None)
    success = getattr(result, "success", True)
    if isinstance(content, (dict, list)):
        return json.dumps({"ok": success, "result": content})
    return json.dumps({"ok": success, "result": str(content) if content is not None else ""})


def _run_tool(name: str, arguments_json: str) -> str:
    try:
        args = json.loads(arguments_json or "{}")
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"bad arguments json: {exc}"})
    if not isinstance(args, dict):
        return json.dumps({"error": "arguments must be a JSON object"})

    fn = _TOOL_DISPATCH.get(name)
    if fn is not None:
        try:
            return fn(**args)
        except TypeError as exc:
            return json.dumps({"error": f"bad arguments: {exc}"})
        except Exception as exc:
            logger.exception("tool '%s' raised", name)
            return json.dumps({"error": str(exc)})

    if name in _agno_instances:
        return _run_agno_tool(name, args)

    return json.dumps({"error": f"unknown tool '{name}'"})


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

_TOOL_USE_DISABLED = os.environ.get("OPENJARVIS_DISABLE_TOOL_USE", "").lower() in {"1", "true", "yes"}


def generate_with_tools(messages: Sequence, fallback_engine: Any = None,
                        fallback_model: str = "") -> str:
    """Run the tool-use loop. Drop-in replacement for ``generate_fallback``."""
    if _TOOL_USE_DISABLED:
        return generate_fallback(messages, fallback_engine=fallback_engine,
                                 fallback_model=fallback_model)

    client = _get_openai_client()
    if client is None:
        return generate_fallback(messages, fallback_engine=fallback_engine,
                                 fallback_model=fallback_model)

    _load_agno_tools()
    all_tools = TOOL_SCHEMAS + _agno_schemas
    msgs = list(_messages_to_openai(messages))

    try:
        for iteration in range(MAX_TOOL_ITERATIONS):
            resp = client.chat.completions.create(
                model=DEFAULT_OPENAI_MODEL,
                messages=msgs,
                tools=all_tools,
                tool_choice="auto",
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=0.7,
            )
            choice = resp.choices[0]
            msg = choice.message
            tool_calls = getattr(msg, "tool_calls", None) or []

            if not tool_calls:
                return (msg.content or "").strip() or "Done, sir."

            # Append the assistant message verbatim so tool_call_ids resolve
            msgs.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments or "{}",
                        },
                    }
                    for tc in tool_calls
                ],
            })

            for tc in tool_calls:
                name = tc.function.name
                args = tc.function.arguments or "{}"
                logger.info("tool-use: %s(%s)", name, args[:200])
                result = _run_tool(name, args)
                msgs.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        # Iteration cap hit — ask once more without tools to force a text reply
        final = client.chat.completions.create(
            model=DEFAULT_OPENAI_MODEL,
            messages=msgs,
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.7,
        )
        return (final.choices[0].message.content or "").strip() or "Done, sir."

    except Exception as exc:
        logger.warning("tool-use loop failed (%s) — falling back to plain LLM", exc)
        return generate_fallback(messages, fallback_engine=fallback_engine,
                                 fallback_model=fallback_model)


__all__ = ["generate_with_tools", "TOOL_SCHEMAS"]

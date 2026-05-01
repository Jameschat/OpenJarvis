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

import html
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote_plus

from openjarvis.cli.llm_fallback import (
    DEFAULT_OPENAI_MODEL,
    MAX_OUTPUT_TOKENS,
    _get_openai_client,
    _messages_to_openai,
    generate_fallback,
)

logger = logging.getLogger(__name__)

# Cap loop iterations so a misbehaving model can't burn budget. Each
# iteration is one round-trip to the conversational LLM plus zero or
# more tool runs. Bumped 8 -> 16 (2026-04-28) so multi-step autonomy
# plans (calendar + sonos + lights + team-task chains, "plan a trip"-
# style queries) don't hit the cap mid-execution. The brain is now
# gpt-4o (was gpt-4o-mini) which is strong enough to converge in
# fewer iterations on simple turns; the headroom matters for the rare
# complex chain. Per-query ceiling still bounded to ~60s and ~£0.10.
MAX_TOOL_ITERATIONS = 16


# Addendum spliced in front of the operator's persona for tool-use turns.
# The voice persona insists on 1-3 sentence responses with no markdown —
# correct for chitchat, wrong for "research X then dispatch Y" requests
# that need the model to actually use its tools instead of describing
# what it would do. This addendum explicitly overrides the brevity rule
# for the planning/tool-calling phase while preserving it for the final
# spoken reply.
_TOOL_USE_ADDENDUM = """\
TOOL-USE MODE
=============
You have native function-calling tools available. ACT first, summarise after.

Rules:
- For any request that involves looking something up, saving information, \
dispatching agents, or controlling devices: CALL THE RELEVANT TOOL. Do not \
describe what you would do — do it.
- Chain tools freely when a request needs multiple steps (e.g. web_search \
then fetch_url on the best hit then remember_fact then dispatch_agent).
- The brevity rule (1-3 sentences, no markdown) applies ONLY to the FINAL \
spoken reply after tools have run. Until then, drive the work to completion.
- For multi-agent project work, pass the same project_id to every \
dispatch_agent call so they share a workspace and leave a vault trail.
- When you cite a fact you looked up, mention the source briefly so the \
operator knows where it came from.
- Failure of one tool does not mean give up — try an alternative or report \
specifically what went wrong.
- BE SOURCE-CRITICAL when researching products, libraries, or tools. A \
vendor's own page ranking themselves #1 is marketing, not evidence. Cross-\
reference with at least one INDEPENDENT source (review site, listicle from \
a third party, GitHub stars, recent Reddit/HN discussion) before naming a \
favourite. If you can only find vendor self-promotion, say so explicitly \
rather than picking one anyway.
- When researching CODE / LIBRARIES / OPEN SOURCE TOOLS, prefer the \
dedicated tools over web_search: `github_search("<topic>")` returns \
real repos sorted by stars, `hackernews_search("<topic>")` returns \
community discussion. Pick favourites based on GitHub stars, recent \
commit activity, and HN/Reddit consensus — never on a vendor's own \
blog. Only fall back to web_search when the topic isn't code-related \
or the dedicated tools returned nothing useful.
- For RELATIONSHIP / CONNECTION questions about the operator's own \
work ("how is X connected to Y in my notes", "what bridges A and B", \
"what's around X"), prefer the graph tools over recall_vault: \
`graph_query` traverses outward, `graph_path` finds the shortest \
chain, `graph_explain` lists every direct neighbour. Use recall_vault \
for keyword search, the graph tools for structural relationships.
- For MULTI-STEP project work (operator says "build a thing" that \
clearly needs 2+ agents working on a shared workspace), prefer the \
plan tools over a single big dispatch: call `create_plan` first with \
the breakdown, then `dispatch_agent` for the first ready step \
(passing plan_step_id). On any subsequent turn that mentions an \
existing project_id, call `get_plan` first to see where you left off; \
use `advance_plan` to dispatch the next ready step. For single-step \
requests, skip create_plan entirely — just dispatch_agent.
- When the operator asks for a constraint (e.g. "use independent \
sources only", "GitHub stars not vendor sites"), TREAT IT AS A HARD RULE. \
If your tool calls violate the constraint, retry with corrected queries \
before producing a final answer or dispatch.
- For BUILD requests ("build me X", "make me Y", "let's build Z", \
"spin up a project to ..."), IMMEDIATELY call dispatch_agent with \
sensible defaults rather than asking clarifying questions. The \
operator wants results moving, not interrogation. The architect can \
flag missing auth / dependencies / decisions inside PLAN.md — that's \
a faster feedback loop than a chat round-trip. Default project_id \
to a lowercase-hyphenated slug derived from the request.
- When the operator's message is a SHORT REPLY ("yes", "no", "go", \
"continue", "do it"), use the prior conversation context (provided \
in the system message) to interpret it. Never default to a fresh \
greeting when prior context exists.\
"""


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
                "wait for completion — confirm dispatch and move on. Pass the "
                "same project_id to multiple dispatches when they should share "
                "files (e.g. an architect's PLAN.md read by backend-dev)."
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
                    "project_id": {
                        "type": "string",
                        "description": (
                            "Optional project handle (e.g. 'tiktok-poster'). Tasks "
                            "with the same project_id share a workspace so they can "
                            "pass files. Use lowercase-hyphenated. Omit for one-off tasks."
                        ),
                    },
                    "plan_step_id": {
                        "type": "string",
                        "description": (
                            "Optional. If this dispatch fulfils a step from a plan "
                            "created via create_plan, pass the step id (e.g. 's2') "
                            "so completion auto-updates the plan. Requires project_id."
                        ),
                    },
                },
                "required": ["agent", "prompt", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dispatch_browser_pilot",
            "description": (
                "Spawn an autonomous browser-pilot agent on a goal that "
                "needs real web browsing — gpt-4o vision drives a headless "
                "Chromium session via Playwright, navigates, clicks, screen-"
                "shots, extracts text, and (for YouTube goals) fetches a "
                "transcript and writes a structured briefing to "
                "Brain/Knowledge/. Use this for: 'watch X on YouTube and "
                "brief me', 'find a video about Y and summarise it', 'look "
                "up Z online and tell me what's there', 'browse to <url> "
                "and ...'. Does NOT need follow-up — the operator gets the "
                "briefing in their vault asynchronously. Cost-capped at "
                "~$0.50/run; typical cost is $0.05-0.15. Prefer this over "
                "dispatch_agent for any task that needs to actually look "
                "at a real web page."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": (
                            "Natural-language goal for the pilot. Be specific "
                            "about what page / video / topic, and what the "
                            "operator wants out of it. e.g. 'Watch a 5-10 "
                            "minute video about personal branding on YouTube "
                            "and write a structured briefing to my vault.'"
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": "Short label (~60 chars) for the task in the HUD.",
                    },
                },
                "required": ["goal", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dispatch_department",
            "description": (
                "Spawn a department head on a goal — the head will Task-"
                "dispatch its specialists (from ~/.claude/agents/) in "
                "parallel, collect their outputs, and synthesise a single "
                "deliverable. PREFER this over dispatch_agent when the "
                "operator's ask is domain-specific and benefits from "
                "specialist expertise:\n"
                "  - marketing: TikTok / Instagram / LinkedIn / Twitter / "
                "Reddit / SEO / podcast / growth / content / app-store. "
                "Bias toward this for any 'campaign', 'hooks', 'strategy', "
                "'audience', 'engagement', 'content series' ask.\n"
                "  - design: UI / UX / brand / visual / logo / mockups / "
                "wireframes / accessibility-of-visuals / image prompts.\n"
                "  - engineering: anything beyond a single-file dev task — "
                "system architecture, security review, multi-component "
                "wiring, devops, embedded, blockchain, mobile, AI "
                "engineering, technical writing.\n"
                "  - product: feature triage, sprint prioritisation, "
                "trend research, feedback synthesis, behavioural nudges.\n"
                "  - pm: sprint planning, roadmap, project status, retros.\n"
                "  - testing: a11y audits, perf benchmarks, evidence "
                "collection, API testing, tool evaluation. (For unit / "
                "integration tests on existing code, use dispatch_agent "
                "with agent='qa-engineer' instead.)\n"
                "  - support: analytics summaries, infra health, exec "
                "briefings, response templates.\n"
                "  - finance: bookkeeping, FP&A, investment research, "
                "tax (operator is in Jersey, UK).\n"
                "  - gamedev: anything game-related — particularly Unreal "
                "Engine for the operator's CursedTides project.\n"
                "  - ops: cross-departmental coordination — when the goal "
                "spans 2+ departments and needs a single coordinator. ops "
                "can also dispatch other heads.\n\n"
                "DON'T use this for: simple chat replies, one-off code "
                "edits (use dispatch_agent), web research / video briefs "
                "(use dispatch_browser_pilot). DON'T use it for vague "
                "asks where no specialist would obviously help."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "department": {
                        "type": "string",
                        "enum": [
                            "engineering", "design", "marketing", "product",
                            "pm", "testing", "support", "finance",
                            "gamedev", "ops",
                        ],
                        "description": "Which department to dispatch to.",
                    },
                    "goal": {
                        "type": "string",
                        "description": (
                            "Natural-language goal. Be specific about the "
                            "deliverable, target audience/platform/stack, "
                            "and any constraints. The head reads this and "
                            "routes to specialists, so include enough "
                            "context for them to pick correctly."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": "Short label (~60 chars) for the task in the HUD.",
                    },
                },
                "required": ["department", "goal", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_plan",
            "description": (
                "Persist a multi-step execution plan for a project so future turns "
                "and other agents can resume it. Use when the operator's request "
                "decomposes into 2+ agent dispatches sharing a project_id. After "
                "creating, immediately call dispatch_agent for the first ready step "
                "(passing plan_step_id). On subsequent turns, call get_plan first to "
                "see where you left off rather than re-deciding the breakdown. Don't "
                "use for single-shot requests.\n\n"
                "EACH STEP routes to either a specific agent OR a department. Set "
                "`department` (preferred for domain-specific work — marketing / "
                "design / engineering / product / pm / testing / support / finance / "
                "gamedev / ops) when you want the dept head to pick the specific "
                "specialist; the head will Task-dispatch to its team. Set `agent` "
                "when you need a specific agent_id (e.g. browser-pilot, architect, "
                "or one of the existing dev team like backend-dev). At least one of "
                "the two MUST be set per step. If both are set, `agent` wins."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string",
                        "description": "Lowercase-hyphenated project slug, e.g. 'scheduling-app'."},
                    "goal": {"type": "string",
                        "description": "One-sentence statement of what 'done' means."},
                    "steps": {
                        "type": "array",
                        "description": "2+ ordered steps. Each step is one dispatch (specific agent OR department head).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string",
                                    "description": "Short slug unique within plan, e.g. 's1'."},
                                "agent": {"type": "string",
                                    "description": (
                                        "Agent id from list_agents (e.g. 'browser-pilot', "
                                        "'architect', 'backend-dev'). EITHER `agent` OR "
                                        "`department` must be provided. Use this for "
                                        "specific agents that aren't department-routed."
                                    )},
                                "department": {
                                    "type": "string",
                                    "enum": [
                                        "engineering", "design", "marketing", "product",
                                        "pm", "testing", "support", "finance",
                                        "gamedev", "ops",
                                    ],
                                    "description": (
                                        "Route this step to a department head, who will "
                                        "Task-dispatch the right specialist. Prefer this "
                                        "over `agent` for domain-specific work where you "
                                        "don't need a specific agent_id."
                                    ),
                                },
                                "title": {"type": "string",
                                    "description": "Short label (~60 chars)."},
                                "prompt": {"type": "string",
                                    "description": "Self-contained instruction for the dispatched agent / head."},
                                "depends_on": {"type": "array", "items": {"type": "string"},
                                    "description": "Step ids this step waits for."},
                            },
                            "required": ["id", "title", "prompt"],
                        },
                    },
                },
                "required": ["project_id", "goal", "steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_plan",
            "description": (
                "Look up a saved plan and its current progress. Call when the "
                "operator asks 'where are we on project X', or at the start of any "
                "turn that mentions an existing project_id, before deciding whether "
                "to dispatch_agent or create_plan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "advance_plan",
            "description": (
                "Dispatch the next ready step of a saved plan. Convenience wrapper "
                "around get_plan + dispatch_agent — use when the operator says "
                "'continue project X' or 'do the next step'. No-op if nothing is "
                "ready or the plan is already complete."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the public web via DuckDuckGo. Returns a list of "
                "{title, url, snippet} hits. Use for current events, "
                "documentation lookups, library comparisons, or any factual "
                "question whose answer might post-date training. Follow up "
                "with fetch_url(url) to read promising results in full."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default 5, cap 10).",
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
            "name": "github_search",
            "description": (
                "Search GitHub repositories via the public REST API. "
                "MANDATORY for any 'what's new on github / trending repos / "
                "tools on github / popular libraries' style question — DO "
                "NOT answer from training data, the data goes stale daily. "
                "Also renders a clickable results card in the chat panel "
                "so the operator can SEE the repos, not just hear about "
                "them. Returns up to 10 repos sorted by stars with name, "
                "URL, description, star count, language, last-push date. "
                "Example queries: 'AI agents', 'tiktok automation', "
                "'language:python topic:llm', 'voice assistant'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "GitHub search query (supports its query syntax).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 5, cap 10).",
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
            "name": "hackernews_search",
            "description": (
                "Search Hacker News via the Algolia API for stories and "
                "comments matching a query. MANDATORY for any 'what's on HN "
                "/ hacker news today / HN front page' style question — DO "
                "NOT answer from training data. Renders a clickable results "
                "card in the chat panel. Returns up to 10 hits with title, "
                "URL, points, comment count, and date. Use to find "
                "community discussion about products, libraries, techniques "
                "— a strong independent signal vs vendor pages."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                    "limit": {
                        "type": "integer",
                        "description": "Max hits (default 5, cap 10).",
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
            "name": "graph_query",
            "description": (
                "Traverse the operator's knowledge graph (built by graphify "
                "from the Obsidian vault) starting from nodes that match the "
                "question. Use for relationship questions like 'how is X "
                "connected to Y', 'what's around the JARVIS architecture', "
                "'what bridges OpenJarvis and TikTok work'. Returns a "
                "ranked subgraph with nodes + edges. Complements "
                "recall_vault: that one finds notes containing keywords; "
                "this one traces structural connections between concepts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Natural-language question. Key terms get matched to node labels.",
                    },
                    "mode": {
                        "type": "string",
                        "description": "bfs = broad neighbour exploration; dfs = follow one chain deep.",
                        "enum": ["bfs", "dfs"],
                        "default": "bfs",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Max traversal depth (default 3).",
                        "default": 3,
                    },
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "graph_path",
            "description": (
                "Shortest path between two named concepts in the knowledge "
                "graph. Returns the chain of nodes + edges connecting them, "
                "or 'no path' if they're in disconnected components. Use "
                "for 'how does X reach Y', 'what bridges A and B', 'is "
                "there any connection between X and Y'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "string", "description": "Start concept name (fuzzy match against node labels)."},
                    "b": {"type": "string", "description": "End concept name (fuzzy match against node labels)."},
                },
                "required": ["a", "b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "refresh_graph",
            "description": (
                "Trigger a background rebuild of the graphify knowledge "
                "graph from the live vault. Non-blocking: returns "
                "immediately with start status. The next graph_query / "
                "graph_path / graph_explain call after the rebuild "
                "finishes will auto-pick-up the new graph. Use when the "
                "operator asks to refresh / rebuild the graph, or when "
                "graph_query returns nothing relevant for what should be "
                "in the vault."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "graph_explain",
            "description": (
                "Dump everything connected to a single node in the knowledge "
                "graph: degree + all neighbours with relation type and "
                "source file. Use for 'what is X', 'tell me about X and "
                "what it touches', 'what depends on X'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Concept name to look up (fuzzy match against node labels)."},
                },
                "required": ["node"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "Download a URL and return the readable text content with "
                "HTML stripped. Capped at ~8000 characters. Use after "
                "web_search to read a promising hit, or directly when given "
                "a URL by the operator."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Absolute http(s) URL to fetch."},
                    "max_chars": {
                        "type": "integer",
                        "description": "Max characters of body text to return (default 8000, cap 20000).",
                        "default": 8000,
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "maps_locate",
            "description": (
                "Geocode a place / address / landmark and render a map "
                "card in the chat panel. USE THIS for any 'where is X', "
                "'show me X on a map', 'find the location of X', "
                "'find X near me' style request. Returns a one-line "
                "text summary you can speak; the visual map is "
                "rendered as a side-channel widget below the chat "
                "bubble. Powered by OpenStreetMap Nominatim, no API "
                "key. Examples: 'Buckingham Palace', "
                "'123 Main St, Boston', 'Starbucks Times Square', "
                "'Eiffel Tower'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Place name, address, or landmark to locate.",
                    },
                    "zoom": {
                        "type": "integer",
                        "description": "Map zoom level 2-19 (default 13). 13-15 = neighbourhood, 16-18 = building.",
                        "default": 13,
                    },
                },
                "required": ["query"],
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


def _tool_dispatch_agent(agent: str, prompt: str, title: str,
                         project_id: Optional[str] = None,
                         plan_step_id: Optional[str] = None) -> str:
    from openjarvis.tools import agent_runner
    valid_ids = {a["id"] for a in agent_runner.list_agents()}
    if agent not in valid_ids:
        return json.dumps({
            "ok": False,
            "error": f"unknown agent '{agent}'",
            "valid": sorted(valid_ids),
        })
    pid = (project_id or "").strip().lower() or None
    if pid:
        # Defensive — keep project ids filesystem-safe.
        # Audit (M2-RCE) found ".." survives this regex (only "-_" are
        # stripped at the ends); a literal ".." pid would resolve to
        # PROJECTS_DIR/.. = the agents-root parent, letting the spawned
        # agent run in / write to a non-project location. Final guard
        # rejects pure-dot ids and overlong values.
        pid = re.sub(r"[^a-z0-9._-]+", "-", pid).strip("-") or None
        if pid in ("", ".", "..") or set(pid) == {"."}:
            pid = None
        elif len(pid) > 60:
            pid = pid[:60].strip("-") or None

    # Plan-step validation (autonomy #2c): if plan_step_id is given but
    # we have no project_id (or the step doesn't exist), warn and
    # proceed without the link. Don't reject the dispatch — the
    # operator asked for work, work happens; only the plan-tracking
    # is opt-out on bad input.
    step_id = (plan_step_id or "").strip() or None
    plan_warning: Optional[str] = None
    if step_id:
        if not pid:
            plan_warning = "plan_step_id requires project_id; ignored"
            step_id = None
        else:
            try:
                from openjarvis.tools import agent_plan
                plan = agent_plan.get_plan(pid)
                if plan is None:
                    plan_warning = f"no plan exists for project {pid!r}; step ignored"
                    step_id = None
                elif not any(s.get("id") == step_id for s in plan.get("steps") or []):
                    valid_steps = [s.get("id") for s in plan.get("steps") or []]
                    plan_warning = (f"step {step_id!r} not in plan; valid: {valid_steps}; "
                                    "step ignored")
                    step_id = None
            except Exception:
                logger.exception("agent_plan.get_plan failed in dispatch_agent")
                step_id = None

    task_id = agent_runner.add_task(title=title[:80], agent_id=agent,
                                    prompt=prompt, project_id=pid,
                                    plan_step_id=step_id)
    # Vault: ensure every project has a home note. Future sessions can
    # then recall "what's in the X project" naturally.
    if pid:
        _ensure_project_note(pid, title=title, task_id=task_id,
                             agent=agent, prompt=prompt)
    out: Dict[str, Any] = {
        "ok": True,
        "task_id": task_id,
        "agent": agent,
        "project_id": pid,
    }
    if step_id:
        out["plan_step_id"] = step_id
    if plan_warning:
        out["plan_warning"] = plan_warning
    return json.dumps(out)


# Friendly display names for each department slug. The slug -> head_id
# mapping itself lives in agent_runner.DEPT_TO_HEAD (shared with
# agent_plan); this table is purely for friendly task titles + spoken
# acknowledgements.
_DEPARTMENT_FRIENDLY = {
    "engineering": "engineering",
    "design":      "design",
    "marketing":   "marketing",
    "product":     "product",
    "pm":          "project management",
    "testing":     "testing & QA",
    "support":     "support",
    "finance":     "finance",
    "gamedev":     "game dev",
    "ops":         "ops",
}


def _resolve_department(dep: str) -> Optional[Tuple[str, str]]:
    """Return (head_agent_id, friendly_name) or None for unknown dept."""
    try:
        from openjarvis.tools.agent_runner import DEPT_TO_HEAD
    except Exception:
        return None
    head = DEPT_TO_HEAD.get(dep)
    if not head:
        return None
    return head, _DEPARTMENT_FRIENDLY.get(dep, dep)


# Back-compat shim for any callers that imported the old internal name.
# Will be removed in a future cleanup pass.
_DEPARTMENT_TO_HEAD = {
    k: (v, _DEPARTMENT_FRIENDLY.get(k, k))
    for k, v in (
        __import__("openjarvis.tools.agent_runner", fromlist=["DEPT_TO_HEAD"])
        .DEPT_TO_HEAD.items()
    )
}


def _tool_dispatch_department(department: str, goal: str, title: str) -> str:
    """Spawn a department head on a goal. The head Task-dispatches its
    specialist roster (installed at ~/.claude/agents/ from the
    msitarzewski/agency-agents library), collects outputs, and synthesises
    a single deliverable. Mirrors voice fast-path _try_department but
    callable from the conversational LLM brain mid-conversation."""
    from openjarvis.tools import agent_runner
    dep = (department or "").strip().lower()
    g = (goal or "").strip()
    resolved = _resolve_department(dep)
    if not resolved:
        return json.dumps({
            "ok": False,
            "error": f"unknown department {dep!r}",
            "valid_departments": list(agent_runner.DEPT_TO_HEAD.keys()),
        })
    if len(g) < 6:
        return json.dumps({"ok": False, "error": "goal too short — be specific"})
    head_id, friendly = resolved
    t = (title or g)[:80]
    prompt = (
        f"DEPARTMENT BRIEF — {friendly.upper()}\n"
        f"========================================\n"
        f"The operator's request: {g}\n\n"
        f"YOU ARE the {friendly} department head. Your role and the "
        f"specialists you command are described in your system prompt. "
        f"This is a single-shot non-interactive run — there is no second "
        f"turn, no clarifying questions accepted. Do all of this NOW in "
        f"the current working directory.\n\n"
        f"METHOD:\n"
        f"1. Identify which 1-N specialists in your department best fit "
        f"the request.\n"
        f"2. Use the Task tool to dispatch them. Spawn in PARALLEL when "
        f"possible (multiple Task calls in one turn).\n"
        f"3. Collect their outputs.\n"
        f"4. Synthesise into a single integrated deliverable. Write it "
        f"as DELIVERABLE.md (or a more specific name) in the workspace.\n"
        f"5. Briefly summarise in stdout what you built and which "
        f"specialists contributed.\n\n"
        f"ANTI-PATTERNS: doing specialist work yourself, sequential "
        f"dispatch when parallel works, lifting verbatim outputs without "
        f"integrating, skipping the synthesis step.\n\n"
        f"START NOW. Make sensible defaults. Be concise, no preamble."
    )
    try:
        task_id = agent_runner.add_task(
            title=f"{friendly}: {t}",
            agent_id=head_id,
            prompt=prompt,
            priority=20,    # operator-spoken intent priority
        )
    except Exception as exc:
        logger.exception("dispatch_department failed")
        return json.dumps({"ok": False, "error": f"dispatch failed: {exc}"})
    return json.dumps({
        "ok": True,
        "task_id": task_id,
        "department": dep,
        "head": head_id,
        "note": (
            f"{friendly.title()} department head is now coordinating "
            f"specialists. Operator does not need to wait — the deliverable "
            f"will land in the task workspace, and the head will summarise "
            f"on completion."
        ),
    })


def _tool_dispatch_browser_pilot(goal: str, title: str) -> str:
    """Spawn the browser-pilot agent on a goal. Thin wrapper around
    add_task — exists as a dedicated tool (not just dispatch_agent with
    agent='browser-pilot') so the conversational LLM has a clearer
    signal that THIS is what to reach for on browse-y intents."""
    from openjarvis.tools import agent_runner
    g = (goal or "").strip()
    if len(g) < 6:
        return json.dumps({"ok": False, "error": "goal too short"})
    t = (title or g)[:80]
    try:
        task_id = agent_runner.add_task(
            title=t,
            agent_id="browser-pilot",
            prompt=g,
            priority=20,   # operator-spoken intent priority
        )
    except Exception as exc:
        logger.exception("dispatch_browser_pilot failed")
        return json.dumps({"ok": False, "error": f"dispatch failed: {exc}"})
    return json.dumps({
        "ok": True,
        "task_id": task_id,
        "agent": "browser-pilot",
        "note": (
            "Browser pilot is now running in the background. Briefing will "
            "land in Brain/Knowledge/ when complete. Operator does not need "
            "to wait."
        ),
    })


def _tool_create_plan(project_id: str, goal: str,
                      steps: List[Dict[str, Any]]) -> str:
    """Create + persist a multi-step plan."""
    try:
        from openjarvis.tools import agent_plan
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"agent_plan unavailable: {exc}"})
    try:
        plan_path = agent_plan.create_plan(
            project_id=project_id, goal=goal, steps=steps,
            created_by="tool_use.create_plan",
        )
    except agent_plan.PlanValidationError as exc:
        return json.dumps({"ok": False, "error": str(exc)})
    except Exception as exc:
        logger.exception("create_plan failed")
        return json.dumps({"ok": False, "error": str(exc)})

    nxt = agent_plan.next_pending_step(project_id)
    return json.dumps({
        "ok": True,
        "project_id": project_id,
        "plan_path": str(plan_path),
        "step_count": len(steps),
        "next_step": (
            {"id": nxt["id"], "agent": nxt["agent"], "title": nxt["title"],
             "prompt": nxt["prompt"]}
            if nxt else None
        ),
        "hint": (
            "Now call dispatch_agent for next_step.id, passing "
            "plan_step_id=next_step.id, project_id=project_id, and the "
            "step's prompt as the dispatch prompt."
            if nxt else "plan has no runnable steps"
        ),
    })


def _tool_get_plan(project_id: str) -> str:
    """Read the current plan + summary."""
    try:
        from openjarvis.tools import agent_plan
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"agent_plan unavailable: {exc}"})
    plan = agent_plan.get_plan(project_id)
    if plan is None:
        return json.dumps({
            "ok": False,
            "reason": f"no plan for project {project_id!r}",
        })
    nxt = agent_plan.next_pending_step(project_id)
    summary = agent_plan.plan_summary(project_id)
    return json.dumps({
        "ok": True,
        "project_id": project_id,
        "summary": summary,
        "status": plan.get("status"),
        "goal": plan.get("goal"),
        "steps": [
            {"id": s.get("id"), "agent": s.get("agent"),
             "title": s.get("title"), "status": s.get("status"),
             "task_id": s.get("task_id"),
             "depends_on": s.get("depends_on") or []}
            for s in plan.get("steps") or []
        ],
        "next_step": (
            {"id": nxt["id"], "agent": nxt["agent"],
             "title": nxt["title"], "prompt": nxt["prompt"]}
            if nxt else None
        ),
    })


def _tool_advance_plan(project_id: str) -> str:
    """Dispatch the next ready step of a saved plan."""
    try:
        from openjarvis.tools import agent_plan, agent_runner
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"agent_plan unavailable: {exc}"})
    plan = agent_plan.get_plan(project_id)
    if plan is None:
        return json.dumps({"ok": False, "dispatched": False,
                           "reason": f"no plan for project {project_id!r}"})
    nxt = agent_plan.next_pending_step(project_id)
    if nxt is None:
        return json.dumps({"ok": True, "dispatched": False,
                           "reason": "no runnable steps (plan complete or blocked)"})
    try:
        task_id = agent_runner.add_task(
            title=nxt.get("title", "")[:80],
            agent_id=nxt["agent"],
            prompt=nxt.get("prompt", ""),
            project_id=project_id,
            plan_step_id=nxt["id"],
        )
    except Exception as exc:
        logger.exception("advance_plan dispatch failed")
        return json.dumps({"ok": False, "dispatched": False, "error": str(exc)})
    # Ensure the project's vault note exists for visibility
    try:
        _ensure_project_note(project_id, title=nxt.get("title", ""),
                             task_id=task_id, agent=nxt["agent"],
                             prompt=nxt.get("prompt", ""))
    except Exception:
        pass
    return json.dumps({
        "ok": True,
        "dispatched": True,
        "task_id": task_id,
        "step_id": nxt["id"],
        "agent": nxt["agent"],
    })


def _ensure_project_note(project_id: str, *, title: str, task_id: str,
                         agent: str, prompt: str) -> None:
    """Create Brain/Projects/<project_id>.md on first dispatch, append a
    task entry on every subsequent one. Best-effort — vault failures must
    not break dispatch."""
    try:
        from openjarvis.tools import obsidian_brain
        obsidian_brain._ensure_layout()
        from datetime import datetime
        proj_dir = obsidian_brain.BRAIN_ROOT / "Projects"
        proj_dir.mkdir(parents=True, exist_ok=True)
        path = proj_dir / f"{project_id}.md"
        stamp = datetime.now().isoformat(timespec="seconds")
        if not path.exists():
            body = (
                f"---\n"
                f"type: project\n"
                f"name: {project_id}\n"
                f"created: {stamp}\n"
                f"tags: [project, agent-team]\n"
                f"---\n\n"
                f"# {project_id}\n\n"
                f"Project workspace at `~/.openjarvis/agents/projects/{project_id}/`.\n\n"
                f"## Tasks dispatched\n\n"
            )
            path.write_text(body, encoding="utf-8")
            obsidian_brain._emit_event("write", f"project: {project_id}",
                                       kind="project")
        # Append the task entry. Bounded — keep the appendix tail readable.
        entry = (f"- {stamp} — **{agent}** · `{task_id}` — "
                 f"{title[:120].strip()}\n")
        with path.open("a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        logger.exception("vault: project note write failed for %s", project_id)


# ---------------------------------------------------------------------------
# Web tools — DuckDuckGo HTML + plain GET, no API keys
# ---------------------------------------------------------------------------

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_RESULT_RE = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
    re.DOTALL,
)


def _strip_html(text: str) -> str:
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


_DDG_BLOCKED_UNTIL: float = 0.0


def _tool_web_search(query: str, limit: int = 5) -> str:
    global _DDG_BLOCKED_UNTIL
    try:
        import httpx
    except Exception as exc:
        return json.dumps({"error": f"httpx unavailable: {exc}"})
    limit = max(1, min(int(limit or 5), 10))
    if time.time() < _DDG_BLOCKED_UNTIL:
        return json.dumps({
            "hits": [],
            "error": (
                "web_search is in cooldown (DuckDuckGo rate-limited us within "
                "the last 10 minutes). Use github_search / hackernews_search "
                "instead. Do not retry web_search this turn."
            ),
        })
    try:
        with httpx.Client(timeout=12.0, follow_redirects=True,
                          headers={"User-Agent": _USER_AGENT}) as client:
            resp = client.post(_DDG_HTML_URL, data={"q": query})
            resp.raise_for_status()
            body = resp.text
    except Exception as exc:
        return json.dumps({"error": f"search failed: {exc}"})
    hits = []
    for m in _RESULT_RE.finditer(body):
        url = html.unescape(m.group(1))
        # DDG wraps real URLs in /l/?uddg=...
        if "uddg=" in url:
            from urllib.parse import parse_qs, urlparse, unquote
            qs = parse_qs(urlparse(url).query)
            url = unquote(qs.get("uddg", [url])[0])
        title = _strip_html(m.group(2))
        snippet = _strip_html(m.group(3))
        if not url or not title:
            continue
        hits.append({"title": title, "url": url, "snippet": snippet[:280]})
        if len(hits) >= limit:
            break
    if not hits:
        # DDG returns 202 + their home page when soft-blocked. Track that
        # and fast-fail subsequent calls in the same process so the model
        # doesn't burn iterations retrying. (`global` declared at top.)
        _DDG_BLOCKED_UNTIL = time.time() + 600  # 10 min cooldown
        return json.dumps({
            "hits": [],
            "error": (
                "web_search is unavailable right now (DuckDuckGo rate-limit). "
                "Use github_search / hackernews_search instead, or proceed "
                "with what you already have. Do not retry web_search this turn."
            ),
        })
    _save_web_search_to_vault(query, hits)
    try:
        from openjarvis.cli.brain_server import emit_widget
        emit_widget("results", {
            "title": f"Web: {query}",
            "source": "web",
            "hits": [{
                "title": h["title"],
                "url": h["url"],
                "snippet": h.get("snippet") or "",
                "meta": "",
            } for h in hits],
        })
    except Exception:
        logger.debug("web_search emit_widget failed", exc_info=True)
    return json.dumps({"hits": hits})


def _is_internal_ip(ip_str: str) -> bool:
    """SSRF defence (audit 2026-04-26 H3): block private / loopback /
    link-local / metadata IP ranges so a prompt-injected fetch_url can't
    pivot to internal services (e.g. 127.0.0.1:7710 Jarvis brain,
    169.254.169.254 cloud metadata, RFC1918 LAN devices)."""
    try:
        import ipaddress
        ip = ipaddress.ip_address(ip_str)
    except (ValueError, TypeError):
        return False
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def _tool_fetch_url(url: str, max_chars: int = 8000) -> str:
    try:
        import httpx
    except Exception as exc:
        return json.dumps({"error": f"httpx unavailable: {exc}"})
    if not (url.startswith("http://") or url.startswith("https://")):
        return json.dumps({"error": "url must start with http:// or https://"})

    # SSRF guard: resolve hostname and block internal IPs before connecting.
    # Re-checked after each redirect via the event_hooks below.
    try:
        from urllib.parse import urlparse
        import socket
        host = urlparse(url).hostname or ""
        if not host:
            return json.dumps({"error": "no hostname in url"})
        try:
            resolved = socket.gethostbyname(host)
        except OSError as exc:
            return json.dumps({"error": f"dns resolution failed: {exc}"})
        if _is_internal_ip(resolved):
            return json.dumps({
                "error": "fetch_url refused: target resolves to an internal IP "
                         "(loopback / RFC1918 / link-local / metadata range). "
                         "External URLs only."
            })
    except Exception as exc:
        return json.dumps({"error": f"url validation failed: {exc}"})

    def _redirect_guard(response):
        """Re-validate redirected URLs so a public hostname can't 302 to
        an internal one mid-request."""
        loc = response.headers.get("location")
        if not loc:
            return
        try:
            from urllib.parse import urlparse, urljoin
            new = urljoin(str(response.url), loc)
            new_host = urlparse(new).hostname or ""
            if not new_host:
                raise ValueError("redirect with no hostname")
            new_resolved = socket.gethostbyname(new_host)
            if _is_internal_ip(new_resolved):
                raise httpx.RequestError(
                    f"redirect target {new_host} resolves to internal IP")
        except httpx.RequestError:
            raise
        except Exception:
            # Silent: let httpx handle weird redirects normally
            pass

    cap = max(500, min(int(max_chars or 8000), 20000))
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True,
                          headers={"User-Agent": _USER_AGENT},
                          event_hooks={"response": [_redirect_guard]}) as client:
            resp = client.get(url)
            resp.raise_for_status()
            ctype = (resp.headers.get("content-type") or "").lower()
            if "html" in ctype or "xml" in ctype:
                text = _strip_html(resp.text)
            elif "text" in ctype or "json" in ctype:
                text = resp.text
            else:
                return json.dumps({"error": f"unsupported content-type: {ctype}"})
    except Exception as exc:
        return json.dumps({"error": f"fetch failed: {exc}"})
    truncated = len(text) > cap
    _save_fetched_page_to_vault(url, text)
    return json.dumps({
        "url": url,
        "chars": len(text),
        "truncated": truncated,
        "text": text[:cap],
    })


def _save_web_search_to_vault(query: str, hits: List[dict]) -> None:
    """Persist search results as a Knowledge/Web note + a daily-journal
    line. Future sessions can then recall("trending AI tools") and find
    the cached result instead of needing to re-search."""
    try:
        from openjarvis.tools import obsidian_brain
        from datetime import datetime
        obsidian_brain._ensure_layout()
        web_dir = obsidian_brain.BRAIN_ROOT / "Knowledge" / "Web"
        web_dir.mkdir(parents=True, exist_ok=True)
        date = datetime.now().strftime("%Y-%m-%d")
        slug = obsidian_brain._slugify(f"search - {query}")
        path = web_dir / f"{date} - {slug}.md"
        # Append-or-create — the same query searched multiple times in one
        # day collects all variations in one note.
        stamp = datetime.now().strftime("%H:%M:%S")
        if not path.exists():
            head = (
                f"---\ntype: knowledge\nsource: web_search\nquery: {query}\n"
                f"created: {datetime.now().isoformat(timespec='seconds')}\n"
                f"tags: [web, search]\n---\n\n# Web search: {query}\n\n"
            )
            path.write_text(head, encoding="utf-8")
        body_lines = [f"## {stamp}\n"]
        for h in hits:
            title = (h.get("title") or "").replace("\n", " ").strip()
            url = (h.get("url") or "").strip()
            snippet = (h.get("snippet") or "").replace("\n", " ").strip()
            body_lines.append(f"- **[{title}]({url})** — {snippet}")
        body_lines.append("")
        with path.open("a", encoding="utf-8") as f:
            f.write("\n".join(body_lines) + "\n")
        obsidian_brain.daily_append(
            f"web_search: '{query[:80]}' → {len(hits)} hits → [[{path.stem}]]"
        )
        obsidian_brain._emit_event("write", f"web search: {query[:60]}",
                                   kind="knowledge")
    except Exception:
        logger.exception("vault: web_search note write failed")


def _save_fetched_page_to_vault(url: str, text: str) -> None:
    """Persist a fetched page's text as a Knowledge/Web note. Cap at 16KB
    in the vault — these add up. Daily journal gets a one-liner pointer."""
    try:
        from openjarvis.tools import obsidian_brain
        from datetime import datetime
        from urllib.parse import urlparse
        if not text or len(text) < 80:
            return  # not worth a note
        obsidian_brain._ensure_layout()
        web_dir = obsidian_brain.BRAIN_ROOT / "Knowledge" / "Web"
        web_dir.mkdir(parents=True, exist_ok=True)
        host = urlparse(url).hostname or "page"
        date = datetime.now().strftime("%Y-%m-%d")
        # Slug from path tail or full url if path is empty
        path_part = urlparse(url).path.strip("/").replace("/", "-") or host
        slug = obsidian_brain._slugify(f"fetch - {host} - {path_part}")[:80]
        path = web_dir / f"{date} - {slug}.md"
        if path.exists():
            return  # already cached today
        snippet = text[:16000]
        body = (
            f"---\ntype: knowledge\nsource: fetch_url\nurl: {url}\n"
            f"created: {datetime.now().isoformat(timespec='seconds')}\n"
            f"tags: [web, fetch]\n---\n\n# {host}\n\n"
            f"<{url}>\n\n{snippet}\n"
        )
        path.write_text(body, encoding="utf-8")
        obsidian_brain.daily_append(f"fetch_url: {url} → [[{path.stem}]]")
        obsidian_brain._emit_event("write", f"fetched: {host}",
                                   kind="knowledge")
    except Exception:
        logger.exception("vault: fetch_url note write failed")


def _tool_github_search(query: str, limit: int = 5) -> str:
    try:
        import httpx
    except Exception as exc:
        return json.dumps({"error": f"httpx unavailable: {exc}"})
    limit = max(1, min(int(limit or 5), 10))
    try:
        with httpx.Client(timeout=12.0, follow_redirects=True,
                          headers={"User-Agent": _USER_AGENT,
                                   "Accept": "application/vnd.github+json",
                                   "X-GitHub-Api-Version": "2022-11-28"}) as client:
            resp = client.get(
                "https://api.github.com/search/repositories",
                params={"q": query, "sort": "stars", "order": "desc",
                        "per_page": str(limit)},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return json.dumps({"error": f"github search failed: {exc}"})
    repos = []
    for item in (data.get("items") or [])[:limit]:
        repos.append({
            "name": item.get("full_name"),
            "url": item.get("html_url"),
            "description": (item.get("description") or "")[:200],
            "stars": item.get("stargazers_count", 0),
            "language": item.get("language"),
            "pushed_at": item.get("pushed_at"),
            "topics": (item.get("topics") or [])[:6],
        })
    if not repos:
        return json.dumps({"repos": [], "note": "no matching repos"})
    _save_github_search_to_vault(query, repos)
    # Side-channel: emit a clickable results card so the operator
    # SEES what Jarvis is talking about, not just hears it.
    try:
        from openjarvis.cli.brain_server import emit_widget
        emit_widget("results", {
            "title": f"GitHub: {query}",
            "source": "github",
            "hits": [{
                "title": r["name"],
                "url": r["url"],
                "snippet": r.get("description") or "",
                "meta": (f"⭐ {r['stars']:,}"
                         + (f" · {r['language']}" if r.get("language") else "")
                         + (f" · pushed {r['pushed_at'][:10]}" if r.get("pushed_at") else "")),
            } for r in repos],
        })
    except Exception:
        logger.debug("github_search emit_widget failed", exc_info=True)
    return json.dumps({"repos": repos})


def _tool_hackernews_search(query: str, limit: int = 5) -> str:
    try:
        import httpx
    except Exception as exc:
        return json.dumps({"error": f"httpx unavailable: {exc}"})
    limit = max(1, min(int(limit or 5), 10))
    try:
        with httpx.Client(timeout=12.0, follow_redirects=True,
                          headers={"User-Agent": _USER_AGENT}) as client:
            resp = client.get(
                "https://hn.algolia.com/api/v1/search",
                params={"query": query, "hitsPerPage": str(limit)},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return json.dumps({"error": f"hn search failed: {exc}"})
    hits = []
    for h in (data.get("hits") or [])[:limit]:
        title = h.get("title") or h.get("story_title") or ""
        url = h.get("url") or h.get("story_url") or (
            f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        )
        hits.append({
            "title": title[:200],
            "url": url,
            "points": h.get("points") or 0,
            "comments": h.get("num_comments") or 0,
            "created_at": (h.get("created_at") or "")[:10],
        })
    if not hits:
        return json.dumps({"hits": [], "note": "no HN results"})
    _save_hn_search_to_vault(query, hits)
    try:
        from openjarvis.cli.brain_server import emit_widget
        emit_widget("results", {
            "title": f"Hacker News: {query}",
            "source": "hackernews",
            "hits": [{
                "title": h["title"],
                "url": h["url"],
                "snippet": "",
                "meta": (f"▲ {h['points']} · {h['comments']} comments"
                         + (f" · {h['created_at']}" if h.get("created_at") else "")),
            } for h in hits],
        })
    except Exception:
        logger.debug("hackernews_search emit_widget failed", exc_info=True)
    return json.dumps({"hits": hits})


def _save_github_search_to_vault(query: str, repos: List[dict]) -> None:
    try:
        from openjarvis.tools import obsidian_brain
        from datetime import datetime
        obsidian_brain._ensure_layout()
        web_dir = obsidian_brain.BRAIN_ROOT / "Knowledge" / "Web"
        web_dir.mkdir(parents=True, exist_ok=True)
        date = datetime.now().strftime("%Y-%m-%d")
        slug = obsidian_brain._slugify(f"github - {query}")
        path = web_dir / f"{date} - {slug}.md"
        stamp = datetime.now().strftime("%H:%M:%S")
        if not path.exists():
            head = (
                f"---\ntype: knowledge\nsource: github_search\nquery: {query}\n"
                f"created: {datetime.now().isoformat(timespec='seconds')}\n"
                f"tags: [web, github, code]\n---\n\n# GitHub: {query}\n\n"
            )
            path.write_text(head, encoding="utf-8")
        lines = [f"## {stamp}\n"]
        for r in repos:
            lines.append(
                f"- **[{r['name']}]({r['url']})** — ⭐ {r['stars']:,} · "
                f"{r.get('language') or '?'} · pushed {r.get('pushed_at','')[:10]}\n"
                f"  {r.get('description') or '(no description)'}"
            )
        with path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")
        obsidian_brain.daily_append(
            f"github_search: '{query[:80]}' -> {len(repos)} repos -> [[{path.stem}]]"
        )
        obsidian_brain._emit_event("write", f"github: {query[:60]}",
                                   kind="knowledge")
    except Exception:
        logger.exception("vault: github search note write failed")


def _save_hn_search_to_vault(query: str, hits: List[dict]) -> None:
    try:
        from openjarvis.tools import obsidian_brain
        from datetime import datetime
        obsidian_brain._ensure_layout()
        web_dir = obsidian_brain.BRAIN_ROOT / "Knowledge" / "Web"
        web_dir.mkdir(parents=True, exist_ok=True)
        date = datetime.now().strftime("%Y-%m-%d")
        slug = obsidian_brain._slugify(f"hn - {query}")
        path = web_dir / f"{date} - {slug}.md"
        stamp = datetime.now().strftime("%H:%M:%S")
        if not path.exists():
            head = (
                f"---\ntype: knowledge\nsource: hackernews_search\nquery: {query}\n"
                f"created: {datetime.now().isoformat(timespec='seconds')}\n"
                f"tags: [web, hackernews, community]\n---\n\n"
                f"# Hacker News: {query}\n\n"
            )
            path.write_text(head, encoding="utf-8")
        lines = [f"## {stamp}\n"]
        for h in hits:
            lines.append(
                f"- **[{h['title']}]({h['url']})** — ▲ {h['points']} · "
                f"{h['comments']} comments · {h['created_at']}"
            )
        with path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")
        obsidian_brain.daily_append(
            f"hn_search: '{query[:80]}' -> {len(hits)} hits -> [[{path.stem}]]"
        )
        obsidian_brain._emit_event("write", f"hn: {query[:60]}",
                                   kind="knowledge")
    except Exception:
        logger.exception("vault: hn search note write failed")


def _tool_maps_locate(query: str, zoom: int = 13) -> str:
    """Geocode a place name via OpenStreetMap Nominatim and emit a map
    widget to the HUD chat panel. Returns a one-line text summary the
    LLM can speak/render. No API key required.

    Nominatim policy: max 1 req/sec, must include a real User-Agent.
    We satisfy both — this tool only fires on direct operator request,
    not in tight loops, and we send a J.A.R.V.I.S. UA string."""
    q = (query or "").strip()
    if not q:
        return "maps_locate: empty query"
    try:
        import urllib.parse
        import urllib.request
        url = ("https://nominatim.openstreetmap.org/search?"
               + urllib.parse.urlencode({
                   "q": q, "format": "json", "limit": 1, "addressdetails": 1,
               }))
        req = urllib.request.Request(url, headers={
            "User-Agent": "OpenJarvis/1.0 (jarvis personal assistant)",
            "Accept-Language": "en",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return f"maps_locate failed: {exc}"
    if not data:
        return f"No place found for {q!r}."
    hit = data[0]
    try:
        lat = float(hit["lat"]); lon = float(hit["lon"])
    except (KeyError, ValueError, TypeError):
        return f"maps_locate: bad coords for {q!r}."
    label = hit.get("display_name") or q
    try:
        zoom_i = max(2, min(int(zoom or 13), 19))
    except Exception:
        zoom_i = 13
    # Side-channel: emit the map widget. Tool's text return is what
    # the LLM sees + speaks; the widget is purely visual.
    try:
        from openjarvis.cli.brain_server import emit_widget
        emit_widget("map", {
            "lat": lat, "lon": lon, "zoom": zoom_i,
            "label": label, "query": q,
        })
    except Exception:
        logger.debug("maps_locate emit_widget failed", exc_info=True)
    # Trim the display_name for the spoken/text reply — Nominatim
    # tends to return very long comma-separated strings.
    short_label = ", ".join(label.split(", ")[:3]) if "," in label else label
    return (f"Found {short_label} at {lat:.4f}, {lon:.4f}. "
            f"Map's on screen, sir.")


def _tool_graph_query(question: str, mode: str = "bfs", depth: int = 3) -> str:
    from openjarvis.cli import graphify_bridge
    try:
        depth_i = max(1, min(int(depth or 3), 5))
    except Exception:
        depth_i = 3
    mode_s = "dfs" if str(mode).lower() == "dfs" else "bfs"
    return json.dumps(graphify_bridge.query(question or "", mode=mode_s, depth=depth_i))


def _tool_graph_path(a: str, b: str) -> str:
    from openjarvis.cli import graphify_bridge
    return json.dumps(graphify_bridge.path(a or "", b or ""))


def _tool_graph_explain(node: str) -> str:
    from openjarvis.cli import graphify_bridge
    return json.dumps(graphify_bridge.explain(node or ""))


def _tool_graph_refresh() -> str:
    from openjarvis.cli import graphify_bridge
    stale = graphify_bridge.staleness()
    res = graphify_bridge.refresh(blocking=False)
    return json.dumps({"refresh": res, "previous_state": stale})


_TOOL_DISPATCH = {
    "recall_vault": _tool_recall_vault,
    "remember_fact": _tool_remember_fact,
    "list_agents": _tool_list_agents,
    "dispatch_agent": _tool_dispatch_agent,
    "dispatch_browser_pilot": _tool_dispatch_browser_pilot,
    "dispatch_department": _tool_dispatch_department,
    "web_search": _tool_web_search,
    "github_search": _tool_github_search,
    "hackernews_search": _tool_hackernews_search,
    "fetch_url": _tool_fetch_url,
    "graph_query": _tool_graph_query,
    "graph_path": _tool_graph_path,
    "graph_explain": _tool_graph_explain,
    "refresh_graph": _tool_graph_refresh,
    "create_plan": _tool_create_plan,
    "get_plan": _tool_get_plan,
    "advance_plan": _tool_advance_plan,
    "maps_locate": _tool_maps_locate,
}

# Markets subsystem tools (paper-trading shape, Day-1) — splice into
# both dispatch + schema list. Lazy-imported so a failure inside
# markets/* never bricks tool_use.py for the non-markets tools.
try:
    from openjarvis.markets.markets_tools import (
        TOOL_DISPATCH as _MARKETS_DISPATCH,
        TOOL_SCHEMAS as _MARKETS_SCHEMAS,
    )
    _TOOL_DISPATCH.update(_MARKETS_DISPATCH)
except Exception:
    logger.warning("markets tools failed to register", exc_info=True)
    _MARKETS_SCHEMAS = []


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


def _emit_tool_event(name: str, args: dict, ok: bool) -> None:
    """Surface tool calls in the activity log so the operator sees the
    agent working in real time. Best-effort — never raises."""
    try:
        from openjarvis.tools import obsidian_brain
        # Compact label with the first interesting arg value
        first_val = ""
        for k in ("query", "question", "title", "url", "agent", "node", "a", "city", "expression", "target", "action"):
            if k in args and args[k]:
                first_val = str(args[k])[:60]
                break
        op = "tool" if ok else "tool-fail"
        label = f"{name}({first_val})" if first_val else f"{name}()"
        obsidian_brain._emit_event(op, label, kind="tool", source="agent")
    except Exception:
        pass


def _run_tool(name: str, arguments_json: str) -> str:
    try:
        args = json.loads(arguments_json or "{}")
    except json.JSONDecodeError as exc:
        _emit_tool_event(name, {}, False)
        return json.dumps({"error": f"bad arguments json: {exc}"})
    if not isinstance(args, dict):
        return json.dumps({"error": "arguments must be a JSON object"})

    fn = _TOOL_DISPATCH.get(name)
    if fn is not None:
        try:
            result = fn(**args)
            _emit_tool_event(name, args, True)
            return result
        except TypeError as exc:
            _emit_tool_event(name, args, False)
            return json.dumps({"error": f"bad arguments: {exc}"})
        except Exception as exc:
            logger.exception("tool '%s' raised", name)
            _emit_tool_event(name, args, False)
            return json.dumps({"error": str(exc)})

    if name in _agno_instances:
        result = _run_agno_tool(name, args)
        _emit_tool_event(name, args, True)
        return result

    _emit_tool_event(name, args, False)
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
    all_tools = TOOL_SCHEMAS + _agno_schemas + list(_MARKETS_SCHEMAS)
    msgs = list(_messages_to_openai(messages))
    # Splice the tool-use addendum in as a SYSTEM message right before the
    # last user turn. Placing it close to the user message gives it more
    # weight than the persona block at the start (which the voice model
    # may otherwise interpret as 'always answer in 1-3 sentences').
    last_user_idx = next(
        (i for i in range(len(msgs) - 1, -1, -1) if msgs[i].get("role") == "user"),
        None,
    )
    addendum = {"role": "system", "content": _TOOL_USE_ADDENDUM}
    if last_user_idx is None:
        msgs.append(addendum)
    else:
        msgs.insert(last_user_idx, addendum)

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

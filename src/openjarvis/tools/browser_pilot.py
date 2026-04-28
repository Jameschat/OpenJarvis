"""browser_pilot — autonomous browser-driving agent (gpt-4o + vision).

Architecture
------------
This is an in-process agent — unlike the existing claude/codex agents
which shell out to ``claude -p`` or ``codex exec`` subprocesses, the
browser pilot needs direct access to the Python ``ToolRegistry`` so it
can drive the Playwright-backed ``browser_*`` tools registered in
``tools/browser.py``. To make that work we introduced a third agent
provider, ``"python"``, which agent_runner._run_task forks on by
calling ``run_task(task)`` here directly in its worker thread instead
of spawning a CLI.

The reasoning loop is OpenAI function-calling against gpt-4o, using
the OpenAI-format schemas exposed by each registered browser tool.
After every screenshot the base64 PNG is injected as a separate
multimodal user message so gpt-4o's vision can actually see the page.

Cost & turn caps
----------------
Bounded by env vars (defaults reasonable for a £-aware operator):
  OPENJARVIS_BROWSER_PILOT_BUDGET_USD   (default 0.50)
  OPENJARVIS_BROWSER_PILOT_MAX_TURNS    (default 25)
  OPENJARVIS_BROWSER_PILOT_MODEL        (default gpt-4o)

Hitting either cap is a soft stop — the loop exits, writes whatever
final text it has into RESULT.md, and returns. No silent retries.

Outputs
-------
Per-task workspace receives:
  - browser_pilot.log     — every turn's tool calls + token usage
  - RESULT.md             — final briefing the operator (and other
                            agents in the same project) can read
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config — env-overridable so the operator can tighten or loosen at runtime
# ---------------------------------------------------------------------------

_DEFAULT_BUDGET_USD = float(
    os.environ.get("OPENJARVIS_BROWSER_PILOT_BUDGET_USD", "0.50")
)
_DEFAULT_MAX_TURNS = int(
    os.environ.get("OPENJARVIS_BROWSER_PILOT_MAX_TURNS", "25")
)
_MODEL = os.environ.get("OPENJARVIS_BROWSER_PILOT_MODEL", "gpt-4o")

# gpt-4o list pricing as of Nov 2024 — refresh if OpenAI changes it.
# These are only used for the cost-cap estimate; if the kill point is
# wrong the worst case is the loop runs slightly longer or shorter than
# the operator intended.
_PRICE_INPUT_PER_1M_USD = float(
    os.environ.get("OPENJARVIS_BROWSER_PILOT_PRICE_IN", "2.50")
)
_PRICE_OUTPUT_PER_1M_USD = float(
    os.environ.get("OPENJARVIS_BROWSER_PILOT_PRICE_OUT", "10.00")
)

# Keep the most-recent N screenshots in the rolling vision context; older
# ones get replaced with a stub. ~1k tokens per 1024x768 image at "auto"
# detail, so 5 = ~5k tokens of vision overhead per turn — bounded.
_SCREENSHOT_RETENTION = 5

# The whitelist of tools the pilot is allowed to call. The conversational
# tool_use.py brain CANNOT call these; this agent has its own scope.
_BROWSER_TOOL_NAMES = (
    "browser_navigate",
    "browser_click",
    "browser_type",
    "browser_screenshot",
    "browser_extract",
    # Briefing helper — transcript fetch + structured summary written to vault.
    # Listed alongside the browser_* tools so the pilot can reach for it
    # naturally on "watch X and brief me" goals.
    "youtube_brief_url",
)

_SYSTEM_PROMPT = """You are JARVIS Browser Pilot — an autonomous web-browsing agent driving a real Chromium instance via the browser_* tools.

Your job: pursue the operator's goal end-to-end and return a useful briefing as your final response.

Method (one step per turn):
  1. Plan the next single action toward the goal.
  2. Call exactly one browser_* tool. Most useful first move: browser_navigate to a sensible URL (a search engine if you don't know the URL).
  3. After significant page changes, call browser_screenshot — you will then SEE the page through your vision input on the next turn.
  4. To pull readable text from the page, call browser_extract. To click, browser_click (selector OR x/y coords). To type, browser_type.
  5. When you have enough information, STOP calling tools and write a final briefing as plain text. The briefing should be markdown-formatted and answer the operator's goal.

Discipline:
  - Be purposeful. One targeted navigation beats ten exploratory clicks.
  - **Prefer direct URLs over typing into search boxes.** Most sites have URL-pattern search; use them and skip the click-and-type dance entirely. Examples:
      YouTube search:    https://www.youtube.com/results?search_query=YOUR+QUERY
      Google search:     https://www.google.com/search?q=YOUR+QUERY
      Wikipedia:         https://en.wikipedia.org/wiki/Special:Search?search=YOUR+QUERY
      DuckDuckGo:        https://duckduckgo.com/?q=YOUR+QUERY
  - After navigating, prefer browser_extract (returns page text) over browser_screenshot when you need to read content. Use screenshot only when you need to SEE the layout / confirm visual state. Extract is faster, cheaper, and gives you actual URLs to click.
  - For YouTube tasks ("watch X and brief me"): navigate directly to https://www.youtube.com/results?search_query=X, browser_extract to read the results page, identify the most-relevant video's full URL (looks like https://www.youtube.com/watch?v=XXXXXXXXXXX), THEN call youtube_brief_url with that URL — that fetches the transcript and writes a structured briefing to the operator's vault under Brain/Knowledge/. After youtube_brief_url returns, your final response should be one short paragraph telling the operator the briefing is ready (mention the title) — do NOT re-summarise the video, the briefing already exists on disk.
  - If you hit a captcha / login wall: stop, report it in your final response. Do not waste budget retrying.
  - Cost is metered: ~25 tool turns and ~$0.50 max. Be economical.
"""


# ---------------------------------------------------------------------------
# Tool plumbing
# ---------------------------------------------------------------------------


def _build_tool_schemas() -> List[Dict[str, Any]]:
    """Pull OpenAI function-calling schemas from each registered browser tool."""
    from openjarvis.core.registry import ToolRegistry

    schemas: List[Dict[str, Any]] = []
    for name in _BROWSER_TOOL_NAMES:
        if not ToolRegistry.contains(name):
            logger.warning("browser_pilot: tool %s not in registry — skipping", name)
            continue
        cls_or_inst = ToolRegistry.get(name)
        inst = cls_or_inst() if isinstance(cls_or_inst, type) else cls_or_inst
        if not hasattr(inst, "to_openai_function"):
            logger.warning("browser_pilot: %s has no to_openai_function()", name)
            continue
        schemas.append(inst.to_openai_function())
    return schemas


def _execute_browser_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Run a single browser_* tool by name. Always returns a dict so the
    caller never has to handle exceptions; tool errors become failures
    that the model can read and react to on the next turn."""
    from openjarvis.core.registry import ToolRegistry

    if not ToolRegistry.contains(name):
        return {"success": False, "content": f"unknown tool {name!r}", "metadata": {}}
    cls_or_inst = ToolRegistry.get(name)
    inst = cls_or_inst() if isinstance(cls_or_inst, type) else cls_or_inst
    try:
        res = inst.execute(**args)
        return {
            "success": getattr(res, "success", True),
            "content": getattr(res, "content", "") or "",
            "metadata": getattr(res, "metadata", {}) or {},
        }
    except Exception as exc:
        logger.exception("browser_pilot: tool %s crashed", name)
        return {"success": False, "content": f"tool error: {exc}", "metadata": {}}


def _estimate_cost_usd(usage: Dict[str, Any]) -> float:
    pt = (usage or {}).get("prompt_tokens", 0) or 0
    ct = (usage or {}).get("completion_tokens", 0) or 0
    return (pt / 1_000_000) * _PRICE_INPUT_PER_1M_USD + (ct / 1_000_000) * _PRICE_OUTPUT_PER_1M_USD


def _trim_old_screenshots(messages: List[Dict[str, Any]], keep: int) -> None:
    """Walk messages newest-first; replace image_url content blocks beyond
    the most-recent ``keep`` screenshots with a stub. Keeps overall message
    structure intact so tool_call_id linkages still work."""
    seen = 0
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        c = m.get("content")
        if not isinstance(c, list):
            continue
        if not any(isinstance(b, dict) and b.get("type") == "image_url" for b in c):
            continue
        seen += 1
        if seen > keep:
            m["content"] = [
                {"type": "text", "text": "(earlier screenshot dropped to bound token cost)"},
            ]


# ---------------------------------------------------------------------------
# Public entry — called by agent_runner._run_task when provider == "python"
# ---------------------------------------------------------------------------


def run_task(task) -> Dict[str, Any]:
    """Run one browser_pilot task end-to-end.

    Caller is agent_runner in a worker thread. ``task.workspace`` is
    expected to be set by the caller before invoking us — we write
    RESULT.md and browser_pilot.log into it.

    Returns a status dict ``{ok, turns, cost_usd, final_text, error?}``.
    """
    try:
        from openai import OpenAI
    except ImportError:
        return {"ok": False, "error": "openai package not installed"}

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"ok": False, "error": "OPENAI_API_KEY not set"}

    ws_str = getattr(task, "workspace", None)
    ws = Path(ws_str) if ws_str else None
    if ws is None or not ws.exists():
        return {"ok": False, "error": "no workspace"}

    tools = _build_tool_schemas()
    if not tools:
        return {"ok": False, "error": "no browser_* tools registered"}

    transcript_log = ws / "browser_pilot.log"
    result_md = ws / "RESULT.md"
    client = OpenAI(api_key=api_key)

    def _log(line: str) -> None:
        try:
            with transcript_log.open("a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {line}\n")
        except Exception:
            pass

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"GOAL: {task.prompt}\n\nBegin."},
    ]

    total_cost = 0.0
    turns = 0
    last_text = ""
    stop_reason = "max_turns"

    _log(
        f"START goal={task.prompt!r} model={_MODEL} "
        f"budget=${_DEFAULT_BUDGET_USD:.2f} max_turns={_DEFAULT_MAX_TURNS}"
    )

    while turns < _DEFAULT_MAX_TURNS:
        turns += 1
        try:
            resp = client.chat.completions.create(
                model=_MODEL,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.2,
            )
        except Exception as exc:
            _log(f"OPENAI_ERR {exc}")
            stop_reason = "openai_error"
            return {
                "ok": False,
                "error": f"openai call failed: {exc}",
                "turns": turns,
                "cost_usd": total_cost,
            }

        usage_obj = getattr(resp, "usage", None)
        usage: Dict[str, Any] = {}
        if usage_obj is not None:
            usage = (
                usage_obj.model_dump()
                if hasattr(usage_obj, "model_dump")
                else dict(usage_obj)
            )
        total_cost += _estimate_cost_usd(usage)
        _log(f"turn={turns} usage={usage} cost_so_far=${total_cost:.4f}")

        if total_cost > _DEFAULT_BUDGET_USD:
            _log(f"BUDGET_EXCEEDED ${total_cost:.4f} > ${_DEFAULT_BUDGET_USD:.2f}")
            stop_reason = "budget_exceeded"
            break

        msg = resp.choices[0].message
        if msg.content:
            last_text = msg.content

        msg_dict: Dict[str, Any] = {"role": "assistant"}
        if msg.content is not None:
            msg_dict["content"] = msg.content
        if msg.tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        messages.append(msg_dict)

        if not msg.tool_calls:
            stop_reason = "final_response"
            _log(f"FINAL turns={turns} cost=${total_cost:.4f}")
            break

        # Execute every requested tool call this turn. For screenshots,
        # also append a vision message so gpt-4o sees the image next turn.
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            _log(f"TOOL {name}({args})")
            res = _execute_browser_tool(name, args)
            success = res["success"]
            content_str = res["content"]
            meta = res["metadata"]

            tool_summary = json.dumps(
                {
                    "success": success,
                    "content": (content_str or "")[:4000],
                    "metadata": {
                        k: v for k, v in (meta or {}).items()
                        if k != "screenshot_base64"
                    },
                }
            )[:8000]
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": tool_summary}
            )

            if name == "browser_screenshot":
                b64 = (meta or {}).get("screenshot_base64")
                if b64:
                    data_url = f"data:image/png;base64,{b64}"
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "(latest browser screenshot — review and continue)",
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {"url": data_url, "detail": "auto"},
                                },
                            ],
                        }
                    )

        _trim_old_screenshots(messages, keep=_SCREENSHOT_RETENTION)

    # Write final briefing — even on partial / budget-cut runs
    try:
        result_md.write_text(
            "# Browser pilot result\n\n"
            f"**Goal:** {task.prompt}\n\n"
            f"**Stop reason:** {stop_reason}  "
            f"**Turns:** {turns}/{_DEFAULT_MAX_TURNS}  "
            f"**Cost:** ${total_cost:.4f} / ${_DEFAULT_BUDGET_USD:.2f}\n\n"
            "## Final response\n\n"
            f"{last_text or '_(no final text response — see browser_pilot.log)_'}\n",
            encoding="utf-8",
        )
    except Exception:
        logger.exception("browser_pilot: failed to write RESULT.md")

    _log(f"DONE turns={turns} cost=${total_cost:.4f} reason={stop_reason}")
    return {
        "ok": True,
        "turns": turns,
        "cost_usd": total_cost,
        "stop_reason": stop_reason,
        "final_text": last_text,
        "result_path": str(result_md),
    }

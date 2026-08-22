"""OrchestratorAgent — multi-turn agent with tool-calling loop.

Supports two modes:

- **function_calling** (default): Uses OpenAI-format tool definitions and
  parses ``tool_calls`` from the engine response.
- **structured**: Uses a THOUGHT/TOOL/INPUT/FINAL_ANSWER text format
  (like ReAct) with a canonical system prompt from the orchestrator
  prompt registry.  This is the format used by the SFT/GRPO training
  pipelines, making the Orchestrator a distinctive trainable agent type.
"""

from __future__ import annotations

import concurrent.futures
import re
from typing import Any, List, Optional

from openjarvis.agents._stubs import AgentContext, AgentResult, ToolUsingAgent
from openjarvis.core.events import EventBus
from openjarvis.core.registry import AgentRegistry
from openjarvis.core.types import Message, Role, ToolCall, ToolResult
from openjarvis.engine._stubs import InferenceEngine
from openjarvis.tools._stubs import BaseTool

# System prompt for function-calling mode when the agent has tools. Local models
# tend to *describe* an action instead of calling the tool unless nudged; this
# makes them act. Also steers multi-step work through todo_write so the plan
# checklist populates.
_TOOL_USE_SYSTEM_PROMPT = (
    "You are Jarvis, a capable AI assistant running locally with real tools. "
    "When the user asks you to read, edit, create, or run files or code, or to "
    "look something up, USE the appropriate tool by calling it directly "
    "(e.g. file_read, file_edit, file_write, shell_exec, web_search) — do not "
    "merely describe what you would do. For multi-step work, call todo_write "
    "first to lay out the plan, then carry it out, updating the todo as you go. "
    "\n\n"
    "You DO have working tools that act on THIS real machine: file_read, "
    "file_edit, file_write, and shell_exec operate on the actual filesystem. "
    "NEVER reply that you 'don't have access to the file system', 'can't browse "
    "local files', or that the user should open File Explorer — you can do it "
    "yourself by calling the tools. Do it. "
    "Your workspace is E:/Claude. Projects you have worked on live under it "
    "(e.g. E:/Claude/westhill-hotel). The Obsidian vault at E:/Claude/Obsidian "
    "holds persistent notes about prior work. Before telling the user you don't "
    "know about something they reference, LOOK FOR IT: list/read files in the "
    "workspace (file_read), search the vault, and search memory "
    "(memory_search / memory_retrieve / retrieval). Only say you can't find it after you have "
    "actually checked. "
    "When building or changing a website/web app, preview it: call preview_start "
    "on the project folder to get a URL, then browser_navigate to it and "
    "browser_screenshot to see the result, and iterate. "
    "\n\n"
    "Several of your tools return LIVE, real-time data: weather (current "
    "conditions + forecast), web_search, crypto (prices), calendar, and the "
    "home-automation tools. A tool result in this conversation is REAL, "
    "CURRENT data fetched just now on this machine — treat it as ground truth and "
    "answer directly from it. NEVER say you 'don't have access to real-time data', "
    "'can't check the live weather', or tell the user to go look it up on another "
    "website AFTER a tool has already returned the answer. If the weather tool "
    "returned a forecast, give the user that forecast. "
    "\n\n"
    "You are NOT a generic stateless chatbot — you are Jarvis, with PERSISTENT "
    "MEMORY across sessions. Your Obsidian vault (E:/Claude/Obsidian), an episodic "
    "memory store (agentmemory), and a knowledge graph (graphify) carry forward what "
    "you and the user did in earlier sessions, and salient turns are captured back "
    "into them automatically — so past conversations ARE remembered. NEVER tell the "
    "user 'I can't access your files or systems', 'I cannot maintain persistent "
    "memory across conversations', or 'each session is independent' — those "
    "statements are FALSE for you and must never be said. When the user asks what you "
    "remember, did before, or know about their projects, SEARCH first "
    "(memory_search, memory_retrieve, retrieval) and read the vault "
    "(file_read), then answer from what you actually find. "
    "When the work is done, give a short, clear final answer."
)

# Cap tool output fed back into the model. A large result (e.g. a big directory
# listing) can overflow a small-context lane (the 27B MTP lane is 16K) and crash
# it mid-turn. Truncate so the agentic loop stays stable.
_MAX_TOOL_RESULT_CHARS = 6000


def _cap_tool_content(content: str) -> str:
    if not content:
        return content or ""
    if len(content) <= _MAX_TOOL_RESULT_CHARS:
        return content
    head = content[:_MAX_TOOL_RESULT_CHARS]
    return f"{head}\n[... truncated {len(content) - _MAX_TOOL_RESULT_CHARS} chars ...]"


def _format_tool_result(result: "ToolResult") -> str:
    """Frame the tool result fed back to the model. A successful result is wrapped
    so a reasoning model treats it as authoritative live data and answers from it,
    instead of falling back to an 'I have no real-time access' refusal despite the
    data being right here. Failures pass through capped/plain."""
    body = _cap_tool_content(result.content or "")
    if getattr(result, "success", True):
        return (
            f"[Real, current result from the `{result.tool_name}` tool, fetched just "
            f"now on this machine. This IS live data — answer from it directly; do NOT "
            f"claim you lack access to this information.]\n{body}"
        )
    return body


def _has_successful_results(results: "list[ToolResult]") -> bool:
    return any(getattr(r, "success", False) for r in results)


# Phrases a local model emits when it deflects instead of using a tool result it
# already holds. Matched on the post-think final answer to trigger re-synthesis.
_REFUSAL_MARKERS = (
    "don't have access",
    "do not have access",
    "don't have real-time",
    "do not have real-time",
    "can't access real-time",
    "cannot access real-time",
    "can't provide real-time",
    "cannot provide real-time",
    "i'm unable to access",
    "i am unable to access",
    "can't check the live",
    "cannot check the live",
    "don't have the ability to",
    "as an ai",
    "i recommend checking",
    "please check a reliable",
    "real-time weather",
)


def _looks_like_ungrounded_refusal(content: str) -> bool:
    if not content:
        return False
    low = content.lower()
    # Strip a leading <think>…</think> so we judge the user-facing answer only.
    if "</think>" in low:
        low = low.split("</think>", 1)[1]
    return any(marker in low for marker in _REFUSAL_MARKERS)


def _maybe_brain_context() -> str:
    """A COMPACT vault snapshot (active project names + a few recent notes) to
    append to the chat system prompt, so the agent knows what's already in the
    second brain and can pull details on demand via file_read / retrieval /
    memory_search.

    Deliberately NOT the heavy ``agent_runner._build_brain_context`` block: that
    is an ~11 KB autonomous-agent preamble ("read 00 Session Handoff.md, write via
    curl…") which, injected into every interactive chat turn, both bloats the
    prompt and destabilises the model (degenerate output + writing files as text).
    Keep this small and declarative. Kill-switch: OPENJARVIS_BRAIN_CONTEXT=0.
    Fully guarded — never raises, never blocks chat."""
    import os

    if os.environ.get("OPENJARVIS_BRAIN_CONTEXT", "1").strip().lower() in (
        "0",
        "false",
        "off",
    ):
        return ""
    try:
        from openjarvis.tools import obsidian_brain as ob

        root = ob.BRAIN_ROOT
        if not root.exists():
            return ""

        def _recent(folder: str, n: int, dirs: bool) -> list[str]:
            d = root / folder
            if not d.exists():
                return []
            items = [
                p
                for p in d.iterdir()
                if (p.is_dir() if dirs else p.suffix == ".md")
                and not p.name.startswith(("_", "."))
            ]
            items.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return [p.stem if not dirs else p.name for p in items[:n]]

        projects = _recent("Projects", 12, dirs=True)
        knowledge = _recent("Knowledge", 6, dirs=False)
        if not (projects or knowledge):
            return ""

        lines = [
            "## Your second brain (Obsidian vault)",
            f"You keep persistent notes at {root}. You can read any of them with "
            "file_read, and search them with retrieval / memory_search. "
            "Use them before saying you don't know about something the user references.",
        ]
        if projects:
            lines.append("Active projects: " + ", ".join(projects) + ".")
        if knowledge:
            lines.append("Recent knowledge notes: " + "; ".join(knowledge) + ".")
        return "\n".join(lines)
    except Exception:
        return ""


@AgentRegistry.register("orchestrator")
class OrchestratorAgent(ToolUsingAgent):
    """Multi-turn agent that routes between tools and the LLM.

    Implements a tool-calling loop:
    1. Send messages with tool definitions to the engine.
    2. If the response contains tool_calls, execute them and loop.
    3. If no tool_calls, return the final answer.
    4. Stop after ``max_turns`` iterations.

    In **structured** mode the agent instead uses a
    ``THOUGHT: / TOOL: / INPUT: / FINAL_ANSWER:`` text protocol
    identical to the format used by the orchestrator SFT/GRPO
    training pipelines.
    """

    agent_id = "orchestrator"
    _default_temperature = 0.7
    # Per-turn generation cap. Must be large for agentic CODING: a file_write
    # crams the whole file into the tool-call's JSON `arguments` string, and if the
    # generation is cut off mid-string the JSON is invalid -> the lane 500s
    # ("Failed to parse tool call arguments"). 1024 truncated any file >~4KB (and a
    # thinking model burns budget reasoning first). 16384 ≈ ~60KB/file and fits
    # every lane's context window. It's a cap, not a target — normal turns stop at
    # the stop token well before it.
    _default_max_tokens = 16384
    _default_max_turns = 10

    def __init__(
        self,
        engine: InferenceEngine,
        model: str,
        *,
        tools: Optional[List[BaseTool]] = None,
        bus: Optional[EventBus] = None,
        max_turns: Optional[int] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        mode: str = "function_calling",
        system_prompt: Optional[str] = None,
        parallel_tools: bool = True,
        interactive: bool = False,
        confirm_callback=None,
    ) -> None:
        super().__init__(
            engine,
            model,
            tools=tools,
            bus=bus,
            max_turns=max_turns,
            temperature=temperature,
            max_tokens=max_tokens,
            interactive=interactive,
            confirm_callback=confirm_callback,
        )
        self._mode = mode
        self._system_prompt = system_prompt
        self._parallel_tools = parallel_tools

    def run(
        self,
        input: str,
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        if self._mode == "structured":
            return self._run_structured(input, context, **kwargs)
        return self._run_function_calling(input, context, **kwargs)

    # ------------------------------------------------------------------
    # Structured mode (THOUGHT/TOOL/INPUT/FINAL_ANSWER)
    # ------------------------------------------------------------------

    def _run_structured(
        self,
        input: str,
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        self._emit_turn_start(input)

        # Build system prompt
        if self._system_prompt:
            sys_prompt = self._system_prompt
        else:
            from openjarvis.learning.intelligence.orchestrator.prompt_registry import (
                build_system_prompt,
            )

            sys_prompt = build_system_prompt(tools=self._tools)

        messages = self._build_messages(input, context, system_prompt=sys_prompt)

        all_tool_results: list[ToolResult] = []
        turns = 0

        for _turn in range(self._max_turns):
            turns += 1

            if self._loop_guard:
                messages = self._loop_guard.compress_context(messages)

            result = self._generate(messages)
            content = result.get("content", "")

            parsed = self._parse_structured_response(content)

            # FINAL_ANSWER -> done
            if parsed["final_answer"]:
                self._emit_turn_end(turns=turns)
                return AgentResult(
                    content=parsed["final_answer"],
                    tool_results=all_tool_results,
                    turns=turns,
                )

            # TOOL -> execute
            if parsed["tool"]:
                messages.append(Message(role=Role.ASSISTANT, content=content))

                tool_call = ToolCall(
                    id=f"orch_{turns}",
                    name=parsed["tool"],
                    arguments=parsed["input"] or "{}",
                )
                tool_result = self._executor.execute(tool_call)
                all_tool_results.append(tool_result)

                observation = f"Observation: {_format_tool_result(tool_result)}"
                messages.append(Message(role=Role.USER, content=observation))
                continue

            # Neither -> treat content as final answer
            self._emit_turn_end(turns=turns)
            return AgentResult(
                content=content,
                tool_results=all_tool_results,
                turns=turns,
            )

        # Max turns exceeded
        return self._max_turns_result(all_tool_results, turns)

    @staticmethod
    def _parse_structured_response(text: str) -> dict:
        """Parse THOUGHT/TOOL/INPUT/FINAL_ANSWER from model output."""
        result = {
            "thought": "",
            "tool": "",
            "input": "",
            "final_answer": "",
        }

        thought_match = re.search(
            r"THOUGHT:\s*(.+?)(?=\nTOOL:|\nFINAL[_ ]?ANSWER:|\Z)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if thought_match:
            result["thought"] = thought_match.group(1).strip()

        final_match = re.search(
            r"FINAL[_ ]?ANSWER:\s*(.+)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if final_match:
            result["final_answer"] = final_match.group(1).strip()
            return result

        tool_match = re.search(r"TOOL:\s*(.+)", text, re.IGNORECASE)
        if tool_match:
            result["tool"] = tool_match.group(1).strip()

        input_match = re.search(
            r"INPUT:\s*(.+?)(?=\nTHOUGHT:|\nTOOL:|\nFINAL|\Z)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if input_match:
            result["input"] = input_match.group(1).strip()

        return result

    # ------------------------------------------------------------------
    # Function-calling mode (original behaviour)
    # ------------------------------------------------------------------

    def _run_function_calling(
        self,
        input: str,
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        self._emit_turn_start(input)

        # Build initial messages. When tools are available and the caller didn't
        # set an explicit system prompt, nudge the model to actually CALL tools.
        sys_prompt = self._system_prompt
        if not sys_prompt and self._tools:
            sys_prompt = _TOOL_USE_SYSTEM_PROMPT
            # Append the live vault snapshot once per run (not per loop turn) so
            # the agent reads from the second brain proactively.
            brain = _maybe_brain_context()
            if brain:
                sys_prompt = f"{sys_prompt}\n\n{brain}"
        messages = self._build_messages(input, context, system_prompt=sys_prompt)

        # Get OpenAI-format tool definitions
        openai_tools = self._executor.get_openai_tools() if self._tools else []

        all_tool_results: list[ToolResult] = []
        turns = 0
        total_prompt_tokens = 0
        total_completion_tokens = 0

        for _turn in range(self._max_turns):
            turns += 1

            if self._loop_guard:
                messages = self._loop_guard.compress_context(messages)

            # Build generate kwargs
            gen_kwargs: dict[str, Any] = {}
            if openai_tools:
                gen_kwargs["tools"] = openai_tools

            result = self._generate(messages, **gen_kwargs)

            # Accumulate token usage
            usage = result.get("usage", {})
            total_prompt_tokens += usage.get("prompt_tokens", 0)
            total_completion_tokens += usage.get("completion_tokens", 0)

            content = result.get("content", "")
            raw_tool_calls = result.get("tool_calls", [])

            # No tool calls -> check continuation, then final answer
            if not raw_tool_calls:
                content = self._check_continuation(result, messages)
                # Grounded-synthesis recovery: some local reasoning models, when the
                # tool schemas are still present in the request, talk themselves into
                # "I'm an AI, I don't have real-time access" and deflect — even though
                # a tool already returned the answer earlier this run. (Measured: with
                # tools in the payload the 35B refused 6/6; with tools absent it
                # answered 6/6 from the identical result.) If the final answer reads
                # like that deflection while we hold a successful tool result, re-ask
                # ONCE with no tools so the model must answer from what it has.
                if (
                    _has_successful_results(all_tool_results)
                    and _looks_like_ungrounded_refusal(content)
                ):
                    recovered = self._generate(messages)  # no tools -> forces synthesis
                    rec_content = self._check_continuation(recovered, messages)
                    if rec_content and not _looks_like_ungrounded_refusal(rec_content):
                        content = rec_content
                content = self._strip_think_tags(content)
                self._emit_turn_end(turns=turns, content_length=len(content))
                return AgentResult(
                    content=content,
                    tool_results=all_tool_results,
                    turns=turns,
                    metadata={
                        "prompt_tokens": total_prompt_tokens,
                        "completion_tokens": total_completion_tokens,
                        "total_tokens": total_prompt_tokens + total_completion_tokens,
                    },
                )

            # Build ToolCall objects from raw dicts
            tool_calls = [
                ToolCall(
                    id=tc.get("id", f"call_{i}"),
                    name=tc.get("name", ""),
                    arguments=tc.get("arguments", "{}"),
                )
                for i, tc in enumerate(raw_tool_calls)
            ]

            # Append assistant message with tool calls
            messages.append(
                Message(
                    role=Role.ASSISTANT,
                    content=content,
                    tool_calls=tool_calls,
                )
            )

            # Execute each tool (with loop guard check) and append results
            if self._parallel_tools and len(tool_calls) > 1:
                # Parallel execution
                def _exec_tool(tc: ToolCall) -> tuple:
                    if self._loop_guard:
                        verdict = self._loop_guard.check_call(
                            tc.name,
                            tc.arguments,
                        )
                        if verdict.blocked:
                            return tc, ToolResult(
                                tool_name=tc.name,
                                content=f"Loop guard: {verdict.reason}",
                                success=False,
                            )
                    return tc, self._executor.execute(tc)

                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=len(tool_calls),
                ) as pool:
                    futures = {pool.submit(_exec_tool, tc): tc for tc in tool_calls}
                    results_map: dict[int, tuple] = {}
                    for future in concurrent.futures.as_completed(futures):
                        tc_orig = futures[future]
                        results_map[id(tc_orig)] = future.result()

                # Append results in original order
                for tc in tool_calls:
                    _, tool_result = results_map[id(tc)]
                    all_tool_results.append(tool_result)
                    messages.append(
                        Message(
                            role=Role.TOOL,
                            content=_format_tool_result(tool_result),
                            tool_call_id=tc.id,
                            name=tc.name,
                        )
                    )
            else:
                # Sequential execution
                for tc in tool_calls:
                    # Loop guard check before execution
                    if self._loop_guard:
                        verdict = self._loop_guard.check_call(
                            tc.name,
                            tc.arguments,
                        )
                        if verdict.blocked:
                            tool_result = ToolResult(
                                tool_name=tc.name,
                                content=f"Loop guard: {verdict.reason}",
                                success=False,
                            )
                            all_tool_results.append(tool_result)
                            messages.append(
                                Message(
                                    role=Role.TOOL,
                                    content=_format_tool_result(tool_result),
                                    tool_call_id=tc.id,
                                    name=tc.name,
                                )
                            )
                            continue

                    tool_result = self._executor.execute(tc)
                    all_tool_results.append(tool_result)

                    # Append tool response message
                    messages.append(
                        Message(
                            role=Role.TOOL,
                            content=_format_tool_result(tool_result),
                            tool_call_id=tc.id,
                            name=tc.name,
                        )
                    )

        # Max turns exceeded
        final_content = self._strip_think_tags(content) if content else ""
        self._emit_turn_end(turns=turns, max_turns_exceeded=True)
        return AgentResult(
            content=final_content or "Maximum turns reached without a final answer.",
            tool_results=all_tool_results,
            turns=turns,
            metadata={
                "max_turns_exceeded": True,
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "total_tokens": total_prompt_tokens + total_completion_tokens,
            },
        )


__all__ = ["OrchestratorAgent"]

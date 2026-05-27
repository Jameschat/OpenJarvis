from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from openjarvis.tools import studio_context, studio_research, studio_store, studio_workflows

STUDIO_RUN_STALE_AFTER_SECONDS = 300
STUDIO_CONTEXT_CHAR_LIMIT = int(os.environ.get("OPENJARVIS_STUDIO_CONTEXT_CHAR_LIMIT", "800000"))
BRAIN_ROOT = Path(os.environ.get("OPENJARVIS_BRAIN_ROOT", r"E:\Claude\Obsidian\Claude\Brain"))
_MEMORY_STOPWORDS = {
    "about",
    "built",
    "claude",
    "codex",
    "from",
    "have",
    "jarvis",
    "know",
    "memory",
    "project",
    "that",
    "what",
    "with",
    "your",
    "website",
}


_LIGHTWEIGHT_CHAT_PREFIXES = (
    "hi",
    "hello",
    "hey",
    "morning",
    "afternoon",
    "evening",
    "good morning",
    "good afternoon",
    "good evening",
)


def _lightweight_chat_reply(prompt: str) -> str | None:
    text = " ".join((prompt or "").strip().lower().split())
    if not text or len(text) > 120:
        return None
    if ("model" in text or "qwen" in text) and any(term in text for term in ("running", "run", "using", "loaded")):
        return (
            "I'm running `qwen3.6-27b-local` as the Studio local-first model. "
            "That routes to Qwen 3.6 27B through the local BeeLlama/Ollama path, "
            "with Claude/Codex kept as escalation paths."
        )
    if any(term in text for term in ("build", "create", "fix", "research", "search", "backtest", "run ")):
        return None
    if text in {"thanks", "thank you"}:
        return "You're welcome."
    if text.startswith(_LIGHTWEIGHT_CHAT_PREFIXES):
        greeting = "Evening"
        if "morning" in text:
            greeting = "Morning"
        elif "afternoon" in text:
            greeting = "Afternoon"
        elif text.startswith(("hi", "hello", "hey")):
            greeting = "Hello"
        if "how are you" in text or "how's it going" in text or "how are things" in text:
            return f"{greeting}. I'm online and ready. What do you want to work on?"
        return f"{greeting}. I'm here and ready."
    return None


def _looks_like_memory_question(prompt: str) -> bool:
    text = " ".join((prompt or "").strip().lower().split())
    if len(text) > 260:
        return False
    starters = (
        "from memory",
        "from your memory",
        "using memory",
        "using your memory",
        "what do you know",
        "what do we know",
        "do you remember",
        "what have we built",
        "what did we build",
        "tell me about",
    )
    return text.startswith(starters)


def _context_direct_reply(prompt: str, context_pack: dict[str, Any]) -> str | None:
    if not _looks_like_memory_question(prompt):
        return None
    markdown = str(context_pack.get("markdown") or "").strip()
    if not markdown:
        return "I do not have a useful saved memory for that yet."
    useful_lines: list[str] = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line or line.startswith(("==", "###", "```")):
            continue
        if line.startswith(("- ", "Project:", "Query:", "## Vault hits", "## Episodic memory")):
            useful_lines.append(line)
        if len(useful_lines) >= 8:
            break
    if not useful_lines:
        useful_lines = [markdown[:900]]
    body = "\n".join(useful_lines)
    return (
        "From Jarvis memory/context, this is what I can see:\n\n"
        f"{body}\n\n"
        "I can dig deeper through the vault, CodeGraph, or live web research if you want a fuller project brief."
    )


def _fast_vault_memory_reply(prompt: str) -> str | None:
    if not _looks_like_memory_question(prompt):
        return None
    words = [
        word
        for word in "".join(ch.lower() if ch.isalnum() else " " for ch in prompt).split()
        if len(word) > 3 and word not in _MEMORY_STOPWORDS
    ]
    if not words:
        return None
    try:
        from openjarvis.tools import obsidian_brain

        root = Path(obsidian_brain.BRAIN_ROOT)
    except Exception:
        return None
    if not root.exists():
        return None
    scored: list[tuple[int, str, str]] = []
    for path in root.rglob("*.md"):
        try:
            rel = path.relative_to(root).as_posix()
            haystack = f"{rel}\n{path.read_text(encoding='utf-8', errors='replace')[:20000]}"
        except Exception:
            continue
        lower = haystack.lower()
        score = sum(lower.count(word) for word in words)
        if score <= 0:
            continue
        first = min((lower.find(word) for word in words if word in lower), default=0)
        start = max(0, first - 160)
        snippet = haystack[start : start + 620].replace("\n", " ").strip()
        scored.append((score, rel, snippet))
    if not scored:
        return "I checked the local vault memory and did not find a saved Networx/project note matching that wording."
    scored.sort(key=lambda item: item[0], reverse=True)
    lines = ["From local vault memory, I found:"]
    for score, rel, snippet in scored[:4]:
        lines.append(f"- `{rel}`: {snippet}")
    lines.append("")
    lines.append("This was answered from local vault files, not the Qwen planner.")
    return "\n".join(lines)


def _queue_agent_task(
    *,
    title: str,
    agent_id: str,
    prompt: str,
    project_id: str | None = None,
) -> str:
    from openjarvis.tools import agent_runner

    return agent_runner.add_task(
        title=title,
        agent_id=agent_id,
        prompt=prompt,
        project_id=project_id,
        priority=20,
    )


def _persist_run_status(
    store: studio_store.StudioStore,
    run: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    run["status"] = status
    run["updated_at"] = studio_store.utc_now()
    store._write_json(store._run_path(run["id"]), run)
    return store.get_run(run["id"])


def _chat_used_chars(chat: dict[str, Any]) -> int:
    total = 0
    for message in chat.get("messages", []):
        total += len(str(message.get("content") or ""))
    return total


def _context_status(percent: int) -> str:
    if percent >= 90:
        return "critical"
    if percent >= 75:
        return "warning"
    return "normal"


def _build_context_handoff_note(chat: dict[str, Any], pressure: dict[str, Any]) -> str:
    messages = chat.get("messages", [])
    recent = messages[-16:]
    lines = [
        "---",
        "type: session",
        "tags: [jarvis-studio, context-handoff]",
        f"date: {studio_store.utc_now()[:10]}",
        "---",
        "",
        "# Jarvis Studio Context Handoff",
        "",
        f"Chat: {chat.get('title') or chat.get('id')}",
        f"Project: {chat.get('project_id') or 'openjarvis'}",
        f"Context pressure: {pressure['percent']}% ({pressure['used_chars']} / {pressure['limit_chars']} chars)",
        "",
        "## Continue From Here",
        "",
        "Use this note as the starting memory for a new Studio chat when the current chat is close to full context.",
        "",
        "## Recent Session Messages",
        "",
    ]
    for message in recent:
        role = str(message.get("role") or "message").title()
        content = str(message.get("content") or "").strip()
        if len(content) > 1200:
            content = content[:1200].rstrip() + "\n...[truncated]"
        lines.extend([f"### {role}", "", content or "(empty)", ""])
    lines.extend([
        "## Next Action",
        "",
        "Open a new Jarvis Studio chat, reference this handoff, and continue with the latest unfinished request.",
        "",
    ])
    return "\n".join(lines)


def _write_context_handoff(store: studio_store.StudioStore, chat: dict[str, Any], pressure: dict[str, Any]) -> dict[str, Any]:
    existing = chat.get("context_handoff")
    if isinstance(existing, dict) and existing.get("path") and Path(str(existing["path"])).exists():
        return existing
    sessions_dir = BRAIN_ROOT / "Sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    slug = studio_store.slugify(str(chat.get("title") or chat.get("id") or "studio-chat"), "studio-chat")
    stamp = studio_store.utc_now()[:10]
    path = sessions_dir / f"{stamp} - Jarvis Studio context handoff - {slug}.md"
    if path.exists():
        path = sessions_dir / f"{stamp} - Jarvis Studio context handoff - {slug}-{chat.get('id', '')[-6:]}.md"
    path.write_text(_build_context_handoff_note(chat, pressure), encoding="utf-8")
    handoff = {
        "path": str(path),
        "created_at": studio_store.utc_now(),
        "percent": pressure["percent"],
        "used_chars": pressure["used_chars"],
    }
    chat["context_handoff"] = handoff
    chat["updated_at"] = studio_store.utc_now()
    store._write_json(store._chat_path(str(chat["id"])), chat)
    return handoff


def enrich_chats_with_context(
    chats: list[dict[str, Any]],
    *,
    store: studio_store.StudioStore | None = None,
    char_limit: int | None = None,
) -> list[dict[str, Any]]:
    """Attach context-window pressure and write a handoff near saturation."""
    limit = max(1, int(char_limit or STUDIO_CONTEXT_CHAR_LIMIT))
    enriched: list[dict[str, Any]] = []
    for chat in chats:
        copy = dict(chat)
        used = _chat_used_chars(copy)
        percent = min(100, int(round((used / limit) * 100)))
        pressure = {
            "used_chars": used,
            "limit_chars": limit,
            "remaining_chars": max(0, limit - used),
            "percent": percent,
            "status": _context_status(percent),
            "handoff_recommended": percent >= 85,
        }
        if pressure["handoff_recommended"] and store is not None and copy.get("id"):
            pressure["handoff"] = _write_context_handoff(store, copy, pressure)
        elif isinstance(copy.get("context_handoff"), dict):
            pressure["handoff"] = copy["context_handoff"]
        copy["context"] = pressure
        enriched.append(copy)
    return enriched


def _load_agent_task_index() -> dict[str, dict[str, Any]]:
    from openjarvis.tools import agent_runner

    try:
        state = json.loads(agent_runner.STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    tasks = state.get("tasks") or []
    return {str(task.get("id")): task for task in tasks if isinstance(task, dict) and task.get("id")}


def _iso_to_epoch(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _read_task_result(task: dict[str, Any]) -> str:
    workspace = task.get("workspace")
    task_id = str(task.get("id") or "")
    if not workspace or not task_id:
        return ""
    root = Path(str(workspace))
    candidates = [
        root / f"{task_id}.RESULT.md",
        root / "RESULT.md",
        root / f"{task_id}.stdout.log",
        root / "stdout.log",
    ]
    for path in candidates:
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if "## Result" in text:
                text = text.split("## Result", 1)[1].strip()
            return text[:6000] + ("\n...[truncated]" if len(text) > 6000 else "")
    return ""


def _task_output_files(task: dict[str, Any]) -> list[dict[str, Any]]:
    workspace = task.get("workspace")
    task_id = str(task.get("id") or "")
    if not workspace:
        return []
    root = Path(str(workspace))
    candidates = [
        root / f"{task_id}.RESULT.md",
        root / "RESULT.md",
        root / "QWEN_TOOL_RESULTS.json",
        root / "FILES_WRITTEN.json",
        root / f"{task_id}.stdout.log",
        root / "stdout.log",
        root / f"{task_id}.stderr.log",
        root / "stderr.log",
    ]
    seen: set[Path] = set()
    outputs: list[dict[str, Any]] = []
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen or not path.exists() or not path.is_file():
            continue
        seen.add(resolved)
        outputs.append(
            {
                "name": path.name,
                "path": str(path),
                "kind": path.suffix.lstrip(".") or "file",
                "size": path.stat().st_size,
                "task_id": task_id,
            }
        )
    return outputs


def enrich_runs_for_studio(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach lightweight task/output details for the Studio progress panel."""
    task_index = _load_agent_task_index()
    enriched: list[dict[str, Any]] = []
    for run in runs:
        copy = dict(run)
        task_details: list[dict[str, Any]] = []
        outputs: list[dict[str, Any]] = []
        for task_id in [str(t) for t in copy.get("tasks", []) if t]:
            task = task_index.get(task_id)
            if not task:
                task_details.append({"id": task_id, "status": "queued"})
                continue
            detail = {
                "id": task_id,
                "title": task.get("title") or task_id,
                "agent_id": task.get("agent_id") or task.get("agent") or "",
                "status": task.get("status") or "queued",
                "workspace": task.get("workspace") or "",
                "error": task.get("error") or "",
                "created_at": task.get("created_at") or task.get("queued_at") or "",
                "started_at": task.get("started_at") or "",
                "finished_at": task.get("finished_at") or "",
            }
            task_outputs = _task_output_files(task)
            detail["outputs"] = task_outputs
            outputs.extend(task_outputs)
            task_details.append(detail)
        copy["task_details"] = task_details
        copy["outputs"] = outputs[:12]
        enriched.append(copy)
    return enriched


def _chat_has_result_message(store: studio_store.StudioStore, chat_id: str, run_id: str) -> bool:
    try:
        chat = store.get_chat(chat_id)
    except KeyError:
        return True
    for message in chat.get("messages", []):
        if message.get("role") == "jarvis" and message.get("run_id") == run_id:
            content = str(message.get("content") or "")
            if "## Result" in content or "Finished result" in content or "Task failed" in content:
                return True
    return False


def sync_completed_run_outputs(store: studio_store.StudioStore | None = None) -> int:
    """Pull completed background agent task outputs back into Studio chats."""
    store = store or studio_store.StudioStore()
    task_index = _load_agent_task_index()
    synced = 0
    terminal = {"done", "failed", "cancelled"}
    for run in store.list_runs():
        if run.get("status") not in {"queued", "running"}:
            continue
        task_ids = [str(t) for t in run.get("tasks", []) if t]
        if not task_ids:
            continue
        tasks = [task_index.get(task_id) for task_id in task_ids]
        if any(task is None or task.get("status") not in terminal for task in tasks):
            known_tasks = [task for task in tasks if task is not None]
            oldest = min(
                (
                    float(task.get("started_at") or 0)
                    for task in known_tasks
                    if task.get("started_at")
                ),
                default=_iso_to_epoch(str(run.get("updated_at") or "")) or 0,
            )
            if oldest and (time.time() - oldest) > STUDIO_RUN_STALE_AFTER_SECONDS:
                updated = store.get_run(run["id"])
                updated["status"] = "failed"
                updated["updated_at"] = studio_store.utc_now()
                store._write_json(store._run_path(updated["id"]), updated)
                store.append_run_event(
                    updated["id"],
                    "run.timeout",
                    "Studio background task timed out",
                    {"tasks": task_ids, "timeout_seconds": STUDIO_RUN_STALE_AFTER_SECONDS},
                )
                if not _chat_has_result_message(store, updated["chat_id"], updated["id"]):
                    store.add_message(
                        updated["chat_id"],
                        "jarvis",
                        (
                            "That Studio task timed out before Jarvis produced a usable result. "
                            "I stopped the spinner so you can retry or ask for a narrower search."
                        ),
                        run_id=updated["id"],
                    )
                synced += 1
            continue

        failed = [task for task in tasks if task and task.get("status") != "done"]
        status = "failed" if failed else "completed"
        updated = store.get_run(run["id"])
        updated["status"] = status
        updated["updated_at"] = studio_store.utc_now()
        store._write_json(store._run_path(updated["id"]), updated)
        store.append_run_event(
            updated["id"],
            "run.completed" if status == "completed" else "run.failed",
            "Background agent task finished",
            {"tasks": task_ids},
        )

        if not _chat_has_result_message(store, updated["chat_id"], updated["id"]):
            result_parts = [_read_task_result(task) for task in tasks if task]
            result_text = "\n\n".join(part for part in result_parts if part).strip()
            if not result_text:
                if status == "failed":
                    reason = "; ".join(
                        str(task.get("error") or "").strip()
                        for task in failed
                        if task and task.get("error")
                    )
                    result_text = f"Task failed: {reason}" if reason else "Task failed."
                else:
                    result_text = "Task completed."
            store.add_message(updated["chat_id"], "jarvis", result_text, run_id=updated["id"])
        synced += 1
    return synced


def start_studio_run(
    project_id: str,
    chat_id: str,
    prompt: str,
    *,
    approved: bool = False,
) -> dict[str, Any]:
    store = studio_store.StudioStore()
    projects = {p["id"]: p for p in store.list_projects()}
    project = projects.get(project_id) or store.ensure_project(
        project_id,
        title=project_id,
    )
    decision = studio_workflows.select_workflow(prompt)
    run = store.create_run(project_id, chat_id, prompt, workflow=decision["workflow"])
    store.append_run_event(run["id"], "run.created", "Studio run created")
    quick_reply = _lightweight_chat_reply(prompt)
    if quick_reply:
        run = _persist_run_status(store, store.get_run(run["id"]), "completed")
        store.append_run_event(
            run["id"],
            "run.completed",
            "Answered lightweight chat directly",
            {"mode": "direct_chat"},
        )
        return {
            "run": store.get_run(run["id"]),
            "context": {"ok": True, "markdown": "", "warnings": []},
            "research": {"ok": False, "markdown": ""},
            "decision": {
                **decision,
                "workflow": "direct_chat",
                "reason": "Lightweight conversational prompt answered directly.",
                "verification": {"required": False, "method": "direct reply"},
                "next_steps": [],
            },
            "reply": quick_reply,
        }
    memory_reply = _fast_vault_memory_reply(prompt)
    if memory_reply:
        run = _persist_run_status(store, store.get_run(run["id"]), "completed")
        store.append_run_event(
            run["id"],
            "run.completed",
            "Answered memory question from local vault search",
            {"mode": "fast_vault_memory"},
        )
        return {
            "run": store.get_run(run["id"]),
            "context": {"ok": True, "markdown": "", "warnings": []},
            "research": {"ok": False, "markdown": ""},
            "decision": {
                **decision,
                "workflow": "fast_vault_memory",
                "reason": "Memory question answered by local vault search.",
                "verification": {"required": False, "method": "vault search"},
                "next_steps": [],
            },
            "reply": memory_reply,
        }
    context_pack = studio_context.build_project_context_pack(prompt, project=project)
    store.append_run_event(
        run["id"],
        "run.context_built",
        "Project context pack built",
        {"warnings": context_pack.get("warnings", [])},
    )
    store.append_run_event(
        run["id"],
        "run.workflow_selected",
        decision["reason"],
        {"workflow": decision["workflow"]},
    )
    context_reply = _context_direct_reply(prompt, context_pack)
    if context_reply:
        run = _persist_run_status(store, store.get_run(run["id"]), "completed")
        store.append_run_event(
            run["id"],
            "run.completed",
            "Answered memory/context question directly",
            {"mode": "context_direct"},
        )
        return {
            "run": store.get_run(run["id"]),
            "context": context_pack,
            "research": {"ok": False, "markdown": ""},
            "decision": {
                **decision,
                "workflow": "context_direct",
                "reason": "Memory/context question answered from Studio context pack.",
                "verification": {"required": False, "method": "context reply"},
                "next_steps": [],
            },
            "reply": context_reply,
        }
    research_pack = {"ok": False, "markdown": ""}
    if decision["workflow"] == "qwen_workflow" or studio_research.should_prefetch_research(prompt):
        research_pack = studio_research.prefetch_research(prompt, limit=4)
        store.append_run_event(
            run["id"],
            "run.research_prefetched",
            "Web/GitHub research prefetched for local Qwen",
            {
                "ok": bool(research_pack.get("ok")),
                "query": research_pack.get("query", prompt),
                "web_hits": len((research_pack.get("web") or {}).get("hits") or []),
                "github_repos": len((research_pack.get("github") or {}).get("repos") or []),
            },
        )

    if decision.get("requires_operator_approval") and not approved:
        run = _persist_run_status(store, store.get_run(run["id"]), "blocked")
        store.append_run_event(
            run["id"],
            "run.blocked",
            "Operator approval required before execution",
            {"risks": decision.get("risks", [])},
        )
        return {
            "run": store.get_run(run["id"]),
            "context": context_pack,
            "research": research_pack,
            "decision": decision,
        }

    agent_id = "qwen-researcher" if decision["workflow"] == "qwen_workflow" else "qwen-planner"
    task_prompt = (
        f"{context_pack.get('markdown', '')}\n\n"
        f"{research_pack.get('markdown', '')}\n\n"
        f"Operator request:\n{prompt}\n\n"
        "Return concrete progress, blockers, and verification needed."
    )
    task_id = _queue_agent_task(
        title=f"Studio: {prompt[:80]}",
        agent_id=agent_id,
        prompt=task_prompt,
        project_id=f"studio-{project_id}",
    )
    run = store.get_run(run["id"])
    run.setdefault("tasks", []).append(task_id)
    run = _persist_run_status(store, run, "running")
    store.append_run_event(
        run["id"],
        "run.task_queued",
        f"Queued {agent_id}",
        {"task_id": task_id, "agent_id": agent_id},
    )
    return {
        "run": store.get_run(run["id"]),
        "context": context_pack,
        "research": research_pack,
        "decision": decision,
    }


def record_verification_evidence(
    run_id: str,
    *,
    kind: str,
    status: str,
    summary: str,
    command_or_check: str = "",
    artifact: str = "",
) -> dict[str, Any]:
    store = studio_store.StudioStore()
    run = store.get_run(run_id)
    evidence = {
        "kind": kind,
        "status": status,
        "summary": summary,
        "command_or_check": command_or_check,
        "artifact": artifact,
        "ts": studio_store.utc_now(),
    }
    run.setdefault("evidence", []).append(evidence)
    run["updated_at"] = evidence["ts"]
    store._write_json(store._run_path(run_id), run)
    store.append_run_event(
        run_id,
        "run.verification_evidence_recorded",
        summary,
        evidence,
    )
    return store.get_run(run_id)

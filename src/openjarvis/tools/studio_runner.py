from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openjarvis.tools import studio_context, studio_research, studio_store, studio_workflows


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


def _load_agent_task_index() -> dict[str, dict[str, Any]]:
    from openjarvis.tools import agent_runner

    try:
        state = json.loads(agent_runner.STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    tasks = state.get("tasks") or []
    return {str(task.get("id")): task for task in tasks if isinstance(task, dict) and task.get("id")}


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
            return text[:6000] + ("\n...[truncated]" if len(text) > 6000 else "")
    return ""


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
                result_text = "Task failed." if status == "failed" else "Task completed."
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

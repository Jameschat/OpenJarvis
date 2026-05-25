from __future__ import annotations

from typing import Any

from openjarvis.tools import studio_context, studio_store, studio_workflows


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
            "decision": decision,
        }

    agent_id = "qwen-researcher" if decision["workflow"] == "qwen_workflow" else "qwen-planner"
    task_prompt = (
        f"{context_pack.get('markdown', '')}\n\n"
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

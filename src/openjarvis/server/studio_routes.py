"""FastAPI routes for the Jarvis Studio desktop workspace."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

logger = logging.getLogger(__name__)

studio_router = APIRouter(prefix="/studio", tags=["studio"])


def _internal_error(route: str, exc: Exception) -> HTTPException:
    logger.exception("%s failed", route)
    return HTTPException(status_code=500, detail={"error": "internal error", "ref": type(exc).__name__})


@studio_router.get("/ping")
async def studio_ping() -> dict[str, Any]:
    return {"ok": True, "service": "jarvis_studio"}


@studio_router.get("/state")
async def studio_state(
    project_id: str | None = Query(default=None),
    chat_id: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        from openjarvis.cli.brain_server import _studio_state
        from openjarvis.tools.runtime_supervisor import build_runtime_supervisor_status

        state = _studio_state(project_id, chat_id)
        state["runtime_supervisor"] = build_runtime_supervisor_status(
            runtime_health=state.get("runtime_health"),
            qwen_runtime=state.get("qwen_runtime"),
        )
        return state
    except Exception as exc:
        raise _internal_error("/studio/state", exc) from exc


@studio_router.get("/runtime-health")
async def studio_runtime_health() -> dict[str, Any]:
    try:
        from openjarvis.tools.runtime_health import (
            check_runtime_health,
            mark_runtime_service_ready,
        )
        from openjarvis.tools.qwen_runtime_status import load_qwen_runtime_status
        from openjarvis.tools.runtime_supervisor import build_runtime_supervisor_status

        health = check_runtime_health()
        mark_runtime_service_ready(
            health,
            "jarvis_backend",
            detail="runtime health request succeeded",
        )
        return {
            **health,
            "runtime_supervisor": build_runtime_supervisor_status(
                runtime_health=health,
                qwen_runtime=load_qwen_runtime_status(),
            ),
        }
    except Exception as exc:
        raise _internal_error("/studio/runtime-health", exc) from exc


@studio_router.get("/projects")
async def studio_projects() -> dict[str, Any]:
    try:
        from openjarvis.tools.studio_store import StudioStore

        return {"projects": StudioStore().list_projects()}
    except Exception as exc:
        raise _internal_error("/studio/projects", exc) from exc


@studio_router.get("/chats")
async def studio_chats(project_id: str | None = Query(default=None)) -> dict[str, Any]:
    try:
        from openjarvis.tools.studio_store import StudioStore

        return {"chats": StudioStore().list_chats(project_id)}
    except Exception as exc:
        raise _internal_error("/studio/chats", exc) from exc


@studio_router.post("/chats")
async def studio_chat_create(request: Request) -> dict[str, Any]:
    try:
        from openjarvis.tools.studio_store import StudioStore

        data = await request.json()
        store = StudioStore()
        project_id = str(data.get("project_id") or "openjarvis")
        title = str(data.get("title") or "New chat")
        store.ensure_project(project_id, title=project_id)
        return {"chat": store.create_chat(project_id, title=title)}
    except Exception as exc:
        raise _internal_error("/studio/chats create", exc) from exc


@studio_router.post("/chats/{chat_id}/archive")
async def studio_chat_archive(chat_id: str) -> dict[str, Any]:
    try:
        from openjarvis.tools.studio_store import StudioStore

        return {"chat": StudioStore().archive_chat(chat_id), "action": "archive"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error": f"chat not found: {exc}"}) from exc
    except Exception as exc:
        raise _internal_error("/studio/chats archive", exc) from exc


@studio_router.post("/chats/{chat_id}/delete")
async def studio_chat_delete(chat_id: str) -> dict[str, Any]:
    try:
        from openjarvis.tools.studio_store import StudioStore

        return {"chat": StudioStore().delete_chat(chat_id), "action": "delete"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error": f"chat not found: {exc}"}) from exc
    except Exception as exc:
        raise _internal_error("/studio/chats delete", exc) from exc


@studio_router.get("/runs")
async def studio_runs(
    project_id: str | None = Query(default=None),
    chat_id: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        from openjarvis.cli.brain_server import _studio_runs_response

        return {"runs": _studio_runs_response(project_id, chat_id)}
    except Exception as exc:
        raise _internal_error("/studio/runs", exc) from exc


@studio_router.post("/runs")
async def studio_run_create(request: Request) -> dict[str, Any]:
    try:
        from openjarvis.tools.studio_runner import start_studio_run
        from openjarvis.tools.studio_store import StudioStore

        data = await request.json()
        store = StudioStore()
        project_id = str(data.get("project_id") or "openjarvis")
        prompt = str(data.get("prompt") or "").strip()
        if not prompt:
            raise HTTPException(status_code=400, detail={"error": "prompt is required"})

        chats = store.list_chats(project_id)
        chat_id = str(data.get("chat_id") or (chats[0]["id"] if chats else ""))
        branch_from_message_id = str(data.get("branch_from_message_id") or "").strip()
        if branch_from_message_id:
            if not chat_id:
                raise HTTPException(status_code=400, detail={"error": "chat_id is required for steering"})
            chat = store.branch_chat(chat_id, branch_from_message_id, prompt)
            chat_id = chat["id"]
            project_id = str(chat.get("project_id") or project_id)
        elif not chat_id:
            chat = store.create_chat(project_id, title=prompt[:80] or "New chat")
            chat_id = chat["id"]
        else:
            store.add_message(chat_id, "operator", prompt)

        selected_skills = data.get("selected_skills") if isinstance(data.get("selected_skills"), list) else []
        result = start_studio_run(
            project_id,
            chat_id,
            prompt,
            approved=bool(data.get("approved")),
            selected_skills=selected_skills,
        )
        run = result.get("run") or {}
        status = run.get("status", "queued")
        reply = result.get("reply")
        if reply or status not in {"queued", "running"}:
            store.add_message(
                chat_id,
                "jarvis",
                str(reply or f"Studio run {status}: {result.get('decision', {}).get('reason', 'workflow selected')}"),
                run_id=run.get("id"),
            )
        return result
    except HTTPException:
        raise
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error": f"chat not found: {exc}"}) from exc
    except Exception as exc:
        raise _internal_error("/studio/runs create", exc) from exc


@studio_router.post("/runs/{run_id}/cancel")
async def studio_run_cancel(run_id: str) -> dict[str, Any]:
    try:
        from openjarvis.tools.studio_runner import cancel_studio_run

        return cancel_studio_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error": f"run not found: {exc}"}) from exc
    except Exception as exc:
        raise _internal_error("/studio/runs cancel", exc) from exc


@studio_router.post("/runs/{run_id}/evidence")
async def studio_run_evidence(run_id: str, request: Request) -> dict[str, Any]:
    try:
        from openjarvis.tools.studio_runner import record_verification_evidence

        data = await request.json()
        run = record_verification_evidence(
            run_id,
            kind=str(data.get("kind") or "manual"),
            status=str(data.get("status") or "recorded"),
            summary=str(data.get("summary") or "Verification evidence recorded"),
            command_or_check=str(data.get("command_or_check") or ""),
            artifact=str(data.get("artifact") or ""),
        )
        return {"run": run}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error": f"run not found: {exc}"}) from exc
    except Exception as exc:
        raise _internal_error("/studio/runs evidence", exc) from exc


@studio_router.get("/search")
async def studio_search(q: str = Query(default="")) -> dict[str, Any]:
    try:
        from openjarvis.tools.studio_store import StudioStore

        return {"results": StudioStore().search(q)}
    except Exception as exc:
        raise _internal_error("/studio/search", exc) from exc


@studio_router.get("/plugins")
async def studio_plugins() -> dict[str, Any]:
    try:
        from openjarvis.cli.brain_server import _studio_plugins

        return {"plugins": _studio_plugins()}
    except Exception as exc:
        raise _internal_error("/studio/plugins", exc) from exc


@studio_router.get("/qwen-profile")
async def studio_qwen_profile_get() -> dict[str, Any]:
    try:
        from openjarvis.cli.brain_server import _studio_qwen_profile

        return _studio_qwen_profile()
    except Exception as exc:
        raise _internal_error("/studio/qwen-profile get", exc) from exc


def _maybe_switch_local_lane(prior: str, new: str) -> bool:
    """fast<->coder share the single local 8084 GPU lane (24GB can't co-load).
    On that transition, run the swap script (stop the active lane to free VRAM,
    load the target) in a DAEMON THREAD via subprocess.run so the HTTP request
    returns immediately while the ~1min swap runs. (Detached Popen variants
    silently failed to launch from the backend; a thread+run is reliable, and the
    swap script itself starts the WSL lane detached so it survives this process.)"""
    import os
    import subprocess
    import sys
    import threading
    from pathlib import Path

    lane_of = {"fast": "fast", "coder": "coder"}
    target = lane_of.get(new)
    if not target or target == lane_of.get(prior) or sys.platform != "win32":
        return False
    repo = Path(__file__).resolve().parents[3]
    script = repo / "scripts" / "switch-qwen-lane.ps1"
    if not script.exists():
        return False
    pwsh = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe"
    )
    if not os.path.exists(pwsh):
        pwsh = "powershell.exe"
    log_path = repo / "dist" / "switch-qwen-lane-launch.log"

    def _run() -> None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as out:
                out.write(f"\n=== launching swap -> {target} ===\n")
                out.flush()
                subprocess.run(
                    [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Target", target],
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    cwd=str(repo),
                    timeout=400,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
        except Exception as exc:  # noqa: BLE001 - log and move on
            try:
                with open(log_path, "a", encoding="utf-8") as out:
                    out.write(f"swap launch error: {exc}\n")
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()
    return True


@studio_router.post("/qwen-profile")
async def studio_qwen_profile_set(request: Request) -> dict[str, Any]:
    try:
        from openjarvis.cli.brain_server import _save_studio_qwen_profile, _studio_qwen_profile

        data = await request.json()
        profile = str(data.get("profile") or "").strip().lower()
        if profile not in {"fast", "quality", "remote", "coder"}:
            raise HTTPException(status_code=400, detail={"error": "profile must be fast, quality, remote, or coder"})
        try:
            prior = str(_studio_qwen_profile().get("active") or "")
        except Exception:
            prior = ""
        _save_studio_qwen_profile(profile)
        switching = _maybe_switch_local_lane(prior, profile)
        result = _studio_qwen_profile()
        result["switching"] = switching
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise _internal_error("/studio/qwen-profile set", exc) from exc


@studio_router.get("/preview")
async def studio_preview_get() -> dict[str, Any]:
    try:
        from openjarvis.tools.studio_store import StudioStore

        projects = StudioStore().list_projects()
        active = projects[0] if projects else {}
        repo_root = str(active.get("repo_root") or "")
        return {
            "available": bool(repo_root and (Path(repo_root) / "index.html").exists()),
            "project_id": active.get("id", ""),
            "repo_root": repo_root,
        }
    except Exception as exc:
        raise _internal_error("/studio/preview get", exc) from exc


@studio_router.post("/preview")
async def studio_preview_start(request: Request) -> dict[str, Any]:
    try:
        from openjarvis.tools.project_preview import start_project_preview
        from openjarvis.tools.studio_store import StudioStore

        data = await request.json()
        project_id = str(data.get("project_id") or "openjarvis")
        project = next(
            (item for item in StudioStore().list_projects() if item.get("id") == project_id),
            None,
        )
        if not project:
            raise HTTPException(status_code=404, detail={"error": "project not found"})
        result = start_project_preview(Path(str(project.get("repo_root") or "")))
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise _internal_error("/studio/preview", exc) from exc


@studio_router.post("/worker-update")
async def studio_worker_update() -> dict[str, Any]:
    try:
        from openjarvis.tools.worker_update import run_worker_update

        result = run_worker_update()
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise _internal_error("/studio/worker-update", exc) from exc


@studio_router.get("/automations")
async def studio_automations() -> dict[str, Any]:
    try:
        from openjarvis.tools import agent_runner

        return {"automations": agent_runner.list_scheduled()}
    except Exception as exc:
        raise _internal_error("/studio/automations", exc) from exc


@studio_router.post("/qwen-proposals/apply")
async def studio_qwen_proposal_apply(request: Request) -> dict[str, Any]:
    try:
        from openjarvis.tools import qwen_tool_bridge

        data = await request.json()
        proposal_id = str(data.get("proposal_id") or data.get("proposal_path") or "").strip()
        phrase = str(data.get("approval_phrase") or "").strip()
        if not proposal_id:
            raise HTTPException(status_code=400, detail={"error": "proposal_id is required"})
        result = qwen_tool_bridge.apply_patch_proposal(proposal_id, approval_phrase=phrase)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise _internal_error("/studio/qwen-proposals/apply", exc) from exc

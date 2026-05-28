from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from openjarvis.tools import studio_context, studio_research, studio_store, studio_workflows

STUDIO_RUN_STALE_AFTER_SECONDS = 300
STUDIO_CONTEXT_CHAR_LIMIT = int(os.environ.get("OPENJARVIS_STUDIO_CONTEXT_CHAR_LIMIT", "800000"))
BRAIN_ROOT = Path(os.environ.get("OPENJARVIS_BRAIN_ROOT", r"E:\Claude\Obsidian\Claude\Brain"))
_FILE_ACTIVITY_IGNORES = {
    "jarvis.bat",
    "uv.lock",
}
_FILE_ACTIVITY_SECRET_PARTS = (
    ".env",
    ".key",
    ".pem",
    "secret",
    "secrets",
)
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


def _is_safe_activity_path(path: str) -> bool:
    normal = path.replace("\\", "/").strip("/")
    if not normal or normal.startswith("../") or "/../" in normal:
        return False
    name = normal.rsplit("/", 1)[-1].lower()
    lower = normal.lower()
    if name in _FILE_ACTIVITY_IGNORES:
        return False
    return not any(part in lower for part in _FILE_ACTIVITY_SECRET_PARTS)


def _parse_numstat(text: str) -> list[dict[str, Any]]:
    activity: list[dict[str, Any]] = []
    for raw in text.splitlines():
        parts = raw.split("\t")
        if len(parts) < 3:
            continue
        additions_raw, deletions_raw, path = parts[0], parts[1], parts[2]
        if " => " in path:
            path = path.split(" => ", 1)[1].strip("{}")
        if not _is_safe_activity_path(path):
            continue
        try:
            additions = int(additions_raw)
            deletions = int(deletions_raw)
        except ValueError:
            additions = 0
            deletions = 0
        activity.append(
            {
                "path": path.replace("\\", "/"),
                "name": Path(path).name,
                "additions": additions,
                "deletions": deletions,
                "status": "editing",
            }
        )
    return activity


def _merge_file_activity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        path = str(row.get("path") or "")
        if not _is_safe_activity_path(path):
            continue
        target = merged.setdefault(
            path,
            {
                "path": path,
                "name": Path(path).name,
                "additions": 0,
                "deletions": 0,
                "status": row.get("status") or "editing",
            },
        )
        target["additions"] += int(row.get("additions") or 0)
        target["deletions"] += int(row.get("deletions") or 0)
    return sorted(
        merged.values(),
        key=lambda item: (int(item.get("additions") or 0) + int(item.get("deletions") or 0), str(item.get("path") or "")),
        reverse=True,
    )


def _git_file_activity(repo_root: Path) -> list[dict[str, Any]]:
    root = Path(repo_root)
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for args in (
        ["git", "-C", str(root), "diff", "--numstat", "--", "."],
        ["git", "-C", str(root), "diff", "--cached", "--numstat", "--", "."],
    ):
        try:
            completed = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0:
            rows.extend(_parse_numstat(completed.stdout))
    return _merge_file_activity(rows)


def _subtract_file_activity(
    current: list[dict[str, Any]],
    baseline: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    base = {str(row.get("path") or ""): row for row in (baseline or []) if row.get("path")}
    rows: list[dict[str, Any]] = []
    for row in current:
        path = str(row.get("path") or "")
        if not _is_safe_activity_path(path):
            continue
        base_row = base.get(path, {})
        additions = max(0, int(row.get("additions") or 0) - int(base_row.get("additions") or 0))
        deletions = max(0, int(row.get("deletions") or 0) - int(base_row.get("deletions") or 0))
        if additions == 0 and deletions == 0:
            continue
        rows.append(
            {
                "path": path,
                "name": Path(path).name,
                "additions": additions,
                "deletions": deletions,
                "status": row.get("status") or "editing",
            }
        )
    return _merge_file_activity(rows)


def _project_repo_root(project: dict[str, Any] | None = None) -> Path:
    if project and project.get("repo_root"):
        return Path(str(project["repo_root"]))
    return studio_store.DEFAULT_REPO_ROOT


def _capture_run_file_activity(run: dict[str, Any]) -> list[dict[str, Any]]:
    root = Path(str(run.get("repo_root") or studio_store.DEFAULT_REPO_ROOT))
    current = _git_file_activity(root)
    return _subtract_file_activity(current, run.get("file_activity_baseline") or [])


def _store_run_file_activity_baseline(
    store: studio_store.StudioStore,
    run: dict[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
    run["repo_root"] = str(repo_root)
    run["file_activity_baseline"] = _git_file_activity(repo_root)
    store._write_json(store._run_path(run["id"]), run)
    return store.get_run(run["id"])


def _store_run_final_file_activity(store: studio_store.StudioStore, run: dict[str, Any]) -> dict[str, Any]:
    run["file_activity_final"] = _capture_run_file_activity(run)
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


def _read_handoff_excerpt(path: str, *, limit: int = 6000) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return text[:limit]


def _ensure_context_continuation(
    store: studio_store.StudioStore,
    chat: dict[str, Any],
    handoff: dict[str, Any],
) -> dict[str, Any]:
    existing = chat.get("context_continuation")
    if isinstance(existing, dict) and existing.get("chat_id"):
        return existing
    handoff_path = str(handoff.get("path") or "")
    continuation = store.create_context_continuation_chat(
        str(chat["id"]),
        handoff_path=handoff_path,
        handoff_excerpt=_read_handoff_excerpt(handoff_path),
    )
    return {
        "chat_id": continuation["id"],
        "handoff_path": handoff_path,
        "created_at": continuation.get("created_at"),
    }


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
        if (
            pressure["status"] == "critical"
            and store is not None
            and copy.get("id")
            and isinstance(pressure.get("handoff"), dict)
        ):
            pressure["continuation"] = _ensure_context_continuation(store, copy, pressure["handoff"])
        elif isinstance(copy.get("context_continuation"), dict):
            pressure["continuation"] = copy["context_continuation"]
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
    outputs.extend(_qwen_tool_result_outputs(root, task_id, seen))
    return outputs


def _safe_artifact_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered_parts = [part.lower() for part in Path(text).parts]
    if any(secret in part for part in lowered_parts for secret in _FILE_ACTIVITY_SECRET_PARTS):
        return ""
    return text


def _qwen_tool_result_outputs(root: Path, task_id: str, seen: set[Path]) -> list[dict[str, Any]]:
    path = root / "QWEN_TOOL_RESULTS.json"
    if not path.exists() or not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, list):
        results = payload
    elif isinstance(payload, dict):
        results = payload.get("results") or []
    else:
        results = []
    outputs: list[dict[str, Any]] = []
    for result in results[:12]:
        if not isinstance(result, dict) or result.get("ok") is False:
            continue
        tool = str(result.get("tool") or "")
        if tool == "repo_patch_proposal":
            artifact_path = _safe_artifact_path(result.get("proposal_path"))
            if not artifact_path:
                continue
            changed_files = [str(item) for item in result.get("changed_files") or [] if item]
            label = changed_files[0] if changed_files else str(result.get("proposal_id") or "pending")
            item = {
                "name": f"Qwen proposal: {label}",
                "path": artifact_path,
                "kind": "proposal",
                "size": Path(artifact_path).stat().st_size if Path(artifact_path).exists() else 0,
                "task_id": task_id,
                "proposal_id": str(result.get("proposal_id") or ""),
                "changed_files": changed_files,
                "apply_requires_approval": bool(result.get("apply_requires_approval", True)),
            }
        elif tool == "browser_visual_check":
            artifact_path = _safe_artifact_path(result.get("screenshot_path"))
            if not artifact_path:
                continue
            title = str(result.get("title") or result.get("url") or "screenshot").strip()
            item = {
                "name": f"Visual check: {title}",
                "path": artifact_path,
                "kind": "screenshot",
                "size": Path(artifact_path).stat().st_size if Path(artifact_path).exists() else 0,
                "task_id": task_id,
                "url": str(result.get("url") or ""),
                "title": title,
            }
        else:
            continue
        try:
            resolved = Path(item["path"]).resolve()
        except OSError:
            resolved = Path(item["path"])
        if resolved in seen:
            continue
        seen.add(resolved)
        outputs.append(item)
    return outputs


def _read_live_task_preview(task: dict[str, Any]) -> str:
    workspace = task.get("workspace")
    if not workspace:
        return ""
    root = Path(str(workspace))
    candidates = [
        root / f"{task.get('id', '')}.stdout.log",
        root / "stdout.log",
        root / f"{task.get('id', '')}.stderr.log",
        root / "stderr.log",
        root / "QWEN_TOOL_RESULTS.json",
    ]
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            return text[-1200:]
    return ""


def _task_progress_detail(task: dict[str, Any]) -> dict[str, Any]:
    status = str(task.get("status") or "queued")
    agent_id = str(task.get("agent_id") or task.get("agent") or "agent")
    started_raw = task.get("started_at") or 0
    elapsed = 0
    try:
        started = float(started_raw)
        if started > 0 and status == "running":
            elapsed = max(0, int(time.time() - started))
    except (TypeError, ValueError):
        elapsed = 0

    if status == "running":
        summary = f"{agent_id} running for {elapsed}s" if elapsed else f"{agent_id} running"
    elif status in {"done", "completed"}:
        summary = f"{agent_id} completed"
    elif status in {"failed", "cancelled"}:
        summary = f"{agent_id} {status}"
    else:
        summary = f"{agent_id} {status}"
    return {
        "elapsed_seconds": elapsed,
        "progress_summary": summary,
        "live_preview": _read_live_task_preview(task),
    }


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
            detail.update(_task_progress_detail(task))
            task_outputs = _task_output_files(task)
            detail["outputs"] = task_outputs
            outputs.extend(task_outputs)
            task_details.append(detail)
        copy["task_details"] = task_details
        copy["progress_summary"] = next(
            (detail.get("progress_summary") for detail in task_details if detail.get("progress_summary")),
            "",
        )
        copy["outputs"] = outputs[:12]
        activity = _capture_run_file_activity(copy)
        if not activity and isinstance(copy.get("file_activity_final"), list):
            activity = copy["file_activity_final"]
        copy["file_activity"] = activity[:12]
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


def _mark_studio_run_timed_out(
    store: studio_store.StudioStore,
    run: dict[str, Any],
    task_ids: list[str],
) -> None:
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
            oldest = _iso_to_epoch(str(run.get("updated_at") or run.get("created_at") or "")) or 0
            if oldest and (time.time() - oldest) > STUDIO_RUN_STALE_AFTER_SECONDS:
                _mark_studio_run_timed_out(store, run, task_ids)
                synced += 1
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
                _mark_studio_run_timed_out(store, run, task_ids)
                synced += 1
            continue

        failed = [task for task in tasks if task and task.get("status") != "done"]
        status = "failed" if failed else "completed"
        updated = store.get_run(run["id"])
        updated["file_activity_final"] = _capture_run_file_activity(updated)
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
    run = _store_run_file_activity_baseline(store, run, _project_repo_root(project))
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

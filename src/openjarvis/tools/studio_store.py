from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STUDIO_ROOT = Path(os.environ.get("OPENJARVIS_STUDIO_ROOT", Path.home() / ".openjarvis" / "studio"))
DEFAULT_REPO_ROOT = Path(os.environ.get("OPENJARVIS_REPO_ROOT", r"E:\Claude\OpenJarvis"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(text: str, fallback: str = "item") -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", (text or "").lower()).strip("-")
    return slug[:64] or fallback


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class StudioStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or STUDIO_ROOT)
        self.projects_dir = self.root / "projects"
        self.chats_dir = self.root / "chats"
        self.runs_dir = self.root / "runs"
        self.corrupt_dir = self.root / "corrupt"
        for path in (self.projects_dir, self.chats_dir, self.runs_dir, self.corrupt_dir):
            path.mkdir(parents=True, exist_ok=True)

    def _read_json(self, path: Path, fallback: Any) -> Any:
        if not path.exists():
            return fallback
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            target = self.corrupt_dir / f"{path.stem}-{uuid.uuid4().hex[:8]}.json"
            shutil.move(str(path), str(target))
            return fallback

    def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    def _project_path(self, project_id: str) -> Path:
        return self.projects_dir / f"{slugify(project_id)}.json"

    def _chat_path(self, chat_id: str) -> Path:
        return self.chats_dir / f"{slugify(chat_id)}.json"

    def _run_path(self, run_id: str) -> Path:
        return self.runs_dir / f"{slugify(run_id)}.json"

    def ensure_project(
        self,
        project_id: str = "openjarvis",
        *,
        title: str = "OpenJarvis",
        repo_root: str | None = None,
        vault_project: str | None = None,
    ) -> dict[str, Any]:
        path = self._project_path(project_id)
        project = self._read_json(path, {})
        now = utc_now()
        if not project:
            project = {
                "id": project_id,
                "title": title,
                "created_at": now,
                "updated_at": now,
                "repo_root": repo_root or str(DEFAULT_REPO_ROOT),
                "vault_project": vault_project or title,
                "status": "active",
            }
        else:
            project["updated_at"] = now
        self._write_json(path, project)
        return project

    def list_projects(self) -> list[dict[str, Any]]:
        projects = [self._read_json(path, {}) for path in self.projects_dir.glob("*.json")]
        projects = [p for p in projects if p.get("id")]
        if not projects:
            projects = [self.ensure_project()]
        return sorted(projects, key=lambda p: p.get("updated_at", ""), reverse=True)

    def create_chat(self, project_id: str, *, title: str) -> dict[str, Any]:
        now = utc_now()
        chat = {
            "id": new_id("chat"),
            "project_id": project_id,
            "title": title[:120] or "New chat",
            "created_at": now,
            "updated_at": now,
            "messages": [],
        }
        self._write_json(self._chat_path(chat["id"]), chat)
        return chat

    def get_chat(self, chat_id: str) -> dict[str, Any]:
        chat = self._read_json(self._chat_path(chat_id), {})
        if not chat:
            raise KeyError(chat_id)
        return chat

    def list_chats(self, project_id: str | None = None) -> list[dict[str, Any]]:
        chats = [self._read_json(path, {}) for path in self.chats_dir.glob("*.json")]
        chats = [c for c in chats if c.get("id") and (project_id is None or c.get("project_id") == project_id)]
        return sorted(chats, key=lambda c: c.get("updated_at", ""), reverse=True)

    def add_message(self, chat_id: str, role: str, content: str, *, run_id: str | None = None) -> dict[str, Any]:
        chat = self.get_chat(chat_id)
        message = {
            "id": new_id("msg"),
            "chat_id": chat_id,
            "role": role,
            "content": content,
            "created_at": utc_now(),
            "run_id": run_id,
        }
        chat.setdefault("messages", []).append(message)
        chat["updated_at"] = message["created_at"]
        self._write_json(self._chat_path(chat_id), chat)
        return message

    def create_run(self, project_id: str, chat_id: str, prompt: str, *, workflow: str) -> dict[str, Any]:
        now = utc_now()
        run = {
            "id": new_id("run"),
            "project_id": project_id,
            "chat_id": chat_id,
            "prompt": prompt,
            "workflow": workflow,
            "status": "queued",
            "model": "qwen3.6-27b-local",
            "created_at": now,
            "updated_at": now,
            "tasks": [],
            "events": [],
            "evidence": [],
            "memory_note": None,
        }
        self._write_json(self._run_path(run["id"]), run)
        return run

    def get_run(self, run_id: str) -> dict[str, Any]:
        run = self._read_json(self._run_path(run_id), {})
        if not run:
            raise KeyError(run_id)
        return run

    def append_run_event(
        self,
        run_id: str,
        event_type: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run = self.get_run(run_id)
        event = {"ts": utc_now(), "type": event_type, "message": message, "data": data or {}}
        run.setdefault("events", []).append(event)
        run["updated_at"] = event["ts"]
        self._write_json(self._run_path(run_id), run)
        return event

    def list_runs(self, project_id: str | None = None, chat_id: str | None = None) -> list[dict[str, Any]]:
        runs = [self._read_json(path, {}) for path in self.runs_dir.glob("*.json")]
        runs = [r for r in runs if r.get("id")]
        if project_id is not None:
            runs = [r for r in runs if r.get("project_id") == project_id]
        if chat_id is not None:
            runs = [r for r in runs if r.get("chat_id") == chat_id]
        return sorted(runs, key=lambda r: r.get("updated_at", ""), reverse=True)

    def initial_state(self) -> dict[str, Any]:
        projects = self.list_projects()
        active_project = projects[0]["id"]
        chats = self.list_chats(active_project)
        return {"projects": projects, "chats": chats, "runs": self.list_runs(active_project)}

    def search(self, query: str, limit: int = 25) -> list[dict[str, Any]]:
        needle = (query or "").lower().strip()
        if not needle:
            return []
        results: list[dict[str, Any]] = []
        for project in self.list_projects():
            if needle in json.dumps(project).lower():
                results.append({"type": "project", "project_id": project["id"], "title": project["title"]})
        for chat in self.list_chats():
            if needle in chat.get("title", "").lower():
                results.append(
                    {
                        "type": "chat",
                        "chat_id": chat["id"],
                        "project_id": chat["project_id"],
                        "title": chat["title"],
                    }
                )
            for message in chat.get("messages", []):
                if needle in message.get("content", "").lower():
                    results.append(
                        {
                            "type": "message",
                            "chat_id": chat["id"],
                            "message_id": message["id"],
                            "title": chat["title"],
                            "snippet": message["content"][:240],
                        }
                    )
        for run in self.list_runs():
            if needle in run.get("prompt", "").lower():
                results.append(
                    {
                        "type": "run",
                        "run_id": run["id"],
                        "project_id": run["project_id"],
                        "title": run["prompt"][:120],
                    }
                )
            for event in run.get("events", []):
                if needle in event.get("message", "").lower():
                    results.append(
                        {
                            "type": "run_event",
                            "run_id": run["id"],
                            "project_id": run["project_id"],
                            "title": event["message"][:120],
                        }
                    )
        return results[:limit]

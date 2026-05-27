from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any


_REQUEST_PATTERNS = [
    re.compile(
        r"```qwen_tool_requests\s*(?P<payload>\{.*?\})\s*```",
        re.DOTALL | re.IGNORECASE,
    ),
    re.compile(
        r"<qwen_tool_requests>\s*(?P<payload>\{.*?\})\s*</qwen_tool_requests>",
        re.DOTALL | re.IGNORECASE,
    ),
]

_SUPERPOWER_SKILLS = {
    "brainstorming": "Explore requirements and present a design before implementation.",
    "writing-plans": "Create a detailed implementation plan with exact files, tests, commands, and commits.",
    "systematic-debugging": "Debug by reproducing, isolating, hypothesising, fixing, and verifying.",
    "test-driven-development": "Write a failing test first, implement the smallest fix, then refactor.",
    "verification-before-completion": "Run concrete verification before claiming work is done.",
    "requesting-code-review": "Ask for review focused on defects, risks, and missing tests.",
    "receiving-code-review": "Evaluate review feedback rigorously before changing code.",
    "subagent-driven-development": "Break a plan into isolated tasks with review between tasks.",
    "finishing-a-development-branch": "Decide how to integrate completed verified work.",
}

_BLOCKED_TOOLS = {
    "shell",
    "powershell",
    "cmd",
    "python",
    "pip_install",
    "npm_install",
    "repo_edit",
    "apply_patch",
    "git_commit",
    "browser",
    "codex_plugin",
    "claude_skill",
}


def tool_manifest() -> str:
    """Return the safe tool contract injected into local Qwen prompts."""
    safe_tools = [
        {
            "tool": "recall_vault",
            "tier": "read",
            "args": {"query": "string", "limit": "1..8"},
            "description": "Search the Obsidian Brain vault.",
        },
        {
            "tool": "mem_search",
            "tier": "read",
            "args": {"query": "string", "limit": "1..8", "project": "optional string"},
            "description": "Search AgentMemory episodic memory.",
        },
        {
            "tool": "codegraph_status",
            "tier": "read",
            "args": {},
            "description": "Inspect whether CodeGraph is installed/indexed for OpenJarvis.",
        },
        {
            "tool": "web_search",
            "tier": "research",
            "args": {"query": "string", "limit": "1..5"},
            "description": "Search the web and cache leads into the vault.",
        },
        {
            "tool": "github_search",
            "tier": "research",
            "args": {"query": "string", "limit": "1..5"},
            "description": "Search GitHub repositories and cache leads into the vault.",
        },
        {
            "tool": "superpower_workflow",
            "tier": "procedure",
            "args": {"name": sorted(_SUPERPOWER_SKILLS)},
            "description": "Load a Superpowers workflow summary as operating guidance.",
        },
        {
            "tool": "skill_guidance",
            "tier": "procedure",
            "args": {"name": "installed Jarvis skill name, e.g. ui-ux-pro-max"},
            "description": "Load an installed Jarvis skill's operating guidance for the current task.",
        },
        {
            "tool": "request_escalation",
            "tier": "approval",
            "args": {"capability": "string", "reason": "string", "risk": "low|medium|high"},
            "description": "Request Codex/Claude or operator approval for blocked execution.",
        },
    ]
    return (
        "QWEN SAFE TOOL BRIDGE\n"
        "=====================\n"
        "You may request safe tools by including exactly one fenced block named "
        "`qwen_tool_requests` containing JSON:\n"
        "{\"requests\":[{\"id\":\"r1\",\"tool\":\"recall_vault\",\"args\":{\"query\":\"...\"}}]}\n\n"
        "Jarvis will execute read/research/procedure tools only. Shell commands, installs, "
        "repo edits, browser control, Codex plugins, and Claude skills are blocked unless "
        "you request escalation. Available safe tools:\n"
        f"{json.dumps(safe_tools, indent=2)}"
    )


def parse_tool_requests(content: str) -> list[dict[str, Any]]:
    match = None
    for pattern in _REQUEST_PATTERNS:
        match = pattern.search(content or "")
        if match:
            break
    if not match:
        return []
    try:
        payload = json.loads(match.group("payload"))
    except json.JSONDecodeError:
        return [
            {
                "id": "parse-error",
                "tool": "invalid",
                "args": {},
                "error": "qwen_tool_requests block was not valid JSON",
            }
        ]
    requests = payload.get("requests")
    if not isinstance(requests, list):
        return [
            {
                "id": "parse-error",
                "tool": "invalid",
                "args": {},
                "error": "qwen_tool_requests.requests must be a list",
            }
        ]
    out: list[dict[str, Any]] = []
    for index, item in enumerate(requests[:5]):
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "id": str(item.get("id") or f"r{index + 1}"),
                "tool": str(item.get("tool") or "").strip(),
                "args": item.get("args") if isinstance(item.get("args"), dict) else {},
            }
        )
    return out


def execute_tool_requests(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_execute_one(request) for request in requests[:5]]


def format_tool_results(results: list[dict[str, Any]]) -> str:
    return (
        "QWEN TOOL RESULTS\n"
        "=================\n"
        "Use these results to produce the final answer. Do not include another "
        "`qwen_tool_requests` block unless you are explicitly asking for escalation.\n\n"
        f"{json.dumps({'results': results}, indent=2)}"
    )


def _execute_one(request: dict[str, Any]) -> dict[str, Any]:
    request_id = str(request.get("id") or "request")
    tool = str(request.get("tool") or "").strip()
    args = request.get("args") if isinstance(request.get("args"), dict) else {}
    if tool in _BLOCKED_TOOLS:
        return {
            "id": request_id,
            "tool": tool,
            "ok": False,
            "blocked": True,
            "escalation_required": True,
            "reason": f"{tool} is not available to Qwen directly in safe-bridge mode.",
        }
    try:
        if tool == "recall_vault":
            return _recall_vault(request_id, args)
        if tool == "mem_search":
            return _mem_search(request_id, args)
        if tool == "codegraph_status":
            return _codegraph_status(request_id)
        if tool == "web_search":
            return _tool_use_call(request_id, tool, args, {"query", "limit"})
        if tool == "github_search":
            return _tool_use_call(request_id, tool, args, {"query", "limit"})
        if tool == "superpower_workflow":
            return _superpower_workflow(request_id, args)
        if tool == "skill_guidance":
            return _skill_guidance(request_id, args)
        if tool == "request_escalation":
            return {
                "id": request_id,
                "tool": tool,
                "ok": True,
                "escalation_required": True,
                "capability": str(args.get("capability") or ""),
                "reason": str(args.get("reason") or ""),
                "risk": str(args.get("risk") or "medium"),
            }
        return {"id": request_id, "tool": tool, "ok": False, "error": f"unknown safe tool: {tool}"}
    except Exception as exc:
        return {"id": request_id, "tool": tool, "ok": False, "error": str(exc)}


def _recall_vault(request_id: str, args: dict[str, Any]) -> dict[str, Any]:
    from openjarvis.tools import obsidian_brain

    query = str(args.get("query") or "").strip()
    limit = max(1, min(int(args.get("limit") or 5), 8))
    hits = []
    for path, snippet in obsidian_brain.recall(query, limit=limit):
        try:
            rel = path.relative_to(obsidian_brain.BRAIN_ROOT).as_posix()
        except Exception:
            rel = path.name
        hits.append({"path": rel, "snippet": snippet[:700]})
    return {"id": request_id, "tool": "recall_vault", "ok": True, "hits": hits}


def _mem_search(request_id: str, args: dict[str, Any]) -> dict[str, Any]:
    from openjarvis.tools.agentmemory_client import search

    query = str(args.get("query") or "").strip()
    limit = max(1, min(int(args.get("limit") or 5), 8))
    project = str(args.get("project") or "").strip() or None
    hits = search(query, limit=limit, project=project)
    return {
        "id": request_id,
        "tool": "mem_search",
        "ok": True,
        "hits": [
            {
                "snippet": hit.snippet,
                "score": hit.score,
                "session_id": hit.session_id,
                "tier": hit.tier,
            }
            for hit in hits
        ],
    }


def _codegraph_status(request_id: str) -> dict[str, Any]:
    repo_root = Path(r"E:\Claude\OpenJarvis")
    db = repo_root / ".codegraph" / "codegraph.db"
    mcp = repo_root / ".mcp.json"
    return {
        "id": request_id,
        "tool": "codegraph_status",
        "ok": True,
        "installed": Path.home().joinpath(".openjarvis", "tools", "codegraph-0.8.0").exists(),
        "indexed": db.exists(),
        "index_size_mb": round(db.stat().st_size / 1024 / 1024, 2) if db.exists() else 0,
        "mcp_configured": "codegraph" in mcp.read_text(encoding="utf-8", errors="replace") if mcp.exists() else False,
    }


def _tool_use_call(
    request_id: str,
    tool: str,
    args: dict[str, Any],
    allowed_args: set[str],
) -> dict[str, Any]:
    from openjarvis.cli import tool_use

    dispatch = getattr(tool_use, "_TOOL_DISPATCH")
    clean_args = {key: value for key, value in args.items() if key in allowed_args}
    raw = dispatch[tool](**clean_args)
    try:
        data = json.loads(raw)
    except Exception:
        data = {"text": raw}
    return {"id": request_id, "tool": tool, "ok": True, "result": data}


def _superpower_workflow(request_id: str, args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("name") or "").strip()
    if name not in _SUPERPOWER_SKILLS:
        return {
            "id": request_id,
            "tool": "superpower_workflow",
            "ok": False,
            "valid": sorted(_SUPERPOWER_SKILLS),
        }
    skill_path = (
        Path.home()
        / ".codex"
        / "plugins"
        / "cache"
        / "local"
        / "superpowers"
        / "5.0.7"
        / "skills"
        / name
        / "SKILL.md"
    )
    excerpt = ""
    if skill_path.exists():
        text = skill_path.read_text(encoding="utf-8", errors="replace")
        excerpt = text[:2200]
    return {
        "id": request_id,
        "tool": "superpower_workflow",
        "ok": True,
        "name": name,
        "summary": _SUPERPOWER_SKILLS[name],
        "excerpt": excerpt,
    }


def _skills_root() -> Path:
    override = os.environ.get("OPENJARVIS_SKILLS_DIR")
    if override:
        return Path(override)
    return Path.home() / ".openjarvis" / "skills"


def _installed_skill_names(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(path.name for path in root.iterdir() if path.is_dir())


def _skill_guidance(request_id: str, args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("name") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        return {
            "id": request_id,
            "tool": "skill_guidance",
            "ok": False,
            "error": "invalid skill name",
        }

    root = _skills_root()
    skill_dir = (root / name).resolve()
    try:
        skill_dir.relative_to(root.resolve())
    except ValueError:
        return {
            "id": request_id,
            "tool": "skill_guidance",
            "ok": False,
            "error": "invalid skill path",
        }
    if not skill_dir.is_dir():
        return {
            "id": request_id,
            "tool": "skill_guidance",
            "ok": False,
            "error": "skill not found",
            "installed": _installed_skill_names(root),
        }

    metadata_path = skill_dir / "skill.toml"
    metadata_name = "skill.toml"
    if not metadata_path.exists():
        metadata_path = skill_dir / f"{name}.toml"
        metadata_name = f"{name}.toml"

    description = ""
    tags: list[str] = []
    if metadata_path.exists():
        data = tomllib.loads(metadata_path.read_text(encoding="utf-8", errors="replace"))
        skill_meta = data.get("skill") if isinstance(data.get("skill"), dict) else {}
        description = str(skill_meta.get("description") or "")
        raw_tags = skill_meta.get("tags")
        if isinstance(raw_tags, list):
            tags = [str(tag) for tag in raw_tags]

    skill_md = skill_dir / "SKILL.md"
    excerpt = ""
    if skill_md.exists():
        excerpt = skill_md.read_text(encoding="utf-8", errors="replace")[:6000]

    return {
        "id": request_id,
        "tool": "skill_guidance",
        "ok": True,
        "name": name,
        "description": description,
        "tags": tags,
        "metadata_path": metadata_name if metadata_path.exists() else "",
        "excerpt": excerpt,
    }

from __future__ import annotations

import json
import os
import re
import time
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


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
            "tool": "repo_read",
            "tier": "read",
            "args": {"path": "repo-relative path", "max_chars": "optional 1000..20000"},
            "description": "Safely read/search non-secret project files from the OpenJarvis repo.",
        },
        {
            "tool": "repo_search",
            "tier": "read",
            "args": {"query": "string", "glob": "optional file glob", "limit": "1..20"},
            "description": "Safely read/search non-secret project files from the OpenJarvis repo.",
        },
        {
            "tool": "repo_patch_proposal",
            "tier": "approval",
            "args": {
                "rationale": "string",
                "files": [{"path": "repo-relative path", "content": "full proposed file content"}],
            },
            "description": "Save a validated edit proposal for Codex/operator approval; does not apply changes.",
        },
        {
            "tool": "browser_visual_check",
            "tier": "verify",
            "args": {"url": "local Jarvis page URL", "full_page": "optional boolean"},
            "description": "Open a local Jarvis page and capture a screenshot/text excerpt for visual QA.",
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
        "direct repo edits, browser control, Codex plugins, and Claude skills are blocked unless "
        "you request escalation. repo_patch_proposal only saves a proposal; it does not apply "
        "changes. Available safe tools:\n"
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
        if tool == "repo_read":
            return _repo_read(request_id, args)
        if tool == "repo_search":
            return _repo_search(request_id, args)
        if tool == "repo_patch_proposal":
            return _repo_patch_proposal(request_id, args)
        if tool == "browser_visual_check":
            return _browser_visual_check(request_id, args)
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


_SECRET_PATH_RE = re.compile(
    r"(^|/)(jarvis\.bat|\.env(\..*)?|.*secret.*|.*token.*|.*key.*|.*\.pem|.*\.pfx|.*\.p12)$",
    re.IGNORECASE,
)


def _repo_root() -> Path:
    override = os.environ.get("OPENJARVIS_QWEN_REPO_ROOT")
    if override:
        return Path(override).resolve()
    return Path(r"E:\Claude\OpenJarvis").resolve()


def _resolve_repo_path(raw_path: str) -> tuple[Path | None, str, dict[str, Any] | None]:
    rel = str(raw_path or "").replace("\\", "/").strip()
    if not rel or rel.startswith("/") or ":" in rel or ".." in Path(rel).parts:
        return None, rel, {"ok": False, "error": "path escapes repo root"}
    if _SECRET_PATH_RE.search(rel):
        return None, rel, {
            "ok": False,
            "blocked": True,
            "reason": "secret-like paths are not exposed to Qwen safe repo inspection",
        }
    root = _repo_root()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None, rel, {"ok": False, "error": "path escapes repo root"}
    return target, rel, None


def _repo_read(request_id: str, args: dict[str, Any]) -> dict[str, Any]:
    target, rel, error = _resolve_repo_path(str(args.get("path") or ""))
    if error:
        return {"id": request_id, "tool": "repo_read", **error}
    assert target is not None
    if not target.is_file():
        return {
            "id": request_id,
            "tool": "repo_read",
            "ok": False,
            "error": "file not found",
            "path": rel,
        }
    if target.stat().st_size > 1_000_000:
        return {
            "id": request_id,
            "tool": "repo_read",
            "ok": False,
            "error": "file too large",
            "path": rel,
        }
    max_chars = max(1000, min(int(args.get("max_chars") or 12000), 20000))
    content = target.read_text(encoding="utf-8", errors="replace")[:max_chars]
    return {
        "id": request_id,
        "tool": "repo_read",
        "ok": True,
        "path": rel,
        "truncated": len(content) == max_chars,
        "content": content,
    }


def _repo_search(request_id: str, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"id": request_id, "tool": "repo_search", "ok": False, "error": "query required"}
    glob = str(args.get("glob") or "*").strip() or "*"
    limit = max(1, min(int(args.get("limit") or 10), 20))
    root = _repo_root()
    matches: list[dict[str, Any]] = []
    for path in root.rglob(glob):
        if len(matches) >= limit:
            break
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if _SECRET_PATH_RE.search(rel) or ".git/" in rel or "__pycache__/" in rel:
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            for lineno, line in enumerate(lines, start=1):
                if query.lower() in line.lower():
                    matches.append({"path": rel, "line_number": lineno, "line": line[:500]})
                    if len(matches) >= limit:
                        break
        except OSError:
            continue
    return {
        "id": request_id,
        "tool": "repo_search",
        "ok": True,
        "query": query,
        "matches": matches,
    }


def _patch_proposal_dir() -> Path:
    override = os.environ.get("OPENJARVIS_QWEN_PATCH_PROPOSAL_DIR")
    if override:
        return Path(override)
    return Path.home() / ".openjarvis" / "qwen_patch_proposals"


def _repo_patch_proposal(request_id: str, args: dict[str, Any]) -> dict[str, Any]:
    raw_files = args.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        return {
            "id": request_id,
            "tool": "repo_patch_proposal",
            "ok": False,
            "error": "files must be a non-empty list",
        }
    if len(raw_files) > 12:
        return {
            "id": request_id,
            "tool": "repo_patch_proposal",
            "ok": False,
            "error": "too many files in one proposal",
        }

    validated: list[dict[str, Any]] = []
    total_bytes = 0
    for item in raw_files:
        if not isinstance(item, dict):
            continue
        target, rel, error = _resolve_repo_path(str(item.get("path") or ""))
        if error:
            return {"id": request_id, "tool": "repo_patch_proposal", **error}
        content = item.get("content")
        if not isinstance(content, str):
            return {
                "id": request_id,
                "tool": "repo_patch_proposal",
                "ok": False,
                "error": f"content required for {rel}",
            }
        encoded_len = len(content.encode("utf-8"))
        if encoded_len > 300_000:
            return {
                "id": request_id,
                "tool": "repo_patch_proposal",
                "ok": False,
                "error": f"proposed content too large for {rel}",
            }
        total_bytes += encoded_len
        if total_bytes > 750_000:
            return {
                "id": request_id,
                "tool": "repo_patch_proposal",
                "ok": False,
                "error": "proposal too large",
            }
        assert target is not None
        current = ""
        exists = target.exists()
        if exists and target.is_file() and target.stat().st_size <= 300_000:
            current = target.read_text(encoding="utf-8", errors="replace")
        validated.append(
            {
                "path": rel,
                "content": content,
                "exists": exists,
                "current_preview": current[:1200],
                "proposed_bytes": encoded_len,
            }
        )
    if not validated:
        return {
            "id": request_id,
            "tool": "repo_patch_proposal",
            "ok": False,
            "error": "no valid files in proposal",
        }

    proposal_id = f"qwen-proposal-{int(time.time() * 1000)}"
    out_dir = _patch_proposal_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    proposal_path = out_dir / f"{proposal_id}.json"
    payload = {
        "id": proposal_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repo_root": str(_repo_root()),
        "rationale": str(args.get("rationale") or "").strip(),
        "apply_requires_approval": True,
        "files": validated,
    }
    proposal_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {
        "id": request_id,
        "tool": "repo_patch_proposal",
        "ok": True,
        "proposal_id": proposal_id,
        "proposal_path": str(proposal_path),
        "changed_files": [file["path"] for file in validated],
        "apply_requires_approval": True,
        "message": "Edit proposal saved. Codex/operator approval is required before applying it.",
    }


def _visual_check_dir() -> Path:
    override = os.environ.get("OPENJARVIS_QWEN_VISUAL_CHECK_DIR")
    if override:
        return Path(override)
    return Path.home() / ".openjarvis" / "qwen_visual_checks"


def _is_local_jarvis_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def _browser_visual_check(request_id: str, args: dict[str, Any]) -> dict[str, Any]:
    url = str(args.get("url") or "").strip()
    if not _is_local_jarvis_url(url):
        return {
            "id": request_id,
            "tool": "browser_visual_check",
            "ok": False,
            "blocked": True,
            "reason": "browser_visual_check is limited to local Jarvis pages",
        }
    out_dir = _visual_check_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = out_dir / f"qwen-visual-{int(time.time() * 1000)}.png"
    result = _browser_visual_check_impl(
        url,
        screenshot_path,
        bool(args.get("full_page", False)),
    )
    return {"id": request_id, "tool": "browser_visual_check", **result}


def _browser_visual_check_impl(url: str, screenshot_path: Path, full_page: bool) -> dict[str, Any]:
    import openjarvis.tools.browser  # noqa: F401
    from openjarvis.core.registry import ToolRegistry

    nav_cls = ToolRegistry.get("browser_navigate")
    shot_cls = ToolRegistry.get("browser_screenshot")
    nav_tool = nav_cls() if isinstance(nav_cls, type) else nav_cls
    shot_tool = shot_cls() if isinstance(shot_cls, type) else shot_cls
    nav = nav_tool.execute(url=url, wait_for="domcontentloaded")
    shot = shot_tool.execute(path=str(screenshot_path), full_page=full_page)
    return {
        "ok": bool(getattr(nav, "success", False) and getattr(shot, "success", False)),
        "url": url,
        "title": (getattr(nav, "metadata", {}) or {}).get("title", ""),
        "text_excerpt": (getattr(nav, "content", "") or "")[:1200],
        "screenshot_path": str(screenshot_path),
        "navigation": getattr(nav, "content", "")[:300],
        "screenshot": getattr(shot, "content", "")[:300],
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

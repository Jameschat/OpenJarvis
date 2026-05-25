from __future__ import annotations

import json
from typing import Any


RESEARCH_TERMS = {
    "latest",
    "current",
    "today",
    "recent",
    "internet",
    "web",
    "browse",
    "search",
    "github",
    "repo",
    "tool",
    "plugin",
    "library",
    "api",
    "docs",
    "pricing",
    "release",
    "version",
}


def _web_search(query: str, limit: int = 5) -> str:
    from openjarvis.cli.tool_use import _tool_web_search

    return _tool_web_search(query, limit=limit)


def _github_search(query: str, limit: int = 5) -> str:
    from openjarvis.cli.tool_use import _tool_github_search

    return _tool_github_search(query, limit=limit)


def should_prefetch_research(prompt: str) -> bool:
    lower = (prompt or "").lower()
    return any(term in lower for term in RESEARCH_TERMS)


def _parse_json(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {"error": "tool returned non-object JSON"}
    except Exception as exc:
        return {"error": str(exc), "raw": (raw or "")[:500]}


def prefetch_research(prompt: str, limit: int = 4) -> dict[str, Any]:
    query = (prompt or "").strip()
    if not query:
        return {"ok": False, "reason": "empty prompt", "markdown": ""}

    web = _parse_json(_web_search(query, limit=limit))
    github = _parse_json(_github_search(query, limit=limit)) if _looks_code_or_tool_query(query) else {"repos": []}
    markdown = render_research_markdown(query, web, github)
    return {
        "ok": bool((web.get("hits") or github.get("repos"))),
        "query": query,
        "web": web,
        "github": github,
        "markdown": markdown,
    }


def _looks_code_or_tool_query(query: str) -> bool:
    lower = query.lower()
    return any(term in lower for term in ("github", "repo", "tool", "plugin", "library", "code", "mcp", "agent"))


def render_research_markdown(query: str, web: dict[str, Any], github: dict[str, Any]) -> str:
    lines = [
        "== WEB RESEARCH PREFETCH ==",
        "Use these fetched results as source leads, not unquestioned truth. Prefer primary sources and cite URLs in final answers.",
        f"Query: {query}",
        "",
        "## Web search",
    ]
    hits = web.get("hits") or []
    if not hits:
        lines.append(f"- No web hits. Error: {web.get('error') or 'none'}")
    for hit in hits[:6]:
        lines.append(
            f"- {hit.get('title') or 'Untitled'} | {hit.get('url') or ''} | {hit.get('snippet') or ''}"
        )

    lines += ["", "## GitHub search"]
    repos = github.get("repos") or []
    if not repos and github.get("error"):
        lines.append(f"- GitHub search error: {github.get('error')}")
    elif not repos:
        lines.append("- No GitHub repo search was needed or no repos were found.")
    for repo in repos[:6]:
        lines.append(
            f"- {repo.get('name') or 'repo'} | {repo.get('url') or ''} | "
            f"stars={repo.get('stars', 0)} | {repo.get('description') or ''}"
        )
    lines.append("== END WEB RESEARCH PREFETCH ==")
    return "\n".join(lines)


__all__ = ["should_prefetch_research", "prefetch_research", "render_research_markdown"]

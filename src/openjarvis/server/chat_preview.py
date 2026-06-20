"""Deterministic "show me the <project> site" handler for chat.

When the user asks to *see/open/preview/review* one of their projects, we don't
trust the local model to orchestrate preview_start + screenshot (it tends to
wander into file reads and burn its turn budget). Instead the chat route detects
the intent here, starts a local static preview server for the project folder, and
streams back a message containing a ```preview <url>``` fence that the chat UI
renders as a live, scrollable <iframe> of the running site.

Guarded + narrow: only fires when an intent verb is present AND the text resolves
to a real previewable project (a top-level workspace folder with index.html).
Anything else falls through to the normal agent.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

# Verbs that signal "put the site in front of me".
_INTENT_RE = re.compile(
    r"\b(show|see|view|open|preview|display|pull up|bring up|load|look at|review)\b",
    re.IGNORECASE,
)
# Generic words that are NOT distinctive enough to match a project on their own.
_STOPWORD_TOKENS = {
    "hotel", "site", "website", "web", "app", "page", "project", "the", "my",
    "bar", "sports", "lounge", "limited", "ltd", "co", "home", "test", "new",
}


def _workspace() -> Path:
    try:
        from openjarvis.core.config import load_config

        ws = (load_config().agent.workspace_dir or "").strip()
        if ws:
            return Path(ws)
    except Exception:
        pass
    return Path(os.environ.get("OPENJARVIS_WORKSPACE", "E:/Claude"))


def _previewable_projects() -> list[Path]:
    """Top-level workspace folders that have an index.html (i.e. can be served)."""
    root = _workspace()
    out: list[Path] = []
    try:
        for p in root.iterdir():
            if p.is_dir() and not p.name.startswith((".", "_")) and (p / "index.html").is_file():
                out.append(p)
    except Exception:
        pass
    return out


def _slug_tokens(name: str) -> list[str]:
    """Distinctive lowercase tokens for a project slug (drops generic words)."""
    parts = re.split(r"[-_\s]+", name.lower())
    toks = [t for t in parts if len(t) >= 4 and t not in _STOPWORD_TOKENS]
    # the joined slug + de-hyphenated form are also strong signals
    toks.append(name.lower())
    toks.append(name.lower().replace("-", "").replace("_", ""))
    return toks


def _resolve_project(text: str) -> Optional[Path]:
    low = text.lower()
    best: Optional[Path] = None
    best_score = 0
    for proj in _previewable_projects():
        score = 0
        for tok in _slug_tokens(proj.name):
            if tok and tok in low:
                score = max(score, len(tok))  # longer match = more specific
        if score > best_score:
            best, best_score = proj, score
    return best


def _project_title(project: Path) -> str:
    """Prefer the site's <title>, else a prettified slug."""
    try:
        html = (project / "index.html").read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if m:
            title = re.split(r"[—|·\-–]", m.group(1).strip())[0].strip()
            if title:
                return title
    except Exception:
        pass
    return project.name.replace("-", " ").replace("_", " ").title()


def build_preview_reply(text: str) -> Optional[dict]:
    """If the message is a 'show me <project>' request that resolves to a real
    previewable project, start its preview server and return a dict with the
    chat ``content`` (carrying a ```preview <url>``` fence) + metadata. Else None."""
    if not text or not _INTENT_RE.search(text):
        return None
    project = _resolve_project(text)
    if project is None:
        return None
    try:
        from openjarvis.tools.project_preview import start_project_preview

        res = start_project_preview(project)
    except Exception as exc:
        return {
            "ok": False,
            "content": f"I found **{project.name}** but couldn't start a preview: {exc}",
        }
    if not res.get("ok"):
        return {
            "ok": False,
            "content": f"I found **{project.name}** but couldn't preview it: {res.get('error', 'unknown error')}",
        }

    url = res["url"]
    title = _project_title(project)
    content = (
        f"Here's **{title}** — live preview below (scroll inside it to review the "
        f"whole site). Open in a browser: [{url}]({url})\n\n"
        f"```preview\n{url}\n```\n\n"
        f"Tell me what you'd like changed and I'll edit the source."
    )
    return {"ok": True, "content": content, "url": url, "title": title, "project": project.name}


__all__ = ["build_preview_reply"]

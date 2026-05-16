"""One-shot backfill: pulls existing Claude Code sessions + project folders
into the Obsidian vault so ChatGPT (via the Custom GPT) and Jarvis (via
recall) have rich historical context to draw on.

Two scans:

1. ``backup_claude_sessions()``
   Reads every JSONL transcript under ``~/.claude/projects/`` (one per
   Claude Code session), summarises it, and writes a markdown note in
   ``Brain/Sessions/``. Skips sessions already backed up.

2. ``backup_project_folders(parent)``
   Walks ``parent``'s immediate subfolders, treats each as a project, and
   writes a markdown note in ``Brain/Projects/``. The note links to the
   folder's README.md if present and lists key files / size / mtime.

Run via:

    uv run python -m openjarvis.tools.vault_backup

Idempotent — re-running merges new sessions/projects without duplicating.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CLAUDE_PROJECTS_DIR = Path(os.environ.get("CLAUDE_PROJECTS_DIR",
                                          str(Path.home() / ".claude" / "projects")))


def _slugify(text: str, max_len: int = 60) -> str:
    s = re.sub(r"[^a-zA-Z0-9 \-_]+", "", text or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s[:max_len].rstrip(" -_") or "session"


def _decode_project_hash(name: str) -> str:
    """Claude stores projects under a flattened path:
       'E--Claude--OpenJarvis' -> 'E:/Claude/OpenJarvis'
       Best effort — works for the common Windows / Unix layouts."""
    if not name:
        return ""
    # Replace double-dash separators back into path separators
    parts = name.replace("\\", "/").split("--")
    if len(parts) > 1 and len(parts[0]) == 1:  # drive letter like 'E'
        head = parts[0] + ":"
        return os.path.normpath(os.path.join(head, *parts[1:]))
    return os.path.normpath("/" + "/".join(parts))


# ---------------------------------------------------------------------------
# Session JSONL parser
# ---------------------------------------------------------------------------


def _summarise_session_jsonl(path: Path) -> Optional[Dict]:
    """Parse one Claude Code session JSONL into a summary dict."""
    if path.stat().st_size < 200:
        return None
    user_prompts: List[str] = []
    tool_counts: Counter = Counter()
    subagents: List[Dict] = []
    files_touched: set = set()
    first_ts: Optional[float] = None
    last_ts: Optional[float] = None
    cwd: Optional[str] = None
    session_id = path.stem
    project_hash = path.parent.name

    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Try to pull a timestamp
                ts = ev.get("timestamp") or ev.get("ts") or ev.get("time")
                if isinstance(ts, str):
                    try:
                        ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        ts = None
                if isinstance(ts, (int, float)):
                    first_ts = first_ts or ts
                    last_ts = ts
                if not cwd and ev.get("cwd"):
                    cwd = ev.get("cwd")

                # User prompt
                if ev.get("type") == "user" or ev.get("role") == "user":
                    msg = ev.get("message") or ev.get("content")
                    if isinstance(msg, dict):
                        msg = msg.get("content") or msg.get("text")
                    if isinstance(msg, list):
                        # content blocks — find text
                        msg = " ".join(
                            (b.get("text", "") if isinstance(b, dict) else str(b))
                            for b in msg
                        )
                    if isinstance(msg, str) and msg.strip():
                        user_prompts.append(msg.strip()[:500])

                # Tool use
                tool_name = None
                if ev.get("type") == "tool_use" or ev.get("hook_event_name") == "PreToolUse":
                    tool_name = ev.get("tool_name") or ev.get("name")
                elif isinstance(ev.get("message"), dict):
                    inner = ev["message"]
                    if inner.get("type") == "tool_use":
                        tool_name = inner.get("name")
                if tool_name:
                    tool_counts[tool_name] += 1
                    if tool_name in ("Task", "Agent"):
                        ti = (ev.get("tool_input") or
                              (ev.get("message", {}).get("input") if isinstance(ev.get("message"), dict) else None) or {})
                        if isinstance(ti, dict):
                            sub = {
                                "type": ti.get("subagent_type") or ti.get("agent") or "general-purpose",
                                "description": (ti.get("description") or ti.get("prompt") or "")[:200],
                            }
                            if sub["description"]:
                                subagents.append(sub)
                    # Track touched files
                    ti = ev.get("tool_input") or {}
                    if isinstance(ti, dict):
                        fp = ti.get("file_path") or ti.get("path")
                        if fp:
                            files_touched.add(str(fp)[:200])
    except Exception as exc:
        logger.warning("could not parse %s: %s", path, exc)
        return None

    if not first_ts:
        first_ts = path.stat().st_ctime
    if not last_ts:
        last_ts = path.stat().st_mtime

    project_path = cwd or _decode_project_hash(project_hash)
    project_name = Path(project_path).name if project_path else project_hash[:24]

    return {
        "session_id": session_id,
        "project_hash": project_hash,
        "project_path": project_path,
        "project_name": project_name,
        "started": first_ts,
        "ended": last_ts,
        "duration_s": max(0, last_ts - first_ts),
        "user_prompts": user_prompts,
        "tool_counts": dict(tool_counts),
        "tool_calls": sum(tool_counts.values()),
        "subagents": subagents,
        "files_touched": sorted(files_touched)[:30],
        "source_jsonl": str(path),
    }


def _write_session_note_from_summary(summary: Dict) -> Optional[Path]:
    """Write a session note matching the format of agent_runner._write_session_note."""
    from openjarvis.tools import obsidian_brain as ob
    if not ob.DEFAULT_VAULT.exists():
        return None
    sessions_dir = ob.BRAIN_ROOT / "Sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    started = datetime.fromtimestamp(summary["started"])
    ended = datetime.fromtimestamp(summary["ended"])
    dur = summary["duration_s"]
    dur_str = (f"{int(dur // 3600)}h {int((dur % 3600) // 60)}m"
               if dur >= 3600 else f"{int(dur // 60)}m {int(dur % 60)}s"
               if dur >= 60 else f"{dur:.0f}s")

    slug = _slugify(summary["project_name"])
    fname = f"{started.strftime('%Y-%m-%d %H-%M')} - {slug}.md"
    target = sessions_dir / fname
    if target.exists():
        # Compare session ids — if same session, skip
        existing = target.read_text(encoding="utf-8", errors="replace")
        if summary["session_id"] in existing:
            return None
        # Otherwise disambiguate
        i = 2
        while (sessions_dir / f"{started.strftime('%Y-%m-%d %H-%M')} - {slug} ({i}).md").exists():
            i += 1
        target = sessions_dir / f"{started.strftime('%Y-%m-%d %H-%M')} - {slug} ({i}).md"

    sorted_tools = sorted(summary["tool_counts"].items(), key=lambda kv: -kv[1])

    lines = [
        "---",
        f"session_id: {summary['session_id']}",
        f"project: {summary['project_name']}",
        f"cwd: {summary['project_path']}",
        f"started: {started.isoformat(timespec='seconds')}",
        f"ended:   {ended.isoformat(timespec='seconds')}",
        f"duration: {dur_str}",
        f"tool_calls: {summary['tool_calls']}",
        f"prompts: {len(summary['user_prompts'])}",
        f"subagents: {len(summary['subagents'])}",
        "type: claude-session",
        "tags: [claude-session, backfill]",
        "---",
        "",
        f"# {summary['project_name']} · {started.strftime('%a %d %b %Y, %H:%M')}",
        "",
    ]
    bits = []
    if summary["tool_calls"]:
        bits.append(f"{summary['tool_calls']} tool calls")
    if sorted_tools:
        bits.append("mostly " + ", ".join(f"{n}×{k}" for k, n in sorted_tools[:3]))
    if summary["subagents"]:
        bits.append(f"{len(summary['subagents'])} subagent dispatches")
    if dur:
        bits.append(f"over {dur_str}")
    if bits:
        lines.append("> " + " · ".join(bits))
        lines.append("")

    if summary["user_prompts"]:
        lines.append("## Prompts")
        for p in summary["user_prompts"][:30]:
            snippet = p.replace("\n", " ")
            if len(snippet) > 220:
                snippet = snippet[:217] + "…"
            lines.append(f"- {snippet}")
        if len(summary["user_prompts"]) > 30:
            lines.append(f"- _…and {len(summary['user_prompts']) - 30} more_")
        lines.append("")

    if summary["subagents"]:
        lines.append("## Sub-agents dispatched")
        for s in summary["subagents"][:20]:
            lines.append(f"- **{s['type']}** — {s['description']}")
        lines.append("")

    if sorted_tools:
        lines.append("## Tool usage")
        for name, count in sorted_tools[:25]:
            lines.append(f"- `{name}` × {count}")
        lines.append("")

    if summary["files_touched"]:
        lines.append("## Files touched (sample)")
        for f in summary["files_touched"][:15]:
            lines.append(f"- `{f}`")
        if len(summary["files_touched"]) > 15:
            lines.append(f"- _…and {len(summary['files_touched']) - 15} more_")
        lines.append("")

    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return target


def backup_claude_sessions() -> Tuple[int, int, int]:
    """Backfill all Claude Code sessions into Brain/Sessions/.

    Returns (written, skipped, errors).
    """
    if not CLAUDE_PROJECTS_DIR.exists():
        logger.warning("Claude projects dir not found: %s", CLAUDE_PROJECTS_DIR)
        return (0, 0, 0)

    written = skipped = errors = 0
    for jsonl in CLAUDE_PROJECTS_DIR.rglob("*.jsonl"):
        try:
            summary = _summarise_session_jsonl(jsonl)
            if summary is None:
                skipped += 1
                continue
            note = _write_session_note_from_summary(summary)
            if note is None:
                skipped += 1
            else:
                written += 1
        except Exception:
            logger.exception("error processing %s", jsonl)
            errors += 1
    return (written, skipped, errors)


# ---------------------------------------------------------------------------
# Project folder scanner
# ---------------------------------------------------------------------------


_NOISY_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv",
               ".next", "dist", "build", ".obsidian", ".cache", ".idea",
               "target", ".pytest_cache", ".mypy_cache"}


def _summarise_project_folder(folder: Path) -> Optional[Dict]:
    """Light-weight summary of a project folder for the Brain/Projects note."""
    if not folder.is_dir():
        return None
    readme = None
    for candidate in ("README.md", "Readme.md", "readme.md", "README.txt"):
        p = folder / candidate
        if p.exists() and p.is_file():
            readme = p
            break

    total_files = 0
    total_bytes = 0
    last_modified = folder.stat().st_mtime
    notable: List[Tuple[str, int]] = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in _NOISY_DIRS and not d.startswith(".")]
        for f in files:
            p = Path(root) / f
            try:
                size = p.stat().st_size
                mtime = p.stat().st_mtime
            except Exception:
                continue
            total_files += 1
            total_bytes += size
            last_modified = max(last_modified, mtime)
            # Track largest non-binary files in repo root for context
            if p.parent == folder and size < 200_000:
                notable.append((p.name, size))
        if total_files > 5000:   # cheap cap so monorepos don't take forever
            break
    notable.sort(key=lambda kv: -kv[1])
    notable = notable[:8]

    return {
        "path": str(folder),
        "name": folder.name,
        "readme": str(readme) if readme else None,
        "readme_excerpt": (readme.read_text(encoding="utf-8", errors="replace")[:600]
                           if readme else None),
        "total_files": total_files,
        "total_bytes": total_bytes,
        "last_modified": last_modified,
        "root_files": [n for n, _ in notable],
    }


def _write_project_note(summary: Dict) -> Optional[Path]:
    from openjarvis.tools import obsidian_brain as ob
    if not ob.DEFAULT_VAULT.exists():
        return None
    projects_dir = ob.BRAIN_ROOT / "Projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(summary["name"])
    target = projects_dir / f"{slug}.md"
    if target.exists():
        # Update lightly — only refresh the metadata block, keep manual edits below
        existing = target.read_text(encoding="utf-8", errors="replace")
        if "<!-- BACKUP_END -->" in existing:
            below = existing.split("<!-- BACKUP_END -->", 1)[1]
        else:
            below = ""
        body = _render_project_note(summary, manual_below=below)
        target.write_text(body, encoding="utf-8")
        return target

    body = _render_project_note(summary, manual_below="")
    target.write_text(body, encoding="utf-8")
    return target


def _render_project_note(s: Dict, manual_below: str = "") -> str:
    last = datetime.fromtimestamp(s["last_modified"])
    size_mb = s["total_bytes"] / (1024 * 1024)
    lines = [
        "---",
        f"name: {s['name']}",
        f"path: {s['path']}",
        f"files: {s['total_files']}",
        f"size_mb: {size_mb:.2f}",
        f"last_modified: {last.isoformat(timespec='seconds')}",
        "type: project",
        "tags: [project]",
        "---",
        "",
        "<!-- BACKUP_START — fields above this line are auto-generated. -->",
        f"# {s['name']}",
        "",
        f"**Path:** `{s['path']}`",
        f"**Files:** {s['total_files']} · **Size:** {size_mb:.2f} MB · **Last touched:** {last.strftime('%a %d %b %Y, %H:%M')}",
        "",
    ]
    if s.get("readme_excerpt"):
        lines.append("## README excerpt")
        lines.append("```markdown")
        lines.append(s["readme_excerpt"].rstrip())
        lines.append("```")
        lines.append("")
    if s.get("root_files"):
        lines.append("## Root files")
        for f in s["root_files"]:
            lines.append(f"- `{f}`")
        lines.append("")
    lines.append("<!-- BACKUP_END -->")
    if manual_below.strip():
        lines.append(manual_below.rstrip())
    return "\n".join(lines).rstrip() + "\n"


def backup_project_folders(parent: Path) -> Tuple[int, int]:
    """Walk parent's immediate subfolders, write a Project note per folder.

    Returns (written, skipped)."""
    parent = Path(parent)
    if not parent.exists():
        return (0, 0)
    written = skipped = 0
    for child in sorted(parent.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name in _NOISY_DIRS:
            skipped += 1
            continue
        # Don't back up the vault itself
        try:
            from openjarvis.tools.obsidian_brain import DEFAULT_VAULT
            if Path(child).resolve() == Path(DEFAULT_VAULT).resolve():
                skipped += 1
                continue
        except Exception:
            pass
        try:
            summary = _summarise_project_folder(child)
            if not summary:
                skipped += 1
                continue
            _write_project_note(summary)
            written += 1
        except Exception:
            logger.exception("error backing up %s", child)
            skipped += 1
    return (written, skipped)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ChatGPT export importer
# ---------------------------------------------------------------------------
#
# Triggered by the user via chat.openai.com -> Settings -> Data Controls ->
# Export data. ChatGPT emails a zip containing:
#   - conversations.json     (every chat thread, full message tree)
#   - user.json              (account metadata)
#   - shared_conversations.json
#   - chat.html              (web preview)
#   - message_feedback.json
#
# We only need conversations.json — each entry becomes a markdown note.


def _flatten_messages(mapping: dict) -> List[Dict]:
    """ChatGPT conversations are stored as a tree of nodes connected by
    parent/children pointers. Walk it root → tip in chronological order."""
    if not isinstance(mapping, dict):
        return []
    # Find root (the node with no parent)
    root_id = None
    for nid, node in mapping.items():
        if not (node or {}).get("parent"):
            root_id = nid
            break
    if root_id is None:
        return []
    out: List[Dict] = []
    stack = [root_id]
    while stack:
        nid = stack.pop()
        node = mapping.get(nid) or {}
        msg = node.get("message")
        if msg:
            out.append(msg)
        # Walk children depth-first; main thread is usually the last child
        children = node.get("children") or []
        for cid in reversed(children):
            stack.append(cid)
    # Sort by create_time so out-of-order branches don't confuse readers
    out.sort(key=lambda m: (m.get("create_time") or 0))
    return out


def _extract_text(msg: dict) -> str:
    """Pull the human-readable text out of a ChatGPT message node."""
    content = (msg or {}).get("content") or {}
    parts = content.get("parts") or []
    bits: List[str] = []
    for p in parts:
        if isinstance(p, str):
            bits.append(p)
        elif isinstance(p, dict):
            # Multimodal — image refs etc. Just note their kind.
            t = p.get("content_type") or p.get("type") or ""
            if t.startswith("image"):
                bits.append("[image]")
            elif p.get("text"):
                bits.append(p["text"])
    return "\n".join(bits).strip()


def _summarise_chatgpt_conversation(conv: dict) -> Optional[Dict]:
    title = (conv.get("title") or "").strip() or "untitled"
    cid = conv.get("id") or conv.get("conversation_id") or ""
    created = conv.get("create_time") or 0
    updated = conv.get("update_time") or created
    model = conv.get("default_model_slug") or conv.get("model_slug") or ""
    messages = _flatten_messages(conv.get("mapping") or {})
    if not messages:
        return None
    # Keep only user/assistant/system; skip empty
    rendered = []
    user_count = assistant_count = 0
    for m in messages:
        role = ((m.get("author") or {}).get("role") or "").lower()
        if role not in ("user", "assistant", "system", "tool"):
            continue
        text = _extract_text(m)
        if not text:
            continue
        if role == "user":
            user_count += 1
        elif role == "assistant":
            assistant_count += 1
        rendered.append({"role": role, "text": text,
                         "ts": m.get("create_time") or 0})
    if not rendered:
        return None
    return {
        "id": cid,
        "title": title,
        "created": created,
        "updated": updated,
        "model": model,
        "messages": rendered,
        "user_count": user_count,
        "assistant_count": assistant_count,
    }


def _write_chatgpt_note(s: Dict) -> Optional[Path]:
    from openjarvis.tools import obsidian_brain as ob
    if not ob.DEFAULT_VAULT.exists():
        return None
    cg_dir = ob.BRAIN_ROOT / "ChatGPT"
    cg_dir.mkdir(parents=True, exist_ok=True)

    created = datetime.fromtimestamp(s["created"]) if s["created"] else datetime.now()
    slug = _slugify(s["title"])
    fname = f"{created.strftime('%Y-%m-%d')} - {slug}.md"
    target = cg_dir / fname
    if target.exists():
        # Skip if same conversation id already there (idempotent re-imports)
        existing = target.read_text(encoding="utf-8", errors="replace")
        if s["id"] and s["id"] in existing:
            return None
        i = 2
        while (cg_dir / f"{created.strftime('%Y-%m-%d')} - {slug} ({i}).md").exists():
            i += 1
        target = cg_dir / f"{created.strftime('%Y-%m-%d')} - {slug} ({i}).md"

    updated_dt = datetime.fromtimestamp(s["updated"]) if s["updated"] else created
    lines = [
        "---",
        f"id: {s['id']}",
        f"title: {s['title']}",
        f"model: {s['model']}",
        f"created: {created.isoformat(timespec='seconds')}",
        f"updated: {updated_dt.isoformat(timespec='seconds')}",
        f"messages: {len(s['messages'])}",
        f"user_msgs: {s['user_count']}",
        f"assistant_msgs: {s['assistant_count']}",
        "type: chatgpt-conversation",
        "tags: [chatgpt, conversation]",
        "---",
        "",
        f"# {s['title']}",
        "",
        f"_{len(s['messages'])} messages · {s['user_count']} from you · "
        f"{s['assistant_count']} from {s['model'] or 'GPT'} · "
        f"{created.strftime('%a %d %b %Y')}_",
        "",
    ]
    for m in s["messages"]:
        role_label = {"user": "You", "assistant": "ChatGPT",
                      "system": "System", "tool": "Tool"}.get(m["role"], m["role"])
        ts = (datetime.fromtimestamp(m["ts"]).strftime(" · %H:%M")
              if m["ts"] else "")
        lines.append(f"## {role_label}{ts}")
        lines.append("")
        # Truncate giant messages to keep notes readable
        text = m["text"]
        if len(text) > 6000:
            text = text[:6000] + "\n\n…[truncated for vault — full message in original export]"
        lines.append(text)
        lines.append("")
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return target


def backup_chatgpt_export(source: Path) -> Tuple[int, int, int]:
    """Import a ChatGPT data-export zip (or extracted folder).

    Returns (written, skipped, errors)."""
    source = Path(source)
    if not source.exists():
        logger.error("ChatGPT export not found: %s", source)
        return (0, 0, 1)

    # Locate conversations.json — either inside zip or in the folder
    convs_data: Optional[List[Dict]] = None
    if source.is_file() and source.suffix.lower() == ".zip":
        import zipfile
        try:
            with zipfile.ZipFile(source) as zf:
                names = [n for n in zf.namelist() if n.endswith("conversations.json")]
                if not names:
                    logger.error("conversations.json not found in zip")
                    return (0, 0, 1)
                with zf.open(names[0]) as f:
                    convs_data = json.loads(f.read().decode("utf-8", errors="replace"))
        except Exception:
            logger.exception("zip read failed")
            return (0, 0, 1)
    elif source.is_dir():
        cj = source / "conversations.json"
        if not cj.exists():
            # Try recursive
            found = list(source.rglob("conversations.json"))
            if not found:
                logger.error("conversations.json not found under %s", source)
                return (0, 0, 1)
            cj = found[0]
        try:
            convs_data = json.loads(cj.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            logger.exception("conversations.json read failed")
            return (0, 0, 1)
    else:
        logger.error("source must be a .zip or a folder: %s", source)
        return (0, 0, 1)

    if not isinstance(convs_data, list):
        logger.error("unexpected conversations.json format")
        return (0, 0, 1)

    written = skipped = errors = 0
    for conv in convs_data:
        try:
            s = _summarise_chatgpt_conversation(conv)
            if not s:
                skipped += 1
                continue
            note = _write_chatgpt_note(s)
            if note is None:
                skipped += 1
            else:
                written += 1
        except Exception:
            logger.exception("error on conversation %s", conv.get("id", "?"))
            errors += 1
    return (written, skipped, errors)


def main() -> None:
    """Subcommands:

       (no args)            backup Claude sessions + projects under E:\\Claude
       chatgpt <path>       import a ChatGPT data-export zip or folder
       all <chatgpt-path>   do everything in one go
    """
    import sys
    args = sys.argv[1:]
    cmd = args[0].lower() if args else ""

    if cmd == "chatgpt":
        if len(args) < 2:
            print("Usage: vault_backup chatgpt <path-to-export.zip-or-folder>")
            sys.exit(1)
        src = Path(args[1])
        print(f"Importing ChatGPT export from {src}…")
        w, s, e = backup_chatgpt_export(src)
        print(f"   written: {w}, skipped (already imported / empty): {s}, errors: {e}")
        return

    if cmd == "all":
        chatgpt_src = Path(args[1]) if len(args) > 1 else None
        print("[1/3] Claude sessions:")
        w, s, e = backup_claude_sessions()
        print(f"   written: {w}, skipped: {s}, errors: {e}")
        print()
        parent = Path(r"E:\Claude")
        print(f"[2/3] Project folders under {parent}:")
        w, s = backup_project_folders(parent)
        print(f"   written: {w}, skipped: {s}")
        print()
        if chatgpt_src and chatgpt_src.exists():
            print(f"[3/3] ChatGPT export from {chatgpt_src}:")
            w, s, e = backup_chatgpt_export(chatgpt_src)
            print(f"   written: {w}, skipped: {s}, errors: {e}")
        else:
            print("[3/3] ChatGPT: skipped (no export path given). "
                  "Run `vault_backup chatgpt <path>` once you've downloaded one.")
        return

    # Default: just sessions + projects (legacy behaviour)
    print("Backing up Claude Code sessions and project folders to the vault…")
    print()
    print("[1/2] Sessions:")
    w, s, e = backup_claude_sessions()
    print(f"   written: {w}, skipped: {s}, errors: {e}")
    print()
    parent = Path(args[0]) if args else Path(r"E:\Claude")
    print(f"[2/2] Project folders under {parent}:")
    w, s = backup_project_folders(parent)
    print(f"   written: {w}, skipped: {s}")
    print()
    from openjarvis.tools.obsidian_brain import BRAIN_ROOT
    print(f"Done. Vault root: {BRAIN_ROOT}")


if __name__ == "__main__":
    main()

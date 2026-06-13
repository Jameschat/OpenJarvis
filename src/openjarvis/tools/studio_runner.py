from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from openjarvis.tools import project_preview, studio_context, studio_research, studio_store, studio_workflows

# Soft run-timeout: stops the UI spinner after this long. Raised from 300s
# (2026-06-13) because remote deep-work Qwen tasks (128K context + revision
# loop + escalation) legitimately run several minutes; the old value reaped
# them mid-flight. Still-running tasks are now LEFT to complete, not killed.
STUDIO_RUN_STALE_AFTER_SECONDS = 600
STUDIO_CONTEXT_CHAR_LIMIT = int(os.environ.get("OPENJARVIS_STUDIO_CONTEXT_CHAR_LIMIT", "512000"))
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

_ECC_LITE_SKILL_GUIDANCE = {
    "agentic-engineering": "Use agentic decomposition: define success, split into concrete work units, and keep outputs verifiable.",
    "autonomous-agent-harness": "For multi-step autonomous work, keep a clear loop of plan, act, observe, reflect, and stop condition.",
    "verification-loop": "Do not treat a task as done without evidence. Name the check, result, and remaining risk.",
    "tdd-workflow": "For code changes, prefer failing test, minimal fix, passing verification, then focused cleanup.",
    "iterative-retrieval": "For uncertain facts, retrieve in passes, compare sources, and only then summarize.",
    "browser-qa": "For web/UI work, request preview and browser_visual_check evidence before final claims.",
    "search-first": "For current or missing information, request web_search/github_search before guessing.",
    "plan-orchestrate": "Turn broad requests into ordered steps with acceptance criteria and owner/agent fit.",
    "security-review": "Check secrets, auth, privacy, permission, and destructive-action risk before proposing execution.",
    "benchmark-optimization-loop": "For performance work, record baseline, one change, retest, and compare evidence.",
}

_STUDIO_SKILL_GUIDANCE = {
    "taste-skill": {
        "label": "Taste Skill",
        "aliases": ("taste skill", "tasteskill", "design taste", "design-taste-frontend"),
        "guidance": (
            "Use the installed Taste Skill for frontend work: infer the page kind, audience, reference/vibe signals, "
            "and brand constraints before touching code; state a one-line design read; avoid templated/slop layouts; "
            "and apply it mainly to landing pages, portfolios, redesigns, and public-facing website polish."
        ),
    },
    "ui-ux-pro-max": {
        "label": "UI UX Pro Max",
        "aliases": ("ui ux pro max", "ux ui pro max", "ui/ux pro max", "ux/ui pro max"),
        "guidance": (
            "Use premium product/web design judgement: strong visual hierarchy, polished responsive layouts, "
            "domain-matched styling, accessible controls, careful spacing/typography, and responsive visual QA. "
            "For website work, produce inspectable page sections, avoid generic white form styling, and request "
            "browser preview evidence before claiming the UI is finished."
        ),
    },
    "superpowers": {
        "label": "Superpowers",
        "aliases": ("superpowers",),
        "guidance": (
            "Use the Superpowers operating loop: clarify objective, plan the smallest safe change, use tests or "
            "verification before completion, and report evidence instead of assumptions."
        ),
    },
    "browser-qa": {
        "label": "Browser QA",
        "aliases": ("browser qa", "visual qa"),
        "guidance": "For visual work, request a local preview and browser visual check, then fix layout issues before final output.",
    },
    "unreal-engine": {
        "label": "Unreal Engine",
        "aliases": ("unreal engine", "ue5"),
        "guidance": "For Unreal work, use UE-specific project context, module boundaries, validation, and editor/test evidence.",
    },
}


def studio_skill_catalog() -> list[dict[str, str]]:
    return [
        {"id": key, "label": str(value["label"]), "description": str(value["guidance"])}
        for key, value in _STUDIO_SKILL_GUIDANCE.items()
    ]


def _normalize_selected_skills(selected_skills: list[str] | tuple[str, ...] | None, prompt: str = "") -> list[str]:
    normalized: list[str] = []
    text = " ".join((prompt or "").strip().lower().split())

    def add(skill_id: str) -> None:
        if skill_id in _STUDIO_SKILL_GUIDANCE and skill_id not in normalized:
            normalized.append(skill_id)

    for raw in selected_skills or []:
        value = " ".join(str(raw or "").strip().lower().split())
        if not value:
            continue
        if value in _STUDIO_SKILL_GUIDANCE:
            add(value)
            continue
        for skill_id, info in _STUDIO_SKILL_GUIDANCE.items():
            aliases = [str(info["label"]).lower(), *(str(alias).lower() for alias in info.get("aliases", ()))]
            if value in aliases:
                add(skill_id)
                break

    for skill_id, info in _STUDIO_SKILL_GUIDANCE.items():
        if any(str(alias).lower() in text for alias in info.get("aliases", ())):
            add(skill_id)
    return normalized[:6]


def _build_selected_skill_guidance(selected_skills: list[str]) -> str:
    if not selected_skills:
        return ""
    lines = [
        "== STUDIO SELECTED SKILLS ==",
        "The operator explicitly selected these Jarvis skills for this run. Treat them as required operating guidance.",
    ]
    for skill_id in selected_skills:
        info = _STUDIO_SKILL_GUIDANCE.get(skill_id)
        if not info:
            continue
        lines.append(f"- {info['label']} (`{skill_id}`): {info['guidance']}")
    return "\n".join(lines)


def _select_ecc_command(prompt: str, workflow: str) -> str:
    text = " ".join((prompt or "").strip().lower().split())
    if workflow == "debug" or any(term in text for term in ("fix", "bug", "error", "failed", "failure", "broken")):
        return "build-fix"
    if workflow == "feature_dev" or any(
        term in text
        for term in ("build", "create", "implement", "add", "make", "website", "app", "portal", "component")
    ):
        return "feature-dev"
    if workflow == "verify" or any(term in text for term in ("review", "audit", "verify", "check")):
        return "code-review"
    return ""


def _select_ecc_lite_skills(prompt: str, workflow: str) -> list[str]:
    text = " ".join((prompt or "").strip().lower().split())
    selected: list[str] = []

    def add(*names: str) -> None:
        for name in names:
            if name not in selected:
                selected.append(name)

    if workflow in {"execute", "debug", "spec", "feature_dev"} or any(
        term in text
        for term in (
            "build",
            "create",
            "code",
            "fix",
            "implement",
            "app",
            "website",
            "web page",
            "frontend",
            "backend",
            "portal",
        )
    ):
        add("agentic-engineering", "plan-orchestrate", "verification-loop", "tdd-workflow")
    if any(term in text for term in ("code", "fix", "implement", "test", "regression", "debug")):
        add("tdd-workflow")
    if any(term in text for term in ("website", "web page", "frontend", "ui", "browser", "preview", "visual")):
        add("browser-qa")
    if workflow == "qwen_workflow" or any(term in text for term in ("research", "search", "github", "internet", "latest", "look up")):
        add("search-first", "iterative-retrieval", "verification-loop")
    if any(term in text for term in ("secure", "security", "auth", "password", "secret", "client data", "email")):
        add("security-review")
    if any(term in text for term in ("benchmark", "performance", "tok/s", "tokens", "speed", "throughput", "qwen")):
        add("benchmark-optimization-loop", "verification-loop")
    if any(term in text for term in ("autonomous", "agent", "agents", "workflow", "department", "multi-step")):
        add("autonomous-agent-harness", "agentic-engineering", "plan-orchestrate")

    if not selected and workflow not in {"direct_chat", "context_direct"}:
        add("plan-orchestrate", "verification-loop")
    return selected[:6]


def _build_ecc_lite_guidance(prompt: str, workflow: str) -> str:
    skill_names = _select_ecc_lite_skills(prompt, workflow)
    ecc_command = _select_ecc_command(prompt, workflow)
    if not skill_names and not ecc_command:
        return ""
    lines = [
        "== ECC-LITE AGENT OPERATING LAYER ==",
        "Use these installed Jarvis/ECC skills as operating guidance for this task.",
        "If you need to discover available ECC-lite skills or blocked ECC commands, request the safe tool `ecc_catalog`.",
        "If you need the full skill text, request the safe tool `skill_guidance` with the skill name.",
        "If you need a cached ECC command workflow such as feature-dev or build-fix, request `ecc_command_guidance` by command name.",
        "Do not run ECC commands, hooks, shell scripts, installs, or direct edits unless the tool bridge explicitly allows it.",
    ]
    if ecc_command:
        lines.extend(
            [
                "",
                f"Required ECC command profile: `{ecc_command}`.",
                (
                    "Before planning the work, request this read-only command guidance with "
                    f"`ecc_command_guidance` using name `{ecc_command}`."
                ),
            ]
        )
    lines.extend(["", "Required skill guidance:"])
    for name in skill_names:
        lines.append(f"- {name}: {_ECC_LITE_SKILL_GUIDANCE[name]}")
    lines.extend(
        [
            "",
            "Expected execution loop:",
            "1. State the concrete objective and acceptance criteria.",
            "2. Request any needed read/research/procedure tools through `qwen_tool_requests`.",
            "3. Produce the smallest useful output with verification evidence and next action.",
        ]
    )
    return "\n".join(lines)


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


def _looks_like_project_continuation(prompt: str) -> bool:
    text = " ".join((prompt or "").strip().lower().split())
    if not text or len(text) > 360:
        return False
    if not any(term in text for term in ("continue", "carry on", "pick up", "resume", "modernis", "moderniz")):
        return False
    return any(term in text for term in ("project", "website", "site", "app", "build"))


def _looks_like_specific_project_work(prompt: str) -> bool:
    text = " ".join((prompt or "").strip().lower().split())
    if not text:
        return False
    specific_targets = (
        "dining page",
        "events page",
        "room page",
        "rooms page",
        "homepage",
        "landing page",
        "contact form",
        "booking form",
        "newsletter form",
        "section",
        "component",
    )
    work_terms = ("build", "create", "add", "implement", "make", "continue", "modernis", "moderniz")
    return any(target in text for target in specific_targets) and any(term in text for term in work_terms)


def _project_continuation_reply(prompt: str, context_pack: dict[str, Any]) -> str | None:
    if not _looks_like_project_continuation(prompt):
        return None
    if _looks_like_specific_project_work(prompt):
        return None
    markdown = str(context_pack.get("markdown") or "").strip()
    if not markdown or "### STATE.md" not in markdown:
        return None

    current_phase = ""
    where_left_off: list[str] = []
    open_items: list[str] = []
    in_left_off = False
    in_open_items = False
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith("**current phase:**"):
            current_phase = line.replace("**", "")
            continue
        if lower.startswith("## where we left off"):
            in_left_off = True
            in_open_items = False
            continue
        if lower.startswith("## current known issues") or lower.startswith("## open"):
            in_open_items = True
            in_left_off = False
            continue
        if lower.startswith("## ") and not lower.startswith(("## where", "## current known", "## open")):
            in_left_off = False
            in_open_items = False
        if in_left_off and len(where_left_off) < 3 and not line.startswith("#"):
            where_left_off.append(line)
        if in_open_items and line.startswith("- ") and len(open_items) < 5:
            open_items.append(line)

    lines = [
        "Yes. I found the active project state and can continue it.",
        "",
    ]
    if current_phase:
        lines.append(current_phase)
    if where_left_off:
        lines += ["", "Where we left off:"] + [f"- {item}" for item in where_left_off]
    if open_items:
        lines += ["", "Best next modernisation slices:"] + open_items
    lines += [
        "",
        "Recommended next command:",
        "`Build the dedicated dining page for the Westhill site, then verify it locally.`",
        "",
        "I answered this from the project vault so it returns quickly; the local Qwen build lane can take over once the task is specific.",
    ]
    return "\n".join(lines)


def _looks_like_new_project_planning(prompt: str) -> bool:
    text = " ".join((prompt or "").strip().lower().split())
    if not text or len(text) > 1200:
        return False
    project_terms = ("new project", "start a project", "planning a new project", "project -")
    if not any(term in text for term in project_terms):
        return False
    return any(term in text for term in ("platform", "portal", "app", "website", "system", "tool"))


def _new_project_planning_reply(prompt: str) -> str | None:
    if not _looks_like_new_project_planning(prompt):
        return None
    text = (prompt or "").lower()
    if not any(term in text for term in ("email", "emails", "attachment", "client")):
        return (
            "I can start this as a new project brief.\n\n"
            "Recommended first step: create the project folder and define `PROJECT.md`, "
            "`REQUIREMENTS.md`, `ROADMAP.md`, `STATE.md`, and `CONTEXT.md`, then build a small "
            "HTML portal prototype before adding integrations."
        )

    return "\n".join(
        [
            "Yes. Start this as **Local Email Client Portal**.",
            "",
            "Core product shape:",
            "- Local HTML-based portal with a secure backend running on your machine or LAN.",
            "- IMAP/OAuth email import for one or more mailboxes.",
            "- Client detection from sender domain, sender name, subject, existing contacts, and manual corrections.",
            "- Per-client file storage: `clients/<client-name>/files/` for attachments, with duplicate detection.",
            "- Client timeline showing emails, attachments, notes, and follow-up status.",
            "- Search across clients, email body, filenames, dates, and attachment metadata.",
            "",
            "Security baseline:",
            "- Local-first storage by default; no cloud sync unless explicitly enabled.",
            "- Encrypted secrets store for email tokens/passwords, never committed to git or vault.",
            "- Login required for the portal, with session timeout.",
            "- Attachment scanning and file type allow/deny rules before opening files.",
            "- Audit log for imports, file moves, client merges, deletes, and exports.",
            "- Role-ready design, even if v1 only has you as the admin.",
            "",
            "Build phases:",
            "1. Project scaffold: requirements, data model, and secure local config.",
            "2. Email importer: read-only mailbox connection, message index, attachment extraction.",
            "3. Client categorisation: rule-based first, then Qwen-assisted suggestions with manual approval.",
            "4. Portal UI: clients, inbox review queue, files, search, and audit log.",
            "5. Hardening: auth, encrypted token storage, backup/export, permission checks.",
            "",
            "Recommended next command:",
            "`Create the project files for Local Email Client Portal and draft the v1 requirements, roadmap, and security model.`",
            "",
            "I answered this directly so Studio does not wait on a slow background Qwen run. Qwen can take over once the project scaffold exists.",
        ]
    )


def _recent_chat_text(store: studio_store.StudioStore, chat_id: str, limit: int = 8) -> str:
    try:
        chat = store.get_chat(chat_id)
    except KeyError:
        return ""
    messages = chat.get("messages") or []
    return "\n".join(str(message.get("content") or "") for message in messages[-limit:] if isinstance(message, dict))


def _looks_like_phase_one_email_portal_followup(
    prompt: str,
    store: studio_store.StudioStore,
    chat_id: str,
) -> bool:
    text = " ".join((prompt or "").strip().lower().split())
    if text not in {"continue phase 1", "continue phase one", "start phase 1", "start phase one"}:
        return False
    recent = _recent_chat_text(store, chat_id).lower()
    return "local email client portal" in recent


def _projects_root() -> Path:
    return Path(os.environ.get("OPENJARVIS_PROJECTS_ROOT", r"E:\Claude"))


def _write_if_missing(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return False
    path.write_text(content, encoding="utf-8")
    return True


def _create_email_portal_phase1_scaffold(
    store: studio_store.StudioStore,
    *,
    chat_id: str,
) -> dict[str, Any]:
    project_id = "local-email-client-portal"
    title = "Local Email Client Portal"
    project_dir = _projects_root() / project_id
    vault_dir = BRAIN_ROOT / "Projects" / project_id
    created: list[str] = []

    files = {
        project_dir / "README.md": "\n".join(
            [
                "# Local Email Client Portal",
                "",
                "Local-first portal for importing email, categorising messages by client, and storing attachments under each client.",
                "",
                "## Phase 1",
                "- Confirm requirements and non-goals.",
                "- Define the data model and secure storage approach.",
                "- Keep email access read-only until import safety is proven.",
                "- Build a small local HTML portal scaffold before connecting real mailboxes.",
                "",
            ]
        ),
        project_dir / ".gitignore": "\n".join(
            [
                ".env",
                ".env.*",
                "secrets/",
                "data/",
                "clients/",
                "__pycache__/",
                ".venv/",
                "",
            ]
        ),
        project_dir / "docs" / "SECURITY.md": "\n".join(
            [
                "# Security Model",
                "",
                "## Baseline",
                "- Local-first storage by default.",
                "- Email credentials and OAuth tokens must live in an encrypted secrets store, never in git or the vault.",
                "- Portal access requires login and session timeout.",
                "- Import starts read-only: no email delete, send, move, or mailbox mutation in v1.",
                "- Attachments are stored under client folders only after file type checks and duplicate detection.",
                "- Audit log all imports, attachment writes, client merges, deletes, exports, and settings changes.",
                "",
                "## Open Decisions",
                "- Choose mailbox auth: IMAP app password versus OAuth provider flow.",
                "- Choose encrypted local storage implementation.",
                "- Decide whether this stays single-user local or becomes LAN multi-user.",
                "",
            ]
        ),
        project_dir / "docs" / "DATA_MODEL.md": "\n".join(
            [
                "# Data Model Draft",
                "",
                "## Entities",
                "- Client: name, aliases, domains, contacts, notes.",
                "- EmailMessage: provider id, mailbox, sender, recipients, subject, body text, date, client id.",
                "- Attachment: filename, content hash, mime type, size, source email id, client id, storage path.",
                "- ImportRun: mailbox, started/finished timestamps, counts, errors.",
                "- AuditEvent: actor, action, target, timestamp, metadata.",
                "",
            ]
        ),
        vault_dir / "PROJECT.md": "\n".join(
            [
                "---",
                f"slug: {project_id}",
                f"path: {project_dir}",
                "status: planning",
                "---",
                "",
                "# Local Email Client Portal",
                "",
                "Local-first secure portal for importing email, grouping messages by client, and storing attachments under client folders.",
                "",
            ]
        ),
        vault_dir / "REQUIREMENTS.md": "\n".join(
            [
                "# Requirements",
                "",
                "## Must Have",
                "- Import email from one or more mailboxes using read-only access.",
                "- Categorise emails into client names using sender, domain, subject, contacts, and manual corrections.",
                "- Store attachments under `clients/<client-name>/files/` with duplicate detection.",
                "- Provide a local HTML portal for clients, messages, files, search, and review queue.",
                "- Protect secrets and require portal login.",
                "",
                "## Non-Goals For V1",
                "- Sending email.",
                "- Deleting or moving mailbox messages.",
                "- Cloud sync.",
                "- Multi-tenant client access.",
                "",
            ]
        ),
        vault_dir / "ROADMAP.md": "\n".join(
            [
                "# Roadmap",
                "",
                "**Current phase:** Phase 1 - Project scaffold and security model",
                "",
                "## Phase 1",
                "- [x] Create project and vault scaffold.",
                "- [x] Draft requirements.",
                "- [x] Draft security model.",
                "- [x] Draft initial data model.",
                "- [ ] Confirm mailbox provider and authentication method.",
                "",
                "## Phase 2",
                "- [ ] Build local portal shell.",
                "- [ ] Add mailbox connection settings UI.",
                "- [ ] Add read-only email import prototype.",
                "",
                "## Phase 3",
                "- [ ] Add client categorisation review queue.",
                "- [ ] Add attachment extraction and storage.",
                "- [ ] Add audit log and backup/export.",
                "",
            ]
        ),
        vault_dir / "STATE.md": "\n".join(
            [
                "# State",
                "",
                f"**Last touched:** {datetime.now().date()} - Phase 1 scaffold created by Jarvis Studio",
                "",
                "## Where We Left Off",
                "Phase 1 scaffold is created. Requirements, security model, data model, roadmap, and project context are in place.",
                "",
                "## Next Action",
                "Confirm which mailbox provider to connect first and whether auth should use OAuth or IMAP/app password.",
                "",
            ]
        ),
        vault_dir / "CONTEXT.md": "\n".join(
            [
                "# Context",
                "",
                f"- Project path: `{project_dir}`",
                "- Security doc: `docs/SECURITY.md`",
                "- Data model draft: `docs/DATA_MODEL.md`",
                "- Keep secrets out of git and vault.",
                "- Email import must be read-only until explicitly approved otherwise.",
                "",
            ]
        ),
    }
    for path, content in files.items():
        if _write_if_missing(path, content):
            created.append(str(path))

    project = store.ensure_project(
        project_id,
        title=title,
        repo_root=str(project_dir),
        vault_project=project_id,
    )
    return {"project": project, "project_dir": project_dir, "vault_dir": vault_dir, "created": created}


def _phase_one_email_portal_reply(
    prompt: str,
    store: studio_store.StudioStore,
    chat_id: str,
) -> tuple[str, dict[str, Any]] | None:
    if not _looks_like_phase_one_email_portal_followup(prompt, store, chat_id):
        return None
    result = _create_email_portal_phase1_scaffold(store, chat_id=chat_id)
    created_count = len(result["created"])
    reply = "\n".join(
        [
            "Phase 1 scaffold is created for **Local Email Client Portal**.",
            "",
            f"Project folder: `{result['project_dir']}`",
            f"Vault project: `{result['vault_dir']}`",
            f"Files created: {created_count}",
            "",
            "Created the v1 requirements, roadmap, state, context, security model, and data model draft.",
            "",
            "Next decision:",
            "`Which mailbox should we connect first, and should v1 use OAuth or IMAP/app password?`",
            "",
            "I handled this directly so Jarvis does not burn GPU time thinking about a deterministic scaffold step.",
        ]
    )
    return reply, result


def _looks_like_project_preview_request(prompt: str) -> bool:
    text = " ".join((prompt or "").strip().lower().split())
    if not text or len(text) > 360:
        return False
    return any(term in text for term in ("preview", "show me", "open")) and any(
        term in text for term in ("website", "site", "web page", "homepage")
    )


def _project_preview_reply(prompt: str, project: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    if not _looks_like_project_preview_request(prompt):
        return None
    repo_root = Path(str(project.get("repo_root") or ""))
    if not repo_root:
        return None
    result = project_preview.start_project_preview(repo_root)
    if not result.get("ok"):
        return (
            "I could not start a website preview for this project. "
            f"Reason: {result.get('error') or 'no index.html found'}",
            result,
        )
    url = str(result.get("url") or "")
    return (
        "\n".join(
            [
                "Website preview is running.",
                "",
                f"Open: `{url}`",
                f"Project folder: `{repo_root}`",
                "",
                "I handled this directly so Qwen does not waste time asking for a browser tool.",
            ]
        ),
        result,
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
    repo_root: str | None = None,
) -> str:
    from openjarvis.tools import agent_runner

    agent_runner.start_worker()
    return agent_runner.add_task(
        title=title,
        agent_id=agent_id,
        prompt=prompt,
        project_id=project_id,
        priority=20,
        repo_root=repo_root,
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


def _vault_project_workdir(vault_project: str | None) -> Path | None:
    """Resolve a project's working directory from its vault PROJECT.md.

    Looks for a ``path:`` field in the YAML frontmatter of
    ``Brain/Projects/<vault_project>/PROJECT.md``. This is how a Studio project
    that lives outside the OpenJarvis repo (a client website, a game, etc.)
    tells the Qwen tool bridge where its files actually are. Returns None when
    no folder/frontmatter path is found.
    """
    name = (vault_project or "").strip()
    if not name:
        return None
    try:
        from openjarvis.tools import obsidian_brain

        project_md = obsidian_brain.BRAIN_ROOT / "Projects" / name / "PROJECT.md"
        if not project_md.is_file():
            return None
        text = project_md.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    frontmatter = text[3:end] if end != -1 else ""
    for line in frontmatter.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("path:"):
            raw = stripped.split(":", 1)[1].strip().strip("'\"")
            if raw:
                candidate = Path(raw)
                if candidate.exists():
                    return candidate
    return None


def _words(text: str) -> set[str]:
    return {
        word
        for word in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split()
        if len(word) >= 3
    }


def _vault_project_candidates() -> list[dict[str, Any]]:
    try:
        from openjarvis.tools import obsidian_brain

        projects_root = obsidian_brain.BRAIN_ROOT / "Projects"
    except Exception:
        return []
    if not projects_root.exists():
        return []
    candidates: list[dict[str, Any]] = []
    for project_md in projects_root.glob("*/PROJECT.md"):
        project_id = project_md.parent.name
        try:
            text = project_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        title = project_id
        for line in text.splitlines()[:20]:
            stripped = line.strip()
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip() or title
                break
        workdir = _vault_project_workdir(project_id)
        candidates.append(
            {
                "id": project_id,
                "title": title,
                "repo_root": str(workdir) if workdir is not None else None,
                "keywords": _words(f"{project_id} {title} {text[:1000]}"),
            }
        )
    return candidates


def _infer_project_from_prompt(prompt: str, current_project: dict[str, Any] | None) -> dict[str, Any] | None:
    current_id = str((current_project or {}).get("id") or "")
    prompt_words = _words(prompt)
    if not prompt_words:
        return None
    best: tuple[int, dict[str, Any]] | None = None
    for candidate in _vault_project_candidates():
        if candidate["id"] == current_id:
            continue
        keywords = set(candidate.get("keywords") or set())
        score = len(prompt_words & keywords)
        slug_words = _words(str(candidate.get("id") or ""))
        explicit_slug_match = bool(prompt_words & slug_words)
        if slug_words and slug_words.issubset(prompt_words | keywords) and prompt_words & slug_words:
            score += 4
        if score >= 3 and (current_id == "openjarvis" or explicit_slug_match or score >= 6) and (
            best is None or score > best[0]
        ):
            best = (score, candidate)
    return best[1] if best else None


def _project_repo_root(project: dict[str, Any] | None = None) -> Path:
    # An explicit, non-default repo_root on the project wins.
    if project and project.get("repo_root"):
        explicit = Path(str(project["repo_root"]))
        if explicit.resolve() != studio_store.DEFAULT_REPO_ROOT.resolve():
            return explicit
    # Otherwise, let the vault PROJECT.md path point at the real working dir.
    if project:
        workdir = _vault_project_workdir(project.get("vault_project") or project.get("id"))
        if workdir is not None:
            return workdir
        if project.get("repo_root"):
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


def _agent_registry() -> Any:
    from openjarvis.tools import agent_runner

    return agent_runner._reg


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


def _task_written_file_summary(task: dict[str, Any]) -> str:
    workspace = task.get("workspace")
    if not workspace:
        return ""
    path = Path(str(workspace)) / "FILES_WRITTEN.json"
    if not path.exists() or not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return ""
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, list) or not files:
        return ""
    names = [str(item) for item in files if item][:12]
    if not names:
        return ""
    return "Files written:\n" + "\n".join(f"- `{name}`" for name in names)


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


def _studio_agent_for_request(prompt: str, workflow: str) -> str:
    text = (prompt or "").lower()
    if workflow == "qwen_workflow":
        return "qwen-researcher"
    if any(term in text for term in ("test", "verify", "review", "audit", "check")):
        return "qwen-tester"
    if any(
        term in text
        for term in (
            "build",
            "create",
            "make",
            "implement",
            "code",
            "website",
            "web page",
            "landing page",
            "app",
            "application",
            "component",
            "script",
            "portal",
        )
    ):
        return "qwen-builder"
    return "qwen-planner"


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


def _run_ecc_lite_skills(run: dict[str, Any]) -> list[str]:
    for event in run.get("events") or []:
        if not isinstance(event, dict) or event.get("type") != "run.task_queued":
            continue
        details = event.get("data") if isinstance(event.get("data"), dict) else event.get("details")
        details = details if isinstance(details, dict) else {}
        skills = details.get("ecc_lite_skills") if isinstance(details, dict) else []
        if isinstance(skills, list):
            return [str(skill) for skill in skills if skill]
    return []


def _run_ecc_command(run: dict[str, Any]) -> str:
    for event in run.get("events") or []:
        if not isinstance(event, dict) or event.get("type") != "run.task_queued":
            continue
        details = event.get("data") if isinstance(event.get("data"), dict) else event.get("details")
        details = details if isinstance(details, dict) else {}
        command = details.get("ecc_command") if isinstance(details, dict) else ""
        if command:
            return str(command)
    return ""


def _run_selected_skills(run: dict[str, Any]) -> list[str]:
    stored = run.get("selected_skills")
    if isinstance(stored, list):
        return [str(skill) for skill in stored if skill]
    for event in run.get("events") or []:
        if not isinstance(event, dict) or event.get("type") != "run.task_queued":
            continue
        details = event.get("data") if isinstance(event.get("data"), dict) else event.get("details")
        details = details if isinstance(details, dict) else {}
        skills = details.get("selected_skills") if isinstance(details, dict) else []
        if isinstance(skills, list):
            return [str(skill) for skill in skills if skill]
    return []


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
        copy["ecc_lite_skills"] = _run_ecc_lite_skills(copy)
        copy["ecc_command"] = _run_ecc_command(copy)
        copy["selected_skills"] = _run_selected_skills(copy)
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
    task_index: dict[str, dict[str, Any]] | None = None,
) -> None:
    # Stop the run spinner, but DON'T force-finish a task that is still
    # actively running — it will complete (and possibly escalate) on its own.
    # Force-marking an in-flight task wrote a false 'failed' outcome and raced
    # the worker thread, which silently dropped escalations (found live
    # 2026-06-13). Only reap tasks that are already terminal-but-unsynced.
    if task_index is None:
        task_index = _load_agent_task_index()
    try:
        registry = _agent_registry()
        for task_id in task_ids:
            task = task_index.get(task_id)
            if task and str(task.get("status")) == "running":
                continue  # leave it; its real completion + escalation stand
            registry.mark_finished(
                task_id,
                -1,
                f"Studio run timed out after {STUDIO_RUN_STALE_AFTER_SECONDS}s.",
            )
    except Exception:
        pass
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


def cancel_studio_run(run_id: str, store: studio_store.StudioStore | None = None) -> dict[str, Any]:
    """Cancel a Studio run and suppress any late background result from the chat."""
    from openjarvis.tools import agent_runner

    store = store or studio_store.StudioStore()
    run = store.get_run(run_id)
    task_ids = [str(task_id) for task_id in run.get("tasks", []) if task_id]
    for task_id in task_ids:
        try:
            agent_runner.cancel_running_task(task_id)
        except Exception:
            pass
        try:
            agent_runner.cancel_task(task_id)
        except Exception:
            pass

    updated = store.get_run(run_id)
    updated["status"] = "cancelled"
    updated["cancelled"] = True
    updated["cancelled_at"] = studio_store.utc_now()
    updated["updated_at"] = updated["cancelled_at"]
    store._write_json(store._run_path(updated["id"]), updated)
    store.append_run_event(
        updated["id"],
        "run.cancelled",
        "Studio run cancelled by operator",
        {"tasks": task_ids},
    )
    updated = store.get_run(run_id)
    if not _chat_has_result_message(store, updated["chat_id"], updated["id"]):
        store.add_message(
            updated["chat_id"],
            "jarvis",
            "Cancelled the running Studio task. I will ignore any late output from that run.",
            run_id=updated["id"],
        )
    return {"ok": True, "run": store.get_run(run_id), "tasks": task_ids}


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
                _mark_studio_run_timed_out(store, run, task_ids, task_index)
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
            artifact_parts = [_task_written_file_summary(task) for task in tasks if task]
            result_text = "\n\n".join(part for part in result_parts if part).strip()
            artifact_text = "\n\n".join(part for part in artifact_parts if part).strip()
            if result_text and artifact_text:
                result_text = f"{result_text}\n\n{artifact_text}"
            elif artifact_text:
                result_text = artifact_text
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
    selected_skills: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    store = studio_store.StudioStore()
    projects = {p["id"]: p for p in store.list_projects()}
    project = projects.get(project_id) or store.ensure_project(
        project_id,
        title=project_id,
    )
    inferred_project = _infer_project_from_prompt(prompt, project)
    if inferred_project is not None:
        project_id = str(inferred_project["id"])
        project = store.ensure_project(
            project_id,
            title=str(inferred_project.get("title") or project_id),
            repo_root=str(inferred_project.get("repo_root") or studio_store.DEFAULT_REPO_ROOT),
            vault_project=project_id,
        )
    selected_skill_ids = _normalize_selected_skills(selected_skills, prompt)
    decision = studio_workflows.select_workflow(prompt)
    run = store.create_run(project_id, chat_id, prompt, workflow=decision["workflow"])
    if selected_skill_ids:
        run["selected_skills"] = selected_skill_ids
        store._write_json(store._run_path(run["id"]), run)
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
    phase_one_reply = _phase_one_email_portal_reply(prompt, store, chat_id)
    if phase_one_reply:
        reply, scaffold = phase_one_reply
        run = _persist_run_status(store, store.get_run(run["id"]), "completed")
        store.append_run_event(
            run["id"],
            "run.completed",
            "Created phase 1 project scaffold directly",
            {
                "mode": "phase1_project_scaffold",
                "project_id": scaffold["project"]["id"],
                "project_dir": str(scaffold["project_dir"]),
                "vault_dir": str(scaffold["vault_dir"]),
                "created": scaffold["created"],
            },
        )
        return {
            "run": store.get_run(run["id"]),
            "context": {"ok": True, "markdown": "", "warnings": []},
            "research": {"ok": False, "markdown": ""},
            "decision": {
                **decision,
                "workflow": "phase1_project_scaffold",
                "reason": "Phase 1 scaffold created from the prior Studio project brief without queuing Qwen.",
                "verification": {"required": False, "method": "file scaffold"},
                "next_steps": ["Confirm mailbox auth method.", "Build local portal shell.", "Add read-only importer."],
            },
            "reply": reply,
        }
    preview_reply = _project_preview_reply(prompt, project)
    if preview_reply:
        reply, preview = preview_reply
        run = _persist_run_status(store, store.get_run(run["id"]), "completed")
        store.append_run_event(
            run["id"],
            "run.completed",
            "Started project preview directly",
            {"mode": "project_preview", "preview": preview},
        )
        return {
            "run": store.get_run(run["id"]),
            "context": {"ok": True, "markdown": "", "warnings": []},
            "research": {"ok": False, "markdown": ""},
            "decision": {
                **decision,
                "workflow": "project_preview",
                "reason": "Website preview request handled by Studio preview server without queuing Qwen.",
                "verification": {"required": False, "method": "localhost preview"},
                "next_steps": ["Open the preview URL.", "Request visual changes if needed."],
            },
            "reply": reply,
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
    continuation_reply = _project_continuation_reply(prompt, context_pack)
    if continuation_reply:
        run = _persist_run_status(store, store.get_run(run["id"]), "completed")
        store.append_run_event(
            run["id"],
            "run.completed",
            "Answered project continuation from vault state",
            {"mode": "project_continuation"},
        )
        return {
            "run": store.get_run(run["id"]),
            "context": context_pack,
            "research": {"ok": False, "markdown": ""},
            "decision": {
                **decision,
                "workflow": "project_continuation",
                "reason": "Project continuation answered from vault state without a background agent run.",
                "verification": {"required": False, "method": "vault project state"},
                "next_steps": [],
            },
            "reply": continuation_reply,
        }
    new_project_reply = _new_project_planning_reply(prompt)
    if new_project_reply:
        run = _persist_run_status(store, store.get_run(run["id"]), "completed")
        store.append_run_event(
            run["id"],
            "run.completed",
            "Answered new project planning prompt directly",
            {"mode": "new_project_brief"},
        )
        return {
            "run": store.get_run(run["id"]),
            "context": context_pack,
            "research": {"ok": False, "markdown": ""},
            "decision": {
                **decision,
                "workflow": "new_project_brief",
                "reason": "New project planning prompt answered directly before queuing a background agent.",
                "verification": {"required": False, "method": "structured project brief"},
                "next_steps": ["Create canonical project files.", "Confirm security model.", "Build v1 scaffold."],
            },
            "reply": new_project_reply,
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

    agent_id = _studio_agent_for_request(prompt, decision["workflow"])
    ecc_lite_skills = _select_ecc_lite_skills(prompt, decision["workflow"])
    ecc_command = _select_ecc_command(prompt, decision["workflow"])
    ecc_guidance = _build_ecc_lite_guidance(prompt, decision["workflow"])
    selected_skill_guidance = _build_selected_skill_guidance(selected_skill_ids)
    task_prompt = (
        f"{context_pack.get('markdown', '')}\n\n"
        f"{research_pack.get('markdown', '')}\n\n"
        f"{selected_skill_guidance}\n\n"
        f"{ecc_guidance}\n\n"
        f"Operator request:\n{prompt}\n\n"
        "Return concrete progress, blockers, and verification needed."
    )
    task_id = _queue_agent_task(
        title=f"Studio: {prompt[:80]}",
        agent_id=agent_id,
        prompt=task_prompt,
        project_id=f"studio-{project_id}",
        repo_root=str(_project_repo_root(project)),
    )
    run = store.get_run(run["id"])
    run.setdefault("tasks", []).append(task_id)
    run = _persist_run_status(store, run, "running")
    store.append_run_event(
        run["id"],
        "run.task_queued",
        f"Queued {agent_id}",
        {
            "task_id": task_id,
            "agent_id": agent_id,
            "selected_skills": selected_skill_ids,
            "ecc_lite_skills": ecc_lite_skills,
            "ecc_command": ecc_command,
        },
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

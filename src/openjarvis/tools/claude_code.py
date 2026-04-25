"""Claude Code integration — J.A.R.V.I.S. as a software developer.

Detects voice coding requests, delegates them to the Claude Code CLI
(``claude -p``) running in a dedicated workspace folder, and notifies
the user when the job finishes via the UI command queue.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Explicit triggers (ordered longest-first for the prefix match to prefer the
# most specific phrase). Keep these conservative to avoid false positives on
# chit-chat.
CODE_TRIGGERS = (
    "write me a script that ",
    "write me a program that ",
    "build me a script that ",
    "build me a program that ",
    "make me a script that ",
    "make me a program that ",
    "create me a script that ",
    "create me a program that ",
    "write a script that ",
    "write a program that ",
    "write a script to ",
    "build a script that ",
    "build a program that ",
    "create a script that ",
    "create a program that ",
    "make a script that ",
    "make a program that ",
    "write code that ",
    "write code to ",
    "code something that ",
)

WORKSPACE_ROOT = Path.home() / ".openjarvis" / "code_projects"


# ---------------------------------------------------------------------------
# Job state
# ---------------------------------------------------------------------------


@dataclass
class CodeJob:
    job_id: str
    slug: str
    workspace: Path
    prompt: str
    started_at: float
    process: Optional[subprocess.Popen] = field(default=None)
    thread: Optional[threading.Thread] = field(default=None)
    state: str = "queued"  # queued | running | done | failed


_ACTIVE_JOBS: list[CodeJob] = []
_JOBS_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_trigger(text: str) -> Optional[str]:
    """Return the prompt (the part after the trigger) or None if not a match."""
    lower = text.lower().strip()
    # Sort by length desc so longer phrases win over shorter prefixes
    for trig in sorted(CODE_TRIGGERS, key=len, reverse=True):
        if lower.startswith(trig):
            return text.strip()[len(trig):].strip() or None
    return None


def _slugify(prompt: str, max_len: int = 40) -> str:
    """Turn a free-text prompt into a filesystem-safe slug."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", prompt).strip("-").lower()
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s or "job"


def _create_workspace(slug: str) -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    ws = WORKSPACE_ROOT / f"{ts}_{slug}"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _write_status(job: CodeJob, state: str, **extra) -> None:
    status = {
        "job_id": job.job_id,
        "slug": job.slug,
        "state": state,
        "started_at": job.started_at,
        "ended_at": time.time() if state in ("done", "failed") else None,
        "prompt": job.prompt,
        **extra,
    }
    try:
        (job.workspace / "status.json").write_text(
            json.dumps(status, indent=2), encoding="utf-8"
        )
    except Exception:
        logger.exception("Failed to write status.json for job %s", job.job_id)


def _resolve_claude_exe() -> Optional[str]:
    """Find the Claude Code CLI executable."""
    exe = shutil.which("claude")
    if exe:
        return exe
    # Common Windows fallback paths
    candidates = [
        Path.home() / ".bun" / "bin" / "claude.exe",
        Path.home() / ".bun" / "bin" / "claude",
        Path("C:/Users/User/.bun/bin/claude.exe"),
        Path("C:/Users/User/.bun/bin/claude"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def _slug_to_words(slug: str) -> str:
    return slug.replace("-", " ")


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------


def _run_job(job: CodeJob, ui, console) -> None:
    """Run the Claude Code subprocess to completion. Runs in a daemon thread."""
    try:
        exe = _resolve_claude_exe()
        if exe is None:
            job.state = "failed"
            _write_status(job, "failed", error="claude CLI not found on PATH")
            _notify(
                ui,
                console,
                "I couldn't find the Claude Code CLI on your system, sir.",
            )
            return

        job.state = "running"
        _write_status(job, "running")

        stdout_path = job.workspace / "claude_stdout.log"
        stderr_path = job.workspace / "claude_stderr.log"

        popen_kwargs: dict = {
            "cwd": str(job.workspace),
            "shell": False,
        }
        if sys.platform == "win32":
            CREATE_NO_WINDOW = 0x08000000
            popen_kwargs["creationflags"] = CREATE_NO_WINDOW

        with stdout_path.open("wb") as out_f, stderr_path.open("wb") as err_f:
            proc = subprocess.Popen(
                [
                    exe,
                    "-p",
                    "--dangerously-skip-permissions",
                    job.prompt,
                ],
                stdout=out_f,
                stderr=err_f,
                **popen_kwargs,
            )
            job.process = proc
            exit_code = proc.wait()

        if exit_code == 0:
            job.state = "done"
            _write_status(job, "done", exit_code=exit_code)
            folder_name = job.workspace.name
            msg = (
                f"Done, sir. Your project {_slug_to_words(job.slug)} is ready. "
                f"You'll find it in code projects under {folder_name}."
            )
        else:
            job.state = "failed"
            _write_status(job, "failed", exit_code=exit_code)
            folder_name = job.workspace.name
            msg = (
                f"I ran into trouble with {_slug_to_words(job.slug)}, sir. "
                f"Exit code {exit_code}. The logs are in {folder_name}."
            )

        _notify(ui, console, msg)

    except Exception as exc:
        logger.exception("Code job %s failed", job.job_id)
        job.state = "failed"
        _write_status(job, "failed", error=str(exc))
        _notify(
            ui,
            console,
            f"I'm afraid the coding task failed to launch, sir. {exc}",
        )


def _notify(ui, console, message: str) -> None:
    """Send a completion message back to the main voice loop (or fall back to console)."""
    if ui is not None and hasattr(ui, "post_command"):
        try:
            ui.post_command(f"__SAY__:{message}")
            return
        except Exception:
            logger.exception("Failed to post completion to UI queue")
    try:
        console.print(f"[green]{message}[/green]")
    except Exception:
        print(message)


# ---------------------------------------------------------------------------
# Fast-path entry point
# ---------------------------------------------------------------------------


def _try_code(text: str, console, ui=None) -> Optional[str]:
    """Voice-loop fast-path: detect and dispatch a coding request.

    Returns the immediate spoken ack string on a match, or ``None`` if the
    request is not a coding command (so the voice loop falls through).
    """
    prompt = _strip_trigger(text)
    if not prompt:
        return None

    # v1: one job at a time
    with _JOBS_LOCK:
        running = [j for j in _ACTIVE_JOBS if j.state == "running"]

    if running:
        current = running[-1]
        return (
            f"I'm still working on the previous task, sir — "
            f"{_slug_to_words(current.slug)}. Please wait for it to finish."
        )

    slug = _slugify(prompt)
    try:
        workspace = _create_workspace(slug)
    except Exception as exc:
        logger.exception("Failed to create workspace")
        return f"I couldn't create a workspace, sir. {exc}"

    try:
        (workspace / "prompt.txt").write_text(prompt, encoding="utf-8")
    except Exception:
        logger.exception("Failed to write prompt.txt")

    job = CodeJob(
        job_id=uuid.uuid4().hex[:8],
        slug=slug,
        workspace=workspace,
        prompt=prompt,
        started_at=time.time(),
        state="queued",
    )
    _write_status(job, "queued")

    with _JOBS_LOCK:
        _ACTIVE_JOBS.append(job)

    thread = threading.Thread(
        target=_run_job,
        args=(job, ui, console),
        daemon=True,
        name=f"claude-code-{job.job_id}",
    )
    job.thread = thread
    thread.start()

    try:
        console.print(
            f"[cyan]Claude Code: launched job {job.job_id} in {workspace}[/cyan]"
        )
    except Exception:
        pass

    short = prompt if len(prompt) < 60 else prompt[:57] + "..."
    return f"I'm on it, sir. Starting work on {short}."


__all__ = [
    "CODE_TRIGGERS",
    "CodeJob",
    "WORKSPACE_ROOT",
    "_try_code",
    "_strip_trigger",
    "_slugify",
]

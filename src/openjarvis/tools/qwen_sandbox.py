"""Disposable sandbox check runner for the Qwen autonomy upgrade (v2).

This is what lets local Qwen *execute its own proposed change and react to the
real failure* instead of only grading its own prose. Qwen proposes file
contents; this module copies the project into a throwaway directory, writes the
proposed files there, runs an operator-declared, allow-listed check command, and
returns the real exit code + output. Qwen can then call it again with a revised
proposal until the check passes.

Safety model (deliberately split so Qwen never gets free shell):
  * Qwen controls *what to write* (the proposed file contents) — never the
    command. Proposed paths are validated (no abs/drive/``..``/secret-like).
  * The operator controls *what runs*: the check command is read from
    ``<repo_root>/.jarvis-verify.txt`` (one argv line). If absent, we refuse.
  * The command's executable must be on ``ALLOWED_EXECUTABLES``. Commands run
    via argv (never ``shell=True``), in the disposable copy only, with a wall
    timeout and bounded captured output. Real project files are never touched.

Honest limit: this slice does NOT hard network-isolate the subprocess (Windows
has no cheap per-process network jail without a container). Mitigations are the
executable allow-list, no-shell argv, the disposable copy, and the timeout. A
container/Job-object network jail is a future hardening step.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# First argv token (by basename, lowercased, extension stripped) must be one of
# these. Conservative on purpose — extend deliberately, never from model input.
ALLOWED_EXECUTABLES = {
    "python",
    "python3",
    "py",
    "pytest",
    "node",
    "npm",
    "pnpm",
    "yarn",
    "tsc",
    "eslint",
}

# Heavy / regenerable / secret directories we never copy into the sandbox, both
# to bound copy cost and to keep secrets out of the disposable tree.
_IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".uv-cache",
    ".codegraph",
    "dist",
    "build",
    "out",
    ".next",
    ".cache",
    "models",
    # Unreal Engine regenerables
    "Binaries",
    "Intermediate",
    "DerivedDataCache",
    "Saved",
}

_SECRET_NAMES = ("jarvis.bat", ".env", "secret", "token", "key", ".pem", ".pfx", ".p12")

MAX_SANDBOX_FILES = 10_000  # refuse to copy absurdly large trees
DEFAULT_TIMEOUT_S = 120
MAX_OUTPUT_CHARS = 20_000


def load_verify_command(repo_root: Path) -> list[str] | None:
    """Read the operator-declared check command from ``.jarvis-verify.txt``.

    One command per line is supported; the first non-empty, non-comment line is
    used. Returns the argv list, or None if no declaration exists.
    """
    decl = Path(repo_root) / ".jarvis-verify.txt"
    if not decl.is_file():
        return None
    for raw in decl.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        argv = shlex.split(line, posix=False)
        return [a.strip('"') for a in argv] if argv else None
    return None


def _executable_allowed(argv: list[str]) -> bool:
    if not argv:
        return False
    exe = Path(argv[0]).name.lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if exe.endswith(suffix):
            exe = exe[: -len(suffix)]
    return exe in ALLOWED_EXECUTABLES


def _resolve_command(argv: list[str]) -> list[str]:
    """Run generic Python declarations through Jarvis's current interpreter."""
    if not argv:
        return argv
    exe = Path(argv[0]).name.lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if exe.endswith(suffix):
            exe = exe[: -len(suffix)]
    if exe in {"python", "python3", "py"}:
        return [sys.executable, *argv[1:]]
    return argv


def _safe_rel(raw_path: str) -> str | None:
    rel = str(raw_path or "").replace("\\", "/").strip()
    if not rel or rel.startswith("/") or ":" in rel or ".." in Path(rel).parts:
        return None
    low = rel.lower()
    if any(token in low for token in _SECRET_NAMES):
        return None
    return rel


def _ignore(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n in _IGNORE_DIRS}


def run_check_in_sandbox(
    repo_root: str | Path,
    files: list[dict[str, Any]],
    *,
    timeout: int = DEFAULT_TIMEOUT_S,
    max_output: int = MAX_OUTPUT_CHARS,
) -> dict[str, Any]:
    """Copy ``repo_root`` to a temp dir, write ``files``, run the declared check.

    ``files`` is a list of ``{"path": rel, "content": str}`` (same shape as a
    repo_patch_proposal). Returns a dict with ``ok`` (the run completed),
    ``passed`` (exit code 0), ``exit_code``, ``stdout``, ``stderr``, and the
    ``command`` that was run. On a configuration/safety problem returns
    ``ok=False`` with ``error`` (and ``blocked=True`` for safety refusals).
    """
    root = Path(repo_root).resolve()
    if not root.is_dir():
        return {"ok": False, "error": "repo root not found", "repo_root": str(root)}

    command = load_verify_command(root)
    if command is None:
        return {
            "ok": False,
            "error": "no verify command declared; create .jarvis-verify.txt in the project root",
        }
    if not _executable_allowed(command):
        return {
            "ok": False,
            "blocked": True,
            "error": f"check executable not allow-listed: {command[0]}",
        }
    command = _resolve_command(command)

    # Validate every proposed file path before doing any work.
    clean_files: list[tuple[str, str]] = []
    for item in files or []:
        if not isinstance(item, dict):
            continue
        rel = _safe_rel(str(item.get("path") or ""))
        if rel is None:
            return {"ok": False, "blocked": True, "error": f"unsafe path: {item.get('path')!r}"}
        content = item.get("content")
        if not isinstance(content, str):
            return {"ok": False, "error": f"content required for {rel}"}
        clean_files.append((rel, content))

    # Bound the copy: refuse absurdly large trees.
    file_count = 0
    for _p in root.rglob("*"):
        if any(part in _IGNORE_DIRS for part in _p.parts):
            continue
        file_count += 1
        if file_count > MAX_SANDBOX_FILES:
            return {
                "ok": False,
                "error": f"project too large to sandbox (>{MAX_SANDBOX_FILES} files); narrow the project or add ignores",
            }

    tmp = Path(tempfile.mkdtemp(prefix="jarvis-verify-"))
    sandbox = tmp / root.name
    try:
        shutil.copytree(root, sandbox, ignore=_ignore, dirs_exist_ok=True)
        for rel, content in clean_files:
            target = (sandbox / rel).resolve()
            try:
                target.relative_to(sandbox.resolve())
            except ValueError:
                return {"ok": False, "blocked": True, "error": f"path escapes sandbox: {rel}"}
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        try:
            proc = subprocess.run(  # noqa: S603 - argv only, no shell, allow-listed exe
                command,
                cwd=str(sandbox),
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error": f"check timed out after {timeout}s",
                "command": command,
                "timed_out": True,
            }
        except FileNotFoundError:
            return {
                "ok": False,
                "error": f"check executable not found on PATH: {command[0]}",
                "command": command,
            }

        return {
            "ok": True,
            "passed": proc.returncode == 0,
            "exit_code": proc.returncode,
            "command": command,
            "stdout": (proc.stdout or "")[-max_output:],
            "stderr": (proc.stderr or "")[-max_output:],
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

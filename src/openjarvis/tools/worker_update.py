"""Worker-node self update runner."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from openjarvis.tools.node_identity import load_node_identity


CommandRunner = Callable[[str], tuple[int, str, str]]


def run_worker_update(
    *,
    script_path: Path | str | None = None,
    runner: CommandRunner | None = None,
) -> dict:
    node = load_node_identity()
    if not node["is_worker"]:
        return {
            "ok": False,
            "blocked": True,
            "error": "Worker update can only run on a Jarvis worker node.",
            "node": node,
        }

    script = Path(script_path or _default_script_path()).resolve()
    if not script.exists():
        return {
            "ok": False,
            "blocked": False,
            "error": f"worker update script not found: {script}",
            "node": node,
        }

    command = f'powershell.exe -ExecutionPolicy Bypass -File "{script}"'
    code, stdout, stderr = (runner or _run_command)(command)
    return {
        "ok": code == 0,
        "exit_code": code,
        "stdout": stdout[-4000:],
        "stderr": stderr[-4000:],
        "node": node,
        "script": str(script),
    }


def _default_script_path() -> Path:
    return Path(__file__).resolve().parents[3] / "scripts" / "update-worker-node.ps1"


def _run_command(command: str) -> tuple[int, str, str]:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=180,
        shell=True,
    )
    return completed.returncode, completed.stdout or "", completed.stderr or ""

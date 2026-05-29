"""Backend supervisor for the Jarvis desktop app (Phase 5, v1).

Borrows the one genuinely reusable idea from upstream's Tauri ``BackendManager``:
the desktop shell can start the backend stack if it isn't already up, wait for
it to become healthy, and (optionally) stop what *it* started on exit.

Unlike upstream (which runs `uv run jarvis serve` on :8000 + clones the repo),
this drives YOUR existing launcher (`jarvis.bat`, which starts brain_server on
7710 + LiteLLM + the Qwen WSL/MTP lane). We never embed secrets — jarvis.bat
keeps reading them itself.

Pure decision logic is dependency-free and unit-tested; process spawning is
injected so tests don't launch anything.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from openjarvis.desktop.app import DEFAULT_HOST  # noqa: F401  (kept for parity/imports)
from openjarvis.desktop.app import HealthCheck, backend_ready, wait_for_backend

DEFAULT_LAUNCHER = Path(r"E:\Claude\OpenJarvis\jarvis.bat")

Spawn = Callable[[str], Any]


def _default_spawn(launcher: str):
    """Start the launcher in its own working dir. On Windows a .bat needs the
    shell; the stack detaches into its own minimised windows as designed."""
    return subprocess.Popen(  # noqa: S602 - operator-owned launcher path, not model input
        launcher,
        shell=True,
        cwd=str(Path(launcher).parent),
    )


class BackendSupervisor:
    def __init__(
        self,
        *,
        launcher: str | None = None,
        health_check: HealthCheck | None = None,
        spawn: Spawn | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._launcher = launcher
        self._health_check = health_check
        self._spawn = spawn or _default_spawn
        self._sleep = sleep
        self._clock = clock
        self._proc: Any | None = None  # only set to what *we* started

    def resolved_launcher(self) -> str | None:
        """The launcher to use: explicit arg > env override > default jarvis.bat
        (only if it exists). None when nothing is available to start."""
        if self._launcher:
            return self._launcher
        env = os.environ.get("OPENJARVIS_DESKTOP_LAUNCHER")
        if env:
            return env
        return str(DEFAULT_LAUNCHER) if DEFAULT_LAUNCHER.exists() else None

    def is_ready(self) -> bool:
        return backend_ready(health_check=self._health_check)

    def ensure_running(self, *, wait_timeout_s: float = 60.0) -> dict[str, Any]:
        """Make the backend healthy: no-op if already up, else start the
        launcher and wait. Returns ``{ready, started, reason?/launcher?}``."""
        if self.is_ready():
            return {"ready": True, "started": False, "reason": "already running"}
        launcher = self.resolved_launcher()
        if not launcher:
            return {"ready": False, "started": False, "reason": "no launcher configured"}
        self._proc = self._spawn(launcher)
        ready = wait_for_backend(
            timeout_s=wait_timeout_s,
            health_check=self._health_check,
            sleep=self._sleep,
            clock=self._clock,
        )
        return {"ready": ready, "started": True, "launcher": launcher}

    def started_backend(self) -> bool:
        return self._proc is not None

    def stop(self) -> bool:
        """Best-effort stop of the launcher process WE started. Returns False if
        we didn't start anything. Note: jarvis.bat spawns detached child windows
        this cannot fully reap — documented limitation, hence stop-on-exit is
        opt-in in launch()."""
        if self._proc is None:
            return False
        proc = self._proc
        self._proc = None
        try:
            proc.terminate()
        except Exception:
            return False
        return True

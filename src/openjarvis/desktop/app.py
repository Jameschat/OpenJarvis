"""Jarvis desktop app v0 — open Studio in a native WebView2 window.

Pure helpers (URL resolution, backend-readiness wait) are dependency-free and
unit-tested. The actual window uses ``pywebview`` which is lazy-imported so the
rest of the package (and the test suite) does not require it. Install with::

    uv pip install pywebview

Run with::

    python -m openjarvis.desktop            # or scripts/jarvis-app.ps1
"""

from __future__ import annotations

import time
from typing import Any, Callable

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7710
DEFAULT_PATH = "/studio"
WINDOW_TITLE = "J.A.R.V.I.S."

HealthCheck = Callable[[], dict[str, Any]]


def resolve_studio_url(
    host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, path: str = DEFAULT_PATH
) -> str:
    """Build the local Studio URL the desktop window points at."""
    host = (host or DEFAULT_HOST).strip()
    path = path or DEFAULT_PATH
    if not path.startswith("/"):
        path = "/" + path
    return f"http://{host}:{int(port)}{path}"


def _default_health_check() -> dict[str, Any]:
    from openjarvis.tools.runtime_health import check_runtime_health

    return check_runtime_health()


def backend_ready(*, health_check: HealthCheck | None = None) -> bool:
    """True when the required Jarvis backend services are reachable."""
    check = health_check or _default_health_check
    try:
        status = check()
    except Exception:
        return False
    return bool(status.get("ok"))


def wait_for_backend(
    *,
    timeout_s: float = 30.0,
    interval_s: float = 1.0,
    health_check: HealthCheck | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Poll backend readiness until ready or ``timeout_s`` elapses.

    ``sleep``/``clock`` are injectable so the loop is unit-testable without real
    time passing.
    """
    deadline = clock() + max(0.0, timeout_s)
    while True:
        if backend_ready(health_check=health_check):
            return True
        if clock() >= deadline:
            return False
        sleep(interval_s)


def launch(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    title: str = WINDOW_TITLE,
    wait_timeout_s: float = 30.0,
    health_check: HealthCheck | None = None,
) -> int:
    """Wait for the backend, then open Studio in a native WebView2 window.

    Returns a process exit code. 0 = window opened/closed normally; 2 = pywebview
    not installed (instructions printed). The window still opens even if the
    backend is not yet ready — Studio shows its own runtime-readiness screen.
    """
    url = resolve_studio_url(host, port)
    ready = wait_for_backend(timeout_s=wait_timeout_s, health_check=health_check)
    if not ready:
        print(
            "Jarvis backend not detected yet — opening anyway; "
            "Studio will show a runtime-readiness screen. "
            "Start it with jarvis.bat if it stays offline."
        )

    try:
        import webview  # lazy: optional dependency
    except ImportError:
        print("pywebview is not installed. Install it with:  uv pip install pywebview")
        print(f"Meanwhile you can open {url} in a browser.")
        return 2

    webview.create_window(
        title,
        url,
        width=1440,
        height=920,
        min_size=(1024, 700),
    )
    webview.start()
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="jarvis-app",
        description="Launch the Jarvis desktop app (Studio in a native window).",
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--wait",
        type=float,
        default=30.0,
        help="seconds to wait for backend readiness before opening the window",
    )
    args = parser.parse_args(argv)
    return launch(host=args.host, port=args.port, wait_timeout_s=args.wait)

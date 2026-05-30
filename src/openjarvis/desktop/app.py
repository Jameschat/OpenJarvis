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


class DesktopApi:
    """JS-callable API exposed to the boot screen (``window.pywebview.api``)."""

    def __init__(
        self,
        studio_url: str,
        *,
        health_check: HealthCheck | None = None,
        supervisor: Any | None = None,
    ) -> None:
        self._studio_url = studio_url
        self._health_check = health_check
        self._supervisor = supervisor

    def studio_url(self) -> str:
        return self._studio_url

    def ready(self) -> bool:
        return backend_ready(health_check=self._health_check)

    def start_backend(self) -> dict[str, Any]:
        if self._supervisor is None:
            return {"started": False, "reason": "supervisor not available"}
        return self._supervisor.ensure_running(wait_timeout_s=120)


def acquire_single_instance(port: int = 48707):
    """Best-effort single-instance guard: bind a localhost TCP port. Returns the
    bound socket (keep a reference alive for the app's lifetime) if we are the
    only instance, or None if another instance already holds it."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        sock.bind(("127.0.0.1", int(port)))
        sock.listen(1)
        return sock
    except OSError:
        sock.close()
        return None


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
    start_backend: bool = False,
    stop_backend_on_exit: bool = False,
    single_instance: bool = True,
) -> int:
    """Open Jarvis Studio in a native WebView2 window.

    Returns a process exit code. 0 = normal; 2 = pywebview not installed; 3 =
    another instance already running. If the backend is healthy the window opens
    directly on Studio; otherwise it opens a branded **readiness boot screen**
    that polls for the backend, lets the user start it, and navigates to Studio
    once healthy.

    ``start_backend`` proactively starts the jarvis.bat stack via a supervisor;
    ``stop_backend_on_exit`` stops what the supervisor started on close.
    """
    url = resolve_studio_url(host, port)

    _instance_lock = None
    if single_instance:
        _instance_lock = acquire_single_instance()
        if _instance_lock is None:
            print("Jarvis desktop app is already running.")
            return 3

    # A supervisor is always available so the boot screen's "Start backend"
    # button works; we only auto-start when asked.
    from openjarvis.desktop.backend import BackendSupervisor

    supervisor = BackendSupervisor(health_check=health_check)
    if start_backend:
        outcome = supervisor.ensure_running(wait_timeout_s=wait_timeout_s)
        ready = bool(outcome.get("ready"))
    else:
        ready = wait_for_backend(timeout_s=wait_timeout_s, health_check=health_check)

    try:
        import webview  # lazy: optional dependency
    except ImportError:
        print("pywebview is not installed. Install it with:  uv pip install pywebview")
        print(f"Meanwhile you can open {url} in a browser.")
        return 2

    api = DesktopApi(url, health_check=health_check, supervisor=supervisor)
    if ready:
        window = webview.create_window(
            title, url, width=1440, height=920, min_size=(1024, 700),
            text_select=True,  # allow selecting/copying text from Studio
        )
    else:
        from openjarvis.desktop.boot import boot_html

        window = webview.create_window(
            title,
            html=boot_html(url, title=title),
            js_api=api,
            width=1440,
            height=920,
            min_size=(1024, 700),
            text_select=True,
        )

    from openjarvis.desktop.menu import MenuController, start_with_menu

    controller = MenuController(studio_url=url, api=api, get_window=lambda: window)
    try:
        start_with_menu(webview, controller)
    finally:
        if stop_backend_on_exit and supervisor.started_backend():
            supervisor.stop()
        if _instance_lock is not None:
            try:
                _instance_lock.close()
            except Exception:
                pass
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
    parser.add_argument(
        "--start-backend",
        action="store_true",
        help="start the jarvis.bat stack if it isn't already healthy",
    )
    parser.add_argument(
        "--stop-backend-on-exit",
        action="store_true",
        help="stop the backend we started when the window closes (needs --start-backend)",
    )
    parser.add_argument(
        "--no-single-instance",
        action="store_true",
        help="allow more than one desktop window to run at once",
    )
    args = parser.parse_args(argv)
    return launch(
        host=args.host,
        port=args.port,
        wait_timeout_s=args.wait,
        start_backend=args.start_backend,
        stop_backend_on_exit=args.stop_backend_on_exit,
        single_instance=not args.no_single_instance,
    )

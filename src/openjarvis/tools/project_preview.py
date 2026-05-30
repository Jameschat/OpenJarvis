"""Safe local static preview servers for Studio projects."""

from __future__ import annotations

import functools
import http.server
import socket
import threading
from pathlib import Path
from typing import Any


_SERVERS: dict[Path, dict[str, Any]] = {}
_LOCK = threading.Lock()


def start_project_preview(root: Path | str, *, preferred_port: int = 8127) -> dict[str, Any]:
    project_root = Path(root).resolve()
    if not project_root.exists() or not project_root.is_dir():
        return {"ok": False, "error": f"project root not found: {project_root}"}
    entry = project_root / "index.html"
    if not entry.exists() or not entry.is_file():
        return {"ok": False, "error": f"project preview requires index.html in {project_root}"}

    with _LOCK:
        existing = _SERVERS.get(project_root)
        if existing and _port_open(int(existing["port"])):
            return dict(existing)

        port = _find_free_port(preferred_port)
        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler,
            directory=str(project_root),
        )
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
        thread = threading.Thread(
            target=server.serve_forever,
            name=f"jarvis-preview-{port}",
            daemon=True,
        )
        thread.start()
        preview = {
            "ok": True,
            "url": f"http://127.0.0.1:{port}/",
            "host": "127.0.0.1",
            "port": port,
            "root": str(project_root),
            "entry": "index.html",
        }
        _SERVERS[project_root] = {**preview, "_server": server, "_thread": thread}
        return preview


def _find_free_port(start: int) -> int:
    for port in range(max(1024, int(start)), max(1024, int(start)) + 200):
        if not _port_open(port):
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.15):
            return True
    except OSError:
        return False

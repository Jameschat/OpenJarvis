"""Cloudflare Quick Tunnel — exposes localhost:7710 over HTTPS.

Spawns ``cloudflared tunnel --url http://localhost:<port>`` in the background,
parses the public URL from its stderr output, and returns it.  The tunnel
stays up until the process is stopped.
"""

from __future__ import annotations

import atexit
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CLOUDFLARED_PATHS = [
    "cloudflared",   # PATH
    r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
    r"C:\Program Files\cloudflared\cloudflared.exe",
    str(Path.home() / ".cloudflared" / "cloudflared.exe"),
]

_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)
_URL_FILE = Path.home() / ".openjarvis" / "tunnel_url.txt"


def _find_cloudflared() -> Optional[str]:
    """Return a path to a working cloudflared executable, or None."""
    exe = shutil.which("cloudflared")
    if exe:
        return exe
    for candidate in _CLOUDFLARED_PATHS:
        if not candidate:
            continue
        if Path(candidate).exists():
            return candidate
        # `shutil.which` handles PATH lookup
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


class QuickTunnel:
    """Manages a background cloudflared quick tunnel process."""

    def __init__(self, local_port: int = 7710) -> None:
        self.local_port = local_port
        self.url: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None
        self._url_event = threading.Event()
        self._reader_thread: Optional[threading.Thread] = None
        self._named_tunnel_url: Optional[str] = None

    def start(self, wait_timeout: float = 15.0) -> Optional[str]:
        """Start the tunnel and block until the public URL is available.

        Two modes:

        1. **Named tunnel** (preferred): if ``OPENJARVIS_TUNNEL_NAME`` env var
           is set OR ``~/.cloudflared/config.yml`` exists, run that named
           tunnel. Gives you a stable hostname like ``jarvis.yourdomain.com``
           that survives Jarvis restarts.

        2. **Quick tunnel** (fallback): random ``*.trycloudflare.com`` URL
           that changes on every launch. No setup required.
        """
        exe = _find_cloudflared()
        if exe is None:
            logger.error(
                "cloudflared not found — install with: "
                "winget install Cloudflare.cloudflared"
            )
            return None

        # Detect whether a named tunnel is configured. Order of preference:
        #   1. OPENJARVIS_TUNNEL_TOKEN  — dashboard-created tunnels (preferred)
        #   2. OPENJARVIS_TUNNEL_NAME   — CLI-created tunnels with cert.pem
        #   3. ~/.cloudflared/config.yml
        #   4. Fall back to a Quick Tunnel
        token = os.environ.get("OPENJARVIS_TUNNEL_TOKEN", "").strip()
        named_tunnel = os.environ.get("OPENJARVIS_TUNNEL_NAME", "").strip()
        config_path = Path.home() / ".cloudflared" / "config.yml"
        url_override = os.environ.get("OPENJARVIS_TUNNEL_URL", "").strip()

        if token:
            # Token-based tunnel — created via the Zero Trust dashboard.
            # All routing is configured server-side, we just run the connector.
            cmd = [exe, "tunnel", "--no-autoupdate", "run", "--token", token]
            self._named_tunnel_url = url_override or None
            # Mask token in logs
            logger.info("Starting cloudflared with dashboard token (tunnel hostname: %s)",
                        url_override or "configured in Cloudflare dashboard")
        elif named_tunnel:
            cmd = [exe, "tunnel", "--no-autoupdate", "run", named_tunnel]
            self._named_tunnel_url = url_override or f"https://{named_tunnel}"
            logger.info("Starting named cloudflared tunnel: %s", named_tunnel)
        elif config_path.exists():
            cmd = [exe, "tunnel", "--no-autoupdate", "--config", str(config_path), "run"]
            self._named_tunnel_url = url_override or None
            logger.info("Starting cloudflared with %s", config_path)
        else:
            # Quick tunnel — random URL each launch. Use 127.0.0.1 (not
            # "localhost") so cloudflared doesn't try ::1 first on Windows.
            cmd = [
                exe, "tunnel",
                "--url", f"http://127.0.0.1:{self.local_port}",
                "--no-autoupdate",
            ]
            self._named_tunnel_url = None
            logger.info("Starting cloudflared quick tunnel (no named tunnel configured)")

        popen_kwargs: dict = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "bufsize": 1,
        }
        if sys.platform == "win32":
            CREATE_NO_WINDOW = 0x08000000
            popen_kwargs["creationflags"] = CREATE_NO_WINDOW

        try:
            self._proc = subprocess.Popen(cmd, **popen_kwargs)
        except Exception as exc:
            logger.exception("Failed to start cloudflared: %s", exc)
            return None

        self._reader_thread = threading.Thread(
            target=self._read_output, daemon=True
        )
        self._reader_thread.start()

        # Named tunnel: we already know the URL, no need to scrape stderr
        if self._named_tunnel_url:
            self.url = self._named_tunnel_url
            try:
                _URL_FILE.parent.mkdir(parents=True, exist_ok=True)
                _URL_FILE.write_text(self.url, encoding="utf-8")
            except Exception:
                pass
            self._url_event.set()
            return self.url

        # Wait until the URL is parsed (or timeout)
        if self._url_event.wait(timeout=wait_timeout):
            return self.url

        logger.warning(
            "cloudflared didn't print a URL within %ss — check the "
            "process is running.", wait_timeout,
        )
        return None

    def _read_output(self) -> None:
        """Scan the cloudflared process output for the public URL."""
        if self._proc is None or self._proc.stdout is None:
            return
        for line in self._proc.stdout:
            match = _URL_RE.search(line)
            if match and self.url is None:
                self.url = match.group(0)
                try:
                    _URL_FILE.parent.mkdir(parents=True, exist_ok=True)
                    _URL_FILE.write_text(self.url, encoding="utf-8")
                except Exception:
                    pass
                self._url_event.set()

    def stop(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            except Exception:
                pass
            self._proc = None


_instance: Optional[QuickTunnel] = None


def start_quick_tunnel(local_port: int = 7710, wait_timeout: float = 15.0) -> Optional[str]:
    """Start a quick tunnel (idempotent) and return the public HTTPS URL."""
    global _instance
    if _instance is not None and _instance.url is not None:
        return _instance.url
    _instance = QuickTunnel(local_port=local_port)
    # Make sure cloudflared dies with us — prevents zombie processes from
    # piling up across restarts (each holds a different quick-tunnel URL,
    # confusing routing and eventually causing Cloudflare Error 1033).
    atexit.register(_instance.stop)
    return _instance.start(wait_timeout=wait_timeout)


def stop_tunnel() -> None:
    global _instance
    if _instance is not None:
        _instance.stop()
        _instance = None


def get_current_url() -> Optional[str]:
    """Return the active tunnel URL, or None if no tunnel is running."""
    if _instance is not None:
        return _instance.url
    # Fall back to the last-known URL from disk
    try:
        if _URL_FILE.exists():
            return _URL_FILE.read_text(encoding="utf-8").strip() or None
    except Exception:
        pass
    return None


__all__ = [
    "QuickTunnel",
    "start_quick_tunnel",
    "stop_tunnel",
    "get_current_url",
]

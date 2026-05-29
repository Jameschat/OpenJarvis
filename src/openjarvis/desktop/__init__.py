"""Jarvis desktop app (Phase 5).

A native Windows window that wraps Jarvis Studio (the Claude-Code/Codex-class
agent workspace served by brain_server) using pywebview / WebView2, and owns the
backend-readiness check. See the design note:
``Brain/Decisions/2026-05-29 - Jarvis desktop app - design.md``.
"""

from openjarvis.desktop.app import (
    backend_ready,
    launch,
    main,
    resolve_studio_url,
    wait_for_backend,
)

__all__ = [
    "backend_ready",
    "launch",
    "main",
    "resolve_studio_url",
    "wait_for_backend",
]

"""Vault summary — at-a-glance stats for the HUD VAULT button.

Mirrors the surface graphify exposes via /graphify/status: a small dict
the HUD can render as `📓 VAULT · 1234 notes · 12 MB · 7 today` without
any client-side aggregation.

Pure-logic helper. The HTTP wrapping lives in brain_server.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def summary(vault_root: Path, *, now: datetime) -> Dict[str, Any]:
    """Walk vault_root for *.md files; return a stats dict.

    Skips dotfiles and hidden directories (e.g. .obsidian/, .git/).
    Tolerant of unreadable files — logs and continues. Returns zeros
    for missing / empty vaults rather than raising.

    Output keys:
      total_notes:    int — count of *.md files
      total_bytes:    int — sum of file sizes
      last_write_iso: str | None — ISO mtime of newest file
      daily_today:    int — count of Daily/<YYYY-MM-DD>*.md for today
    """
    if not vault_root.exists() or not vault_root.is_dir():
        return {
            "total_notes": 0,
            "total_bytes": 0,
            "last_write_iso": None,
            "daily_today": 0,
        }

    total_notes = 0
    total_bytes = 0
    newest_mtime: Optional[float] = None

    for md in vault_root.rglob("*.md"):
        # Skip anything under a hidden directory (.obsidian/, .git/, etc.)
        if any(part.startswith(".") for part in md.relative_to(vault_root).parts):
            continue
        try:
            stat = md.stat()
        except OSError:
            continue
        total_notes += 1
        total_bytes += stat.st_size
        if newest_mtime is None or stat.st_mtime > newest_mtime:
            newest_mtime = stat.st_mtime

    daily_today = 0
    daily_dir = vault_root / "Daily"
    if daily_dir.exists() and daily_dir.is_dir():
        today_str = now.strftime("%Y-%m-%d")
        try:
            for entry in daily_dir.iterdir():
                if entry.is_file() and entry.suffix == ".md" and entry.name.startswith(today_str):
                    daily_today += 1
        except OSError:
            logger.debug("vault_stats: daily dir scan failed", exc_info=True)

    last_write_iso: Optional[str] = None
    if newest_mtime is not None:
        last_write_iso = datetime.fromtimestamp(newest_mtime, tz=timezone.utc).isoformat()

    return {
        "total_notes": total_notes,
        "total_bytes": total_bytes,
        "last_write_iso": last_write_iso,
        "daily_today": daily_today,
    }


__all__ = ["summary"]

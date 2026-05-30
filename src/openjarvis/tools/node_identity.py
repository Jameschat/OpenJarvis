"""Jarvis node role metadata."""

from __future__ import annotations

import os
from typing import Any


def load_node_identity() -> dict[str, Any]:
    role = os.environ.get("JARVIS_NODE_ROLE", "primary").strip().lower()
    if role not in {"primary", "worker"}:
        role = "primary"

    default_node_id = "worker-gpu" if role == "worker" else "main-4090"
    node_id = os.environ.get("JARVIS_NODE_ID", default_node_id).strip() or default_node_id

    return {
        "role": role,
        "node_id": node_id,
        "is_worker": role == "worker",
        "worker_model": os.environ.get("JARVIS_WORKER_MODEL", "").strip(),
        "worker_repo": os.environ.get("JARVIS_WORKER_REPO", "").strip(),
    }

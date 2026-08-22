"""Build the agent's tool instances from an allowlist, scoping file tools.

Extracted from cli/serve.py so the scoping logic is unit-testable. File tools
(file_read/file_edit/file_write) are constructed with allowed_dirs=[workspace]
when a workspace is configured, so the agent can only touch files there.
"""

from __future__ import annotations

import logging
from typing import Iterable, List

from openjarvis.tools._stubs import BaseTool

_FILE_TOOLS = {"file_read", "file_edit", "file_write", "preview_start"}


def _split_workspace_dirs(workspace_dir: str) -> List[str]:
    """Split a workspace setting into one or more allowed roots.

    A single root is far too restrictive in practice: real projects live outside
    the primary workspace (e.g. the active Cursed Tides tree lives under
    ~/Documents/Codex while workspace_dir was 'E:/Claude'), and every file_read /
    file_write against them failed with 'Access denied' - the agent could not work
    on them at all. Accept ';' or ',' separated roots so several trees can be
    granted without abandoning the sandbox entirely.
    """
    if not workspace_dir:
        return []
    parts = [p.strip() for p in workspace_dir.replace(";", ",").split(",")]
    return [p for p in parts if p]


def build_agent_tools(allowed: Iterable[str], workspace_dir: str = "") -> List[BaseTool]:
    import openjarvis.tools  # noqa: F401  # trigger registration
    from openjarvis.core.registry import ToolRegistry

    allowed_set = {a for a in allowed if a}
    workspace_roots = _split_workspace_dirs(workspace_dir)
    missing = sorted(allowed_set - set(ToolRegistry.keys()))
    if missing:
        # A name in the allow-list that is not registered is silently dropped, so
        # the model can be told (by the system prompt) to call a tool that does
        # not exist. Surface it instead of failing quietly.
        logging.getLogger("openjarvis.agents").warning(
            "agent tool allow-list references unregistered tools: %s", ", ".join(missing)
        )
    tools: List[BaseTool] = []
    for name in ToolRegistry.keys():
        if name not in allowed_set:
            continue
        tool_cls = ToolRegistry.get(name)
        kwargs = {}
        if workspace_roots and name in _FILE_TOOLS:
            kwargs["allowed_dirs"] = list(workspace_roots)
        if isinstance(tool_cls, type) and issubclass(tool_cls, BaseTool):
            try:
                tools.append(tool_cls(**kwargs))
            except TypeError:
                tools.append(tool_cls())
        elif isinstance(tool_cls, BaseTool):
            tools.append(tool_cls)
    return tools


__all__ = ["build_agent_tools"]

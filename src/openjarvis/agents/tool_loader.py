"""Build the agent's tool instances from an allowlist, scoping file tools.

Extracted from cli/serve.py so the scoping logic is unit-testable. File tools
(file_read/file_edit/file_write) are constructed with allowed_dirs=[workspace]
when a workspace is configured, so the agent can only touch files there.
"""

from __future__ import annotations

from typing import Iterable, List

from openjarvis.tools._stubs import BaseTool

_FILE_TOOLS = {"file_read", "file_edit", "file_write", "preview_start"}


def build_agent_tools(allowed: Iterable[str], workspace_dir: str = "") -> List[BaseTool]:
    import openjarvis.tools  # noqa: F401  # trigger registration
    from openjarvis.core.registry import ToolRegistry

    allowed_set = {a for a in allowed if a}
    tools: List[BaseTool] = []
    for name in ToolRegistry.keys():
        if name not in allowed_set:
            continue
        tool_cls = ToolRegistry.get(name)
        kwargs = {}
        if workspace_dir and name in _FILE_TOOLS:
            kwargs["allowed_dirs"] = [workspace_dir]
        if isinstance(tool_cls, type) and issubclass(tool_cls, BaseTool):
            try:
                tools.append(tool_cls(**kwargs))
            except TypeError:
                tools.append(tool_cls())
        elif isinstance(tool_cls, BaseTool):
            tools.append(tool_cls)
    return tools


__all__ = ["build_agent_tools"]

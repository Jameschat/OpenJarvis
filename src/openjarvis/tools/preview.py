"""Preview tool — serve a project directory over HTTP so the agent can load it
in a browser (browser_navigate / browser_screenshot) while building it.

This is the start of the "live preview" capability: start a local server for a
website/project, then observe it with the Playwright browser tools.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec
from openjarvis.tools.project_preview import start_project_preview


@ToolRegistry.register("preview_start")
class PreviewStartTool(BaseTool):
    """Start a local HTTP preview server for a project directory."""

    tool_id = "preview_start"

    def __init__(self, allowed_dirs: Optional[List[str]] = None) -> None:
        self._allowed_dirs = [Path(d).resolve() for d in (allowed_dirs or [])]

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="preview_start",
            description=(
                "Start a local web preview server for a project directory (serves "
                "its files over HTTP) and return the URL. Use this to load a website "
                "you are building, then use browser_navigate to that URL and "
                "browser_screenshot to actually see it. Re-run after edits to refresh."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Project directory to serve (must contain the site's files).",
                    },
                },
                "required": ["path"],
            },
            category="preview",
            required_capabilities=["file:read"],
        )

    def _is_allowed(self, p: Path) -> bool:
        if not self._allowed_dirs:
            return True
        r = p.resolve()
        return any(r == d or r.is_relative_to(d) for d in self._allowed_dirs)

    def execute(self, **params: Any) -> ToolResult:
        path = params.get("path", "")
        if not path:
            return ToolResult(tool_name="preview_start", content="No path provided.", success=False)
        p = Path(path)
        if not self._is_allowed(p):
            return ToolResult(
                tool_name="preview_start",
                content=f"Access denied: {path} is outside allowed directories.",
                success=False,
            )
        if not p.is_dir():
            return ToolResult(
                tool_name="preview_start",
                content=f"Not a directory: {path}",
                success=False,
            )
        try:
            info = start_project_preview(p)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(tool_name="preview_start", content=f"Preview error: {exc}", success=False)
        url = info.get("url")
        return ToolResult(
            tool_name="preview_start",
            content=(
                f"Preview running at {url} (serving {path}). "
                f"Now call browser_navigate with url={url}, then browser_screenshot to view it."
            ),
            success=True,
            metadata={"url": url, "port": info.get("port"), "path": str(p.resolve())},
        )


__all__ = ["PreviewStartTool"]

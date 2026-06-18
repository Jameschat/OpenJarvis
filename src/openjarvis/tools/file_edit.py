"""File edit tool — Claude Code-style old_string/new_string edits with a diff.

Returns a unified diff in ToolResult.metadata so the chat UI can render the
change inline. The ToolExecutor forwards this metadata on TOOL_CALL_END.
"""

from __future__ import annotations

import difflib
import uuid
from pathlib import Path
from typing import Any, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_MAX_SIZE_BYTES = 10_485_760


@ToolRegistry.register("file_edit")
class EditTool(BaseTool):
    """Replace an exact substring in a file and report a unified diff."""

    tool_id = "file_edit"

    def __init__(self, allowed_dirs: Optional[List[str]] = None) -> None:
        self._allowed_dirs = [Path(d).resolve() for d in (allowed_dirs or [])]

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="file_edit",
            description=(
                "Edit an existing file by replacing an exact string. Prefer this"
                " over file_write for changes to existing files. old_string must"
                " match exactly and be unique unless replace_all is true."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to edit."},
                    "old_string": {"type": "string", "description": "Exact text to replace."},
                    "new_string": {"type": "string", "description": "Replacement text."},
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace all occurrences (default false).",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
            category="filesystem",
            required_capabilities=["file:write"],
        )

    def _is_path_allowed(self, path: Path) -> bool:
        if not self._allowed_dirs:
            return True
        resolved = path.resolve()
        return any(resolved == d or resolved.is_relative_to(d) for d in self._allowed_dirs)

    def execute(self, **params: Any) -> ToolResult:
        file_path = params.get("path", "")
        old_string = params.get("old_string")
        new_string = params.get("new_string")
        replace_all = bool(params.get("replace_all", False))

        if not file_path:
            return ToolResult(tool_name="file_edit", content="No path provided.", success=False)
        if old_string is None or new_string is None:
            return ToolResult(
                tool_name="file_edit",
                content="Both old_string and new_string are required.",
                success=False,
            )

        path = Path(file_path)

        from openjarvis.security.file_policy import is_sensitive_file

        if is_sensitive_file(path):
            return ToolResult(
                tool_name="file_edit",
                content=f"Access denied: {file_path} is a sensitive file.",
                success=False,
            )
        if not self._is_path_allowed(path):
            return ToolResult(
                tool_name="file_edit",
                content=f"Access denied: {file_path} is outside allowed directories.",
                success=False,
            )
        if not path.is_file():
            return ToolResult(
                tool_name="file_edit",
                content=f"File does not exist: {file_path}",
                success=False,
            )

        try:
            original = path.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResult(tool_name="file_edit", content=f"Read error: {exc}", success=False)

        count = original.count(old_string)
        if count == 0:
            return ToolResult(
                tool_name="file_edit",
                content="old_string not found in file.",
                success=False,
            )
        if count > 1 and not replace_all:
            return ToolResult(
                tool_name="file_edit",
                content=(
                    f"old_string is not unique ({count} matches). Add surrounding"
                    " context to disambiguate, or set replace_all=true."
                ),
                success=False,
            )

        updated = (
            original.replace(old_string, new_string)
            if replace_all
            else original.replace(old_string, new_string, 1)
        )

        if len(updated.encode("utf-8")) > _MAX_SIZE_BYTES:
            return ToolResult(
                tool_name="file_edit",
                content=f"Result too large (max {_MAX_SIZE_BYTES} bytes).",
                success=False,
            )

        diff = "\n".join(
            difflib.unified_diff(
                original.splitlines(),
                updated.splitlines(),
                fromfile=file_path,
                tofile=file_path,
                lineterm="",
            )
        )
        added = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))

        try:
            path.write_text(updated, encoding="utf-8")
        except OSError as exc:
            return ToolResult(tool_name="file_edit", content=f"Write error: {exc}", success=False)

        edit_id = uuid.uuid4().hex[:12]
        return ToolResult(
            tool_name="file_edit",
            content=f"Edited {file_path} (+{added} -{removed})",
            success=True,
            metadata={
                "path": str(path.resolve()),
                "diff": diff,
                "added": added,
                "removed": removed,
                "edit_id": edit_id,
            },
        )


__all__ = ["EditTool"]

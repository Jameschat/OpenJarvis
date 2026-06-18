"""Todo-write tool — maintain a visible task checklist for multi-step work.

Returns the item list in ToolResult.metadata; the chat UI renders it as a
checklist (PlanChecklist). The ToolExecutor forwards this metadata on
TOOL_CALL_END.
"""

from __future__ import annotations

from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_VALID = {"pending", "in_progress", "completed"}


@ToolRegistry.register("todo_write")
class TodoWriteTool(BaseTool):
    """Publish/update the task checklist shown to the user."""

    tool_id = "todo_write"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="todo_write",
            description=(
                "Maintain a visible task checklist for multi-step work. Call it at"
                " the start of a multi-step task with the full plan, then call it"
                " again to update statuses as you complete steps. Always send the"
                " ENTIRE list each time."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "The full task list.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                },
                            },
                            "required": ["title", "status"],
                        },
                    },
                },
                "required": ["items"],
            },
            category="planning",
        )

    def execute(self, **params: Any) -> ToolResult:
        items = params.get("items")
        if not isinstance(items, list):
            return ToolResult(
                tool_name="todo_write",
                content="items must be a list of {title, status}.",
                success=False,
            )
        norm = []
        for it in items:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title") or "").strip()
            if not title:
                continue
            status = it.get("status")
            if status not in _VALID:
                status = "pending"
            norm.append({"title": title, "status": status})
        done = sum(1 for it in norm if it["status"] == "completed")
        return ToolResult(
            tool_name="todo_write",
            content=f"{len(norm)} tasks ({done} done)",
            success=True,
            metadata={"items": norm},
        )


__all__ = ["TodoWriteTool"]

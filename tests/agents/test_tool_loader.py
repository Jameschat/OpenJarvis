from pathlib import Path

import pytest

from openjarvis.agents.tool_loader import build_agent_tools
from openjarvis.core.registry import ToolRegistry
from openjarvis.tools.calculator import CalculatorTool
from openjarvis.tools.file_edit import EditTool
from openjarvis.tools.file_read import FileReadTool
from openjarvis.tools.file_write import FileWriteTool


@pytest.fixture(autouse=True)
def _reregister_tools():
    # The shared conftest clears ToolRegistry before each test; module imports
    # are cached so the @register decorators don't re-run. Re-register the tools
    # these tests exercise (runs after the conftest clear).
    for name, cls in (
        ("calculator", CalculatorTool),
        ("file_read", FileReadTool),
        ("file_edit", EditTool),
        ("file_write", FileWriteTool),
    ):
        if name not in ToolRegistry.keys():
            ToolRegistry.register(name)(cls)


def test_only_allowed_tools_are_loaded():
    tools = build_agent_tools({"calculator"}, "")
    names = {t.spec.name for t in tools}
    assert "calculator" in names
    assert "file_edit" not in names


def test_file_tools_scoped_to_workspace(tmp_path: Path):
    tools = build_agent_tools({"file_edit", "file_write", "file_read"}, str(tmp_path))
    by = {t.spec.name: t for t in tools}
    expected = [Path(str(tmp_path)).resolve()]
    assert by["file_edit"]._allowed_dirs == expected
    assert by["file_write"]._allowed_dirs == expected
    assert by["file_read"]._allowed_dirs == expected


def test_no_workspace_means_unrestricted():
    tools = build_agent_tools({"file_edit"}, "")
    by = {t.spec.name: t for t in tools}
    assert by["file_edit"]._allowed_dirs == []


def test_non_file_tools_ignore_workspace(tmp_path: Path):
    # A non-file tool must still load even though workspace is set.
    tools = build_agent_tools({"calculator"}, str(tmp_path))
    assert any(t.spec.name == "calculator" for t in tools)

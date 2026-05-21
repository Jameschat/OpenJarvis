from __future__ import annotations

import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_brain_html_renders_codegraph_as_named_cortex_lobe():
    html = (ROOT / "jarvis_web" / "brain.html").read_text(encoding="utf-8")

    assert "CODEGRAPH" in html
    assert "CODEGRAPH_BRAIN_IDX" in html
    assert "let codeGraphPulse = 0" in html
    assert "n.communityIdx === CODEGRAPH_BRAIN_IDX" in html
    assert 'id="codegraph-btn"' in html
    assert "/codegraph/status" in html


def test_codegraph_status_reads_local_index_and_mcp_config(tmp_path):
    from openjarvis.cli.brain_server import _codegraph_status

    repo = tmp_path / "repo"
    index = repo / ".codegraph"
    index.mkdir(parents=True)
    db = index / "codegraph.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table files(id integer)")
        conn.execute("create table nodes(id integer)")
        conn.execute("create table edges(id integer)")
        conn.executemany("insert into files(id) values (?)", [(1,), (2,)])
        conn.executemany("insert into nodes(id) values (?)", [(1,), (2,), (3,)])
        conn.executemany("insert into edges(id) values (?)", [(1,), (2,), (3,), (4,)])

    tool = tmp_path / "codegraph.cmd"
    tool.write_text("", encoding="utf-8")
    (repo / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "codegraph": {
                        "command": str(tool),
                        "args": ["serve", "--mcp", "--path", str(repo), "--no-watch"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    status = _codegraph_status(repo_root=repo, tool_path=tool)

    assert status["online"] is True
    assert status["installed"] is True
    assert status["indexed"] is True
    assert status["mcp_configured"] is True
    assert status["files"] == 2
    assert status["nodes"] == 3
    assert status["edges"] == 4
    assert "daily 06:05" in status["refresh"]


def test_brain_server_exposes_codegraph_status_endpoint():
    source = (ROOT / "src" / "openjarvis" / "cli" / "brain_server.py").read_text(
        encoding="utf-8"
    )

    assert 'elif self.path == "/codegraph/status":' in source
    assert "_codegraph_status()" in source

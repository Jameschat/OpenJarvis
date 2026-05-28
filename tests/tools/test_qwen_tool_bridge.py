import json
from pathlib import Path


def test_qwen_tool_manifest_exposes_installed_jarvis_skill_guidance():
    from openjarvis.tools import qwen_tool_bridge

    manifest = qwen_tool_bridge.tool_manifest()

    assert "skill_guidance" in manifest
    assert "installed Jarvis skill" in manifest


def test_qwen_tool_manifest_exposes_safe_repo_inspection():
    from openjarvis.tools import qwen_tool_bridge

    manifest = qwen_tool_bridge.tool_manifest()

    assert "repo_read" in manifest
    assert "repo_search" in manifest
    assert "read/search non-secret project files" in manifest


def test_qwen_tool_manifest_exposes_local_browser_visual_check():
    from openjarvis.tools import qwen_tool_bridge

    manifest = qwen_tool_bridge.tool_manifest()

    assert "browser_visual_check" in manifest
    assert "local Jarvis page" in manifest


def test_qwen_tool_manifest_exposes_patch_proposal():
    from openjarvis.tools import qwen_tool_bridge

    manifest = qwen_tool_bridge.tool_manifest()

    assert "repo_patch_proposal" in manifest
    assert "validated edit proposal" in manifest


def test_qwen_skill_guidance_loads_installed_skill(monkeypatch, tmp_path):
    from openjarvis.tools import qwen_tool_bridge

    skill_dir = tmp_path / "ui-ux-pro-max"
    skill_dir.mkdir()
    (skill_dir / "skill.toml").write_text(
        "\n".join(
            [
                "[skill]",
                'name = "ui-ux-pro-max"',
                'description = "Design intelligence for polished interfaces."',
                'tags = ["ui", "ux"]',
            ]
        ),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        "# UI/UX Pro Max\n\nUse for visual design, responsive layouts, buttons, forms, and accessibility.",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENJARVIS_SKILLS_DIR", str(tmp_path))

    results = qwen_tool_bridge.execute_tool_requests(
        [
            {
                "id": "r1",
                "tool": "skill_guidance",
                "args": {"name": "ui-ux-pro-max"},
            }
        ]
    )

    assert results == [
        {
            "id": "r1",
            "tool": "skill_guidance",
            "ok": True,
            "name": "ui-ux-pro-max",
            "description": "Design intelligence for polished interfaces.",
            "tags": ["ui", "ux"],
            "metadata_path": "skill.toml",
            "excerpt": "# UI/UX Pro Max\n\nUse for visual design, responsive layouts, buttons, forms, and accessibility.",
        }
    ]


def test_qwen_skill_guidance_rejects_path_traversal(monkeypatch, tmp_path):
    from openjarvis.tools import qwen_tool_bridge

    monkeypatch.setenv("OPENJARVIS_SKILLS_DIR", str(tmp_path))

    results = qwen_tool_bridge.execute_tool_requests(
        [{"id": "r1", "tool": "skill_guidance", "args": {"name": "../secret"}}]
    )

    assert results[0]["ok"] is False
    assert "invalid skill name" in results[0]["error"]


def test_qwen_repo_read_reads_non_secret_project_file(monkeypatch, tmp_path):
    from openjarvis.tools import qwen_tool_bridge

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "example.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")
    monkeypatch.setenv("OPENJARVIS_QWEN_REPO_ROOT", str(tmp_path))

    results = qwen_tool_bridge.execute_tool_requests(
        [
            {
                "id": "r1",
                "tool": "repo_read",
                "args": {"path": "src/example.py", "max_chars": 2000},
            }
        ]
    )

    assert results[0]["ok"] is True
    assert results[0]["path"] == "src/example.py"
    assert "def hello" in results[0]["content"]


def test_qwen_repo_read_rejects_secret_and_path_escape(monkeypatch, tmp_path):
    from openjarvis.tools import qwen_tool_bridge

    (tmp_path / "jarvis.bat").write_text("OPENAI_API_KEY=secret", encoding="utf-8")
    monkeypatch.setenv("OPENJARVIS_QWEN_REPO_ROOT", str(tmp_path))

    secret = qwen_tool_bridge.execute_tool_requests(
        [{"id": "r1", "tool": "repo_read", "args": {"path": "jarvis.bat"}}]
    )
    escape = qwen_tool_bridge.execute_tool_requests(
        [{"id": "r2", "tool": "repo_read", "args": {"path": "../outside.txt"}}]
    )

    assert secret[0]["ok"] is False
    assert secret[0]["blocked"] is True
    assert "secret" in secret[0]["reason"].lower()
    assert escape[0]["ok"] is False
    assert "path escapes" in escape[0]["error"]


def test_qwen_repo_search_returns_bounded_matches(monkeypatch, tmp_path):
    from openjarvis.tools import qwen_tool_bridge

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "one.py").write_text("alpha\nneedle here\n", encoding="utf-8")
    (tmp_path / "src" / "two.py").write_text("needle there\n", encoding="utf-8")
    monkeypatch.setenv("OPENJARVIS_QWEN_REPO_ROOT", str(tmp_path))

    results = qwen_tool_bridge.execute_tool_requests(
        [
            {
                "id": "r1",
                "tool": "repo_search",
                "args": {"query": "needle", "glob": "*.py", "limit": 5},
            }
        ]
    )

    assert results[0]["ok"] is True
    assert results[0]["matches"]
    assert all("needle" in match["line"] for match in results[0]["matches"])


def test_qwen_browser_visual_check_rejects_external_url():
    from openjarvis.tools import qwen_tool_bridge

    results = qwen_tool_bridge.execute_tool_requests(
        [
            {
                "id": "r1",
                "tool": "browser_visual_check",
                "args": {"url": "https://example.com"},
            }
        ]
    )

    assert results[0]["ok"] is False
    assert results[0]["blocked"] is True
    assert "local Jarvis pages" in results[0]["reason"]


def test_qwen_browser_visual_check_runs_local_probe(monkeypatch, tmp_path):
    from openjarvis.tools import qwen_tool_bridge

    monkeypatch.setenv("OPENJARVIS_QWEN_VISUAL_CHECK_DIR", str(tmp_path))
    monkeypatch.setattr(
        qwen_tool_bridge,
        "_browser_visual_check_impl",
        lambda url, screenshot_path, full_page: {
            "ok": True,
            "url": url,
            "title": "Jarvis Studio",
            "text_excerpt": "Studio ready",
            "screenshot_path": str(screenshot_path),
        },
    )

    results = qwen_tool_bridge.execute_tool_requests(
        [
            {
                "id": "r1",
                "tool": "browser_visual_check",
                "args": {"url": "http://localhost:7710/studio"},
            }
        ]
    )

    assert results[0]["ok"] is True
    assert results[0]["title"] == "Jarvis Studio"
    assert results[0]["screenshot_path"].endswith(".png")


def test_qwen_repo_patch_proposal_writes_auditable_plan(monkeypatch, tmp_path):
    from openjarvis.tools import qwen_tool_bridge

    repo = tmp_path / "repo"
    proposals = tmp_path / "proposals"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("print('old')\n", encoding="utf-8")
    monkeypatch.setenv("OPENJARVIS_QWEN_REPO_ROOT", str(repo))
    monkeypatch.setenv("OPENJARVIS_QWEN_PATCH_PROPOSAL_DIR", str(proposals))

    results = qwen_tool_bridge.execute_tool_requests(
        [
            {
                "id": "r1",
                "tool": "repo_patch_proposal",
                "args": {
                    "rationale": "Update greeting",
                    "files": [{"path": "src/app.py", "content": "print('new')\n"}],
                },
            }
        ]
    )

    result = results[0]
    assert result["ok"] is True
    assert result["apply_requires_approval"] is True
    assert result["changed_files"] == ["src/app.py"]
    assert (repo / "src" / "app.py").read_text(encoding="utf-8") == "print('old')\n"
    proposal = json.loads(Path(result["proposal_path"]).read_text(encoding="utf-8"))
    assert proposal["rationale"] == "Update greeting"
    assert proposal["files"][0]["path"] == "src/app.py"
    assert proposal["files"][0]["content"] == "print('new')\n"


def test_qwen_repo_patch_proposal_rejects_secret_path(monkeypatch, tmp_path):
    from openjarvis.tools import qwen_tool_bridge

    monkeypatch.setenv("OPENJARVIS_QWEN_REPO_ROOT", str(tmp_path))

    results = qwen_tool_bridge.execute_tool_requests(
        [
            {
                "id": "r1",
                "tool": "repo_patch_proposal",
                "args": {
                    "files": [{"path": "jarvis.bat", "content": "OPENAI_API_KEY=secret"}],
                },
            }
        ]
    )

    assert results[0]["ok"] is False
    assert results[0]["blocked"] is True

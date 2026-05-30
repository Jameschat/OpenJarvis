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


def test_apply_patch_proposal_requires_exact_approval(monkeypatch, tmp_path):
    from openjarvis.tools import qwen_tool_bridge

    repo = tmp_path / "repo"
    proposals = tmp_path / "proposals"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("print('old')\n", encoding="utf-8")
    monkeypatch.setenv("OPENJARVIS_QWEN_REPO_ROOT", str(repo))
    monkeypatch.setenv("OPENJARVIS_QWEN_PATCH_PROPOSAL_DIR", str(proposals))
    proposal = qwen_tool_bridge.execute_tool_requests(
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
    )[0]

    result = qwen_tool_bridge.apply_patch_proposal(
        proposal["proposal_id"],
        approval_phrase="yes",
    )

    assert result["ok"] is False
    assert result["blocked"] is True
    assert (repo / "src" / "app.py").read_text(encoding="utf-8") == "print('old')\n"


def test_apply_patch_proposal_writes_files_and_backup(monkeypatch, tmp_path):
    from openjarvis.tools import qwen_tool_bridge

    repo = tmp_path / "repo"
    proposals = tmp_path / "proposals"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("print('old')\n", encoding="utf-8")
    monkeypatch.setenv("OPENJARVIS_QWEN_REPO_ROOT", str(repo))
    monkeypatch.setenv("OPENJARVIS_QWEN_PATCH_PROPOSAL_DIR", str(proposals))
    proposal = qwen_tool_bridge.execute_tool_requests(
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
    )[0]

    result = qwen_tool_bridge.apply_patch_proposal(
        proposal["proposal_id"],
        approval_phrase="APPLY QWEN PATCH",
    )

    assert result["ok"] is True
    assert result["applied_files"] == ["src/app.py"]
    assert (repo / "src" / "app.py").read_text(encoding="utf-8") == "print('new')\n"
    assert result["backups"][0]["path"] == "src/app.py"
    assert Path(result["backups"][0]["backup_path"]).read_text(encoding="utf-8") == "print('old')\n"
    saved = json.loads(Path(proposal["proposal_path"]).read_text(encoding="utf-8"))
    assert saved["applied_at"]


def test_list_patch_proposals_marks_pending_and_applied(monkeypatch, tmp_path):
    from openjarvis.tools import qwen_tool_bridge

    repo = tmp_path / "repo"
    proposals = tmp_path / "proposals"
    repo.mkdir()
    monkeypatch.setenv("OPENJARVIS_QWEN_REPO_ROOT", str(repo))
    monkeypatch.setenv("OPENJARVIS_QWEN_PATCH_PROPOSAL_DIR", str(proposals))
    created = qwen_tool_bridge.execute_tool_requests(
        [
            {
                "id": "r1",
                "tool": "repo_patch_proposal",
                "args": {
                    "rationale": "Add file",
                    "files": [{"path": "src/app.py", "content": "print('new')\n"}],
                },
            }
        ]
    )[0]

    pending = qwen_tool_bridge.list_patch_proposals()
    qwen_tool_bridge.apply_patch_proposal(created["proposal_id"], approval_phrase="APPLY QWEN PATCH")
    applied = qwen_tool_bridge.list_patch_proposals()

    assert pending[0]["status"] == "pending"
    assert pending[0]["changed_files"] == ["src/app.py"]
    assert applied[0]["status"] == "applied"


def test_execute_repo_root_kwarg_scopes_reads_outside_default(monkeypatch, tmp_path):
    """A per-call repo_root lets Qwen read a project outside the OpenJarvis repo.

    This is the Westhill fix: no env override set, repo_root passed explicitly.
    """
    from openjarvis.tools import qwen_tool_bridge

    monkeypatch.delenv("OPENJARVIS_QWEN_REPO_ROOT", raising=False)
    site = tmp_path / "westhill-hotel"
    site.mkdir()
    (site / "index.html").write_text("<!doctype html><title>Westhill</title>\n", encoding="utf-8")

    results = qwen_tool_bridge.execute_tool_requests(
        [{"id": "r1", "tool": "repo_read", "args": {"path": "index.html"}}],
        repo_root=str(site),
    )

    assert results[0]["ok"] is True
    assert "Westhill" in results[0]["content"]
    # Active root is reset after the call so later calls are not affected.
    assert qwen_tool_bridge._ACTIVE_REPO_ROOT is None


def test_execute_repo_root_kwarg_resets_even_on_error(monkeypatch, tmp_path):
    from openjarvis.tools import qwen_tool_bridge

    monkeypatch.delenv("OPENJARVIS_QWEN_REPO_ROOT", raising=False)
    qwen_tool_bridge.execute_tool_requests(
        [{"id": "r1", "tool": "repo_read", "args": {"path": "missing.html"}}],
        repo_root=str(tmp_path),
    )
    assert qwen_tool_bridge._ACTIVE_REPO_ROOT is None


def test_apply_patch_proposal_honors_recorded_repo_root(monkeypatch, tmp_path):
    """A proposal created for a project outside the OpenJarvis repo applies back
    into that project's folder, using the repo_root recorded on the proposal —
    even though no env override is active at apply time."""
    from openjarvis.tools import qwen_tool_bridge

    site = tmp_path / "westhill-hotel"
    proposals = tmp_path / "proposals"
    site.mkdir()
    (site / "index.html").write_text("<h1>old</h1>\n", encoding="utf-8")
    monkeypatch.delenv("OPENJARVIS_QWEN_REPO_ROOT", raising=False)
    monkeypatch.setenv("OPENJARVIS_QWEN_PATCH_PROPOSAL_DIR", str(proposals))

    created = qwen_tool_bridge.execute_tool_requests(
        [
            {
                "id": "r1",
                "tool": "repo_patch_proposal",
                "args": {
                    "rationale": "New hero copy",
                    "files": [{"path": "index.html", "content": "<h1>new</h1>\n"}],
                },
            }
        ],
        repo_root=str(site),
    )[0]

    assert created["ok"] is True
    proposal = json.loads(Path(created["proposal_path"]).read_text(encoding="utf-8"))
    assert Path(proposal["repo_root"]).resolve() == site.resolve()

    # Apply with no active scope — must still land inside the site folder.
    applied = qwen_tool_bridge.apply_patch_proposal(
        created["proposal_id"], approval_phrase="APPLY QWEN PATCH"
    )
    assert applied["ok"] is True
    assert (site / "index.html").read_text(encoding="utf-8") == "<h1>new</h1>\n"


def test_manifest_exposes_verify_in_sandbox():
    from openjarvis.tools import qwen_tool_bridge

    manifest = qwen_tool_bridge.tool_manifest()
    assert "verify_in_sandbox" in manifest
    assert "DISPOSABLE" in manifest


def test_verify_in_sandbox_runs_declared_check_scoped_to_project(monkeypatch, tmp_path):
    """End-to-end: Qwen's verify_in_sandbox request runs the project's declared
    check against its proposed files in a throwaway copy, scoped via repo_root."""
    import sys
    from openjarvis.tools import qwen_tool_bridge

    monkeypatch.delenv("OPENJARVIS_QWEN_REPO_ROOT", raising=False)
    site = tmp_path / "proj"
    site.mkdir()
    (site / ".jarvis-verify.txt").write_text(
        f"{sys.executable} -m py_compile app.py\n", encoding="utf-8"
    )
    (site / "app.py").write_text("def broken(:\n", encoding="utf-8")

    results = qwen_tool_bridge.execute_tool_requests(
        [
            {
                "id": "r1",
                "tool": "verify_in_sandbox",
                "args": {"files": [{"path": "app.py", "content": "def ok():\n    return 1\n"}]},
            }
        ],
        repo_root=str(site),
    )

    assert results[0]["ok"] is True
    assert results[0]["passed"] is True
    # Real file untouched.
    assert (site / "app.py").read_text(encoding="utf-8") == "def broken(:\n"


def test_parse_tool_requests_accepts_json_fenced_block():
    """Models commonly wrap requests in ```json instead of ```qwen_tool_requests.
    Regression for the parser bug the agentic eval surfaced (2026-05-30)."""
    from openjarvis.tools import qwen_tool_bridge

    content = (
        "Sure, here is the fix:\n\n"
        "```json\n"
        '{"requests":[{"id":"r1","tool":"repo_patch_proposal","args":'
        '{"rationale":"fix","files":[{"path":"app.py","content":"def greet(name):\\n    return name\\n"}]}}]}\n'
        "```\n"
    )
    reqs = qwen_tool_bridge.parse_tool_requests(content)
    assert len(reqs) == 1
    assert reqs[0]["tool"] == "repo_patch_proposal"
    assert reqs[0]["args"]["files"][0]["path"] == "app.py"


def test_parse_tool_requests_accepts_raw_json():
    from openjarvis.tools import qwen_tool_bridge

    content = '{"requests":[{"id":"r1","tool":"recall_vault","args":{"query":"x"}}]}'
    reqs = qwen_tool_bridge.parse_tool_requests(content)
    assert len(reqs) == 1
    assert reqs[0]["tool"] == "recall_vault"


def test_parse_tool_requests_ignores_unrelated_code_fence():
    from openjarvis.tools import qwen_tool_bridge

    content = "```python\nprint('hello')\n```\nno tool requests here"
    assert qwen_tool_bridge.parse_tool_requests(content) == []


def test_parse_tool_requests_accepts_schema_aliases():
    """Qwen sometimes emits qwen_tool_requests/name instead of requests/tool.
    Regression for schema drift the agentic eval surfaced (2026-05-30)."""
    from openjarvis.tools import qwen_tool_bridge

    content = (
        "```json\n"
        '{"qwen_tool_requests":[{"id":"r1","name":"repo_patch_proposal","args":'
        '{"files":[{"path":"app.py","content":"def greet(name):\\n    return name\\n"}]}}]}\n'
        "```\n"
    )
    reqs = qwen_tool_bridge.parse_tool_requests(content)
    assert len(reqs) == 1
    assert reqs[0]["tool"] == "repo_patch_proposal"
    assert reqs[0]["args"]["files"][0]["path"] == "app.py"

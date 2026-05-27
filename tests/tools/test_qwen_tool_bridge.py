def test_qwen_tool_manifest_exposes_installed_jarvis_skill_guidance():
    from openjarvis.tools import qwen_tool_bridge

    manifest = qwen_tool_bridge.tool_manifest()

    assert "skill_guidance" in manifest
    assert "installed Jarvis skill" in manifest


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

from pathlib import Path


def test_project_preview_starts_static_server_for_index(tmp_path):
    from openjarvis.tools.project_preview import start_project_preview

    (tmp_path / "index.html").write_text("<h1>Westhill</h1>", encoding="utf-8")

    preview = start_project_preview(tmp_path)

    assert preview["ok"] is True
    assert preview["url"].startswith("http://127.0.0.1:")
    assert preview["root"] == str(tmp_path.resolve())
    assert preview["entry"] == "index.html"


def test_project_preview_rejects_missing_index(tmp_path):
    from openjarvis.tools.project_preview import start_project_preview

    preview = start_project_preview(tmp_path)

    assert preview["ok"] is False
    assert "index.html" in preview["error"]


def test_project_preview_reuses_existing_server_for_same_root(tmp_path):
    from openjarvis.tools.project_preview import start_project_preview

    (tmp_path / "index.html").write_text("<h1>Westhill</h1>", encoding="utf-8")

    first = start_project_preview(tmp_path)
    second = start_project_preview(tmp_path)

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["url"] == first["url"]

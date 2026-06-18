from openjarvis.tools.todo_write import TodoWriteTool


def test_returns_items_in_metadata():
    res = TodoWriteTool().execute(items=[{"title": "A", "status": "completed"}, {"title": "B", "status": "in_progress"}])
    assert res.success
    assert res.metadata["items"][0]["title"] == "A"
    assert res.metadata["items"][0]["status"] == "completed"
    assert res.metadata["items"][1]["status"] == "in_progress"


def test_normalizes_unknown_status_to_pending():
    res = TodoWriteTool().execute(items=[{"title": "A", "status": "bogus"}, {"title": "B"}])
    assert res.success
    assert res.metadata["items"][0]["status"] == "pending"
    assert res.metadata["items"][1]["status"] == "pending"


def test_requires_items():
    res = TodoWriteTool().execute()
    assert not res.success


def test_skips_items_without_title():
    res = TodoWriteTool().execute(items=[{"status": "completed"}, {"title": "Keep"}])
    assert res.success
    assert len(res.metadata["items"]) == 1
    assert res.metadata["items"][0]["title"] == "Keep"

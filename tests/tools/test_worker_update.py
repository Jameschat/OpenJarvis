from pathlib import Path


def test_worker_update_refuses_primary_node(monkeypatch):
    from openjarvis.tools.worker_update import run_worker_update

    monkeypatch.setenv("JARVIS_NODE_ROLE", "primary")

    result = run_worker_update(runner=lambda _cmd: (_ for _ in ()).throw(AssertionError("should not run")))

    assert result["ok"] is False
    assert result["blocked"] is True
    assert "worker node" in result["error"]


def test_worker_update_runs_script_on_worker_node(monkeypatch, tmp_path):
    from openjarvis.tools.worker_update import run_worker_update

    script = tmp_path / "update-worker-node.ps1"
    script.write_text("Write-Host updated", encoding="utf-8")
    seen = {}

    def fake_runner(command):
        seen["command"] = command
        return 0, "Worker node updated and verified: jarvis-remote-ok", ""

    monkeypatch.setenv("JARVIS_NODE_ROLE", "worker")
    monkeypatch.setenv("JARVIS_NODE_ID", "worker-gpu")

    result = run_worker_update(script_path=script, runner=fake_runner)

    assert result["ok"] is True
    assert result["node"]["node_id"] == "worker-gpu"
    assert "jarvis-remote-ok" in result["stdout"]
    assert str(script) in seen["command"]

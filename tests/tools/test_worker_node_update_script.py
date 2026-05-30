from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_worker_node_update_script_has_role_and_dirty_tree_guards():
    script = (ROOT / "scripts" / "update-worker-node.ps1").read_text(
        encoding="utf-8"
    )

    assert "$env:JARVIS_NODE_ROLE -ne 'worker'" in script
    assert "AllowDirty" in script
    assert "git status --porcelain" in script
    assert "Refusing to update with dirty tracked files" in script


def test_worker_node_update_script_pulls_shared_repo_and_smokes_litellm():
    script = (ROOT / "scripts" / "update-worker-node.ps1").read_text(
        encoding="utf-8"
    )

    assert "git pull --ff-only" in script
    assert "/v1/chat/completions" in script
    assert "jarvis-remote-ok" in script
    assert "qwen3.6-35b-a3b-rotorquant" in script


def test_worker_node_update_script_pushes_status_from_worker_repo_root():
    script = (ROOT / "scripts" / "update-worker-node.ps1").read_text(
        encoding="utf-8"
    )

    assert "WorkerRepoRoot" in script
    assert "E:\\Claude\\OpenJarvis3090" in script
    assert "Push-Location $WorkerRepoRoot" in script
    assert 'git add "worker-node-status.json"' in script
    assert "git add $statusPath" not in script
    assert "No worker status changes to push" in script


def test_worker_env_example_documents_worker_identity():
    env_example = (ROOT / "configs" / "worker-node.env.example").read_text(
        encoding="utf-8"
    )

    assert "JARVIS_NODE_ROLE=worker" in env_example
    assert "JARVIS_NODE_ID=worker-3090" in env_example
    assert "JARVIS_WORKER_REPO=Jameschat/OpenJarvis3090" in env_example
    assert "JARVIS_WORKER_MODEL=qwen3.6-35b-a3b-rotorquant" in env_example

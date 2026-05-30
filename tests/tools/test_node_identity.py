from openjarvis.tools.node_identity import load_node_identity


def test_node_identity_defaults_to_primary(monkeypatch):
    for key in [
        "JARVIS_NODE_ROLE",
        "JARVIS_NODE_ID",
        "JARVIS_WORKER_MODEL",
        "JARVIS_WORKER_REPO",
    ]:
        monkeypatch.delenv(key, raising=False)

    identity = load_node_identity()

    assert identity["role"] == "primary"
    assert identity["node_id"] == "main-4090"
    assert identity["is_worker"] is False
    assert identity["worker_model"] == ""
    assert identity["worker_repo"] == ""


def test_node_identity_reads_worker_environment(monkeypatch):
    monkeypatch.setenv("JARVIS_NODE_ROLE", "worker")
    monkeypatch.setenv("JARVIS_NODE_ID", "worker-3090")
    monkeypatch.setenv("JARVIS_WORKER_MODEL", "qwen3.6-35b-a3b-rotorquant")
    monkeypatch.setenv("JARVIS_WORKER_REPO", "Jameschat/OpenJarvis3090")

    identity = load_node_identity()

    assert identity["role"] == "worker"
    assert identity["node_id"] == "worker-3090"
    assert identity["worker_model"] == "qwen3.6-35b-a3b-rotorquant"
    assert identity["worker_repo"] == "Jameschat/OpenJarvis3090"
    assert identity["is_worker"] is True


def test_node_identity_rejects_unknown_role(monkeypatch):
    monkeypatch.setenv("JARVIS_NODE_ROLE", "controller")
    monkeypatch.setenv("JARVIS_NODE_ID", "custom-node")

    identity = load_node_identity()

    assert identity["role"] == "primary"
    assert identity["node_id"] == "custom-node"
    assert identity["is_worker"] is False

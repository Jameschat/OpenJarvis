# Worker Node Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe worker-node role and update path so the second PC can stay aligned with Jarvis while remaining a worker, not the primary system.

**Architecture:** Add a small node identity module, surface it through Qwen runtime status, and provide a second-PC update script. Keep machine-specific config in environment variables and avoid secret sync.

**Tech Stack:** Python stdlib, PowerShell, existing Qwen runtime status tests, existing Studio/runtime conventions.

---

### Task 1: Node Identity Module

**Files:**
- Create: `src/openjarvis/tools/node_identity.py`
- Test: `tests/tools/test_node_identity.py`

- [ ] **Step 1: Write failing tests**

Create tests for default primary identity and explicit worker identity:

```python
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
```

- [ ] **Step 2: Implement minimal module**

Implement `load_node_identity()` with strict role normalization: only `primary` and `worker` are accepted; anything else falls back to `primary`.

- [ ] **Step 3: Run tests**

Run:

```powershell
& 'E:\Claude\OpenJarvis\.venv\Scripts\python.exe' -m pytest tests/tools/test_node_identity.py -q
```

Expected: `2 passed`.

### Task 2: Runtime Status Metadata

**Files:**
- Modify: `src/openjarvis/tools/qwen_runtime_status.py`
- Modify: `tests/tools/test_qwen_runtime_status.py`

- [ ] **Step 1: Add test**

Add a test proving runtime status includes `node` metadata and can accept injected worker identity.

- [ ] **Step 2: Implement**

Import `load_node_identity()` and add a `node` field to `load_qwen_runtime_status_from_data()`.

- [ ] **Step 3: Run tests**

Run:

```powershell
& 'E:\Claude\OpenJarvis\.venv\Scripts\python.exe' -m pytest tests/tools/test_node_identity.py tests/tools/test_qwen_runtime_status.py -q
```

Expected: all tests pass.

### Task 3: Worker Update Script

**Files:**
- Create: `scripts/update-worker-node.ps1`
- Create: `configs/worker-node.env.example`
- Test: `tests/tools/test_worker_node_update_script.py`

- [ ] **Step 1: Add script tests**

Test that the script contains the worker-role guard, dirty-tree guard, pull step, remote smoke test, and optional worker repo push marker.

- [ ] **Step 2: Implement script and env example**

The script runs on the second PC, pulls the shared branch, verifies the local LiteLLM worker route, writes a status JSON file, and optionally commits/pushes that status to the worker hardware repo.

- [ ] **Step 3: Run tests and parse script**

Run:

```powershell
& 'E:\Claude\OpenJarvis\.venv\Scripts\python.exe' -m pytest tests/tools/test_worker_node_update_script.py -q
```

Expected: tests pass.

### Task 4: Verification and Commit

**Files:**
- All touched files

- [ ] **Step 1: Run focused suite**

```powershell
& 'E:\Claude\OpenJarvis\.venv\Scripts\python.exe' -m pytest tests/tools/test_node_identity.py tests/tools/test_qwen_runtime_status.py tests/tools/test_worker_node_update_script.py -q
```

- [ ] **Step 2: Compile Python**

```powershell
& 'E:\Claude\OpenJarvis\.venv\Scripts\python.exe' -m py_compile src/openjarvis/tools/node_identity.py src/openjarvis/tools/qwen_runtime_status.py
```

- [ ] **Step 3: Commit scoped paths**

```powershell
git add docs/superpowers/specs/2026-05-30-worker-node-sync-design.md docs/superpowers/plans/2026-05-30-worker-node-sync.md src/openjarvis/tools/node_identity.py src/openjarvis/tools/qwen_runtime_status.py scripts/update-worker-node.ps1 configs/worker-node.env.example tests/tools/test_node_identity.py tests/tools/test_qwen_runtime_status.py tests/tools/test_worker_node_update_script.py
git commit -m "feat(qwen): add worker node sync path"
```

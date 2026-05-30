# Worker Node Sync Design

## Goal

Make the second PC a first-class Jarvis worker node that can stay updated without confusing its role with the main Jarvis controller.

## Scope

This design covers the safe first slice:

- Main PC remains the primary Jarvis controller.
- Second PC identifies as a worker node.
- Main PC can display/check the worker's health.
- The worker has a repeatable update script that pulls shared code and pushes worker-specific state to its own hardware repo.
- Secrets are not copied, synced, or committed.

This does not implement remote shell control from the main PC, automatic secret transfer, or forced updates pushed into the worker.

## Architecture

Jarvis gets a small node-role layer based on environment variables:

- `JARVIS_NODE_ROLE=primary` for the main PC.
- `JARVIS_NODE_ROLE=worker` for the second PC.
- `JARVIS_NODE_ID=main-4090` or `worker-3090`.

The role layer is read-only configuration. It lets the same OpenJarvis codebase behave differently on each machine without hardcoding machine identity throughout the app.

The worker node is updated by a local PowerShell script that runs on the second PC. It pulls the shared Jarvis branch, preserves local worker config, verifies LiteLLM and the model route, then optionally commits/pushes worker-specific status to `Jameschat/OpenJarvis3090`.

## Components

- `src/openjarvis/tools/node_identity.py`: reads and normalizes node role/id/model/repo settings.
- `src/openjarvis/tools/qwen_runtime_status.py`: includes node metadata in runtime status and keeps remote worker lane information visible.
- `scripts/update-worker-node.ps1`: runs on the second PC to pull updates, smoke-test the local worker route, and optionally push worker status.
- `configs/worker-node.env.example`: documents safe worker environment variables.
- Tests cover role defaults, worker env parsing, runtime-status node metadata, and update-script safety markers.

## Data Flow

1. Main PC pushes product updates to `Jameschat/OpenJarvis`.
2. Second PC runs `scripts/update-worker-node.ps1`.
3. Worker pulls the configured branch from the shared repo.
4. Worker keeps its hardware-specific environment/config local.
5. Worker tests `http://127.0.0.1:4000/v1/chat/completions`.
6. Worker may push status/config notes to `Jameschat/OpenJarvis3090`.
7. Main PC checks `192.168.1.191:4000` and displays the remote lane.

## Safety

- No secrets are written into the repo.
- The worker update script refuses to run if the working tree has dirty tracked files unless `-AllowDirty` is passed.
- The script does not delete files.
- The script does not disable firewalls or change Windows security settings.
- The main PC does not push code directly into the worker.

## Testing

Focused tests prove:

- default node identity is primary/main-4090;
- worker environment variables produce worker identity;
- runtime status exposes node identity;
- default runtime status includes the remote worker lane;
- worker update script contains the required safety checks and remote smoke test.

# Jarvis Agent Operating Layer Design

## Goal

Build the missing operating layer that lets Jarvis use local Qwen as a real project agent instead of a fast chatbot: Codex-style project chats, visible task runs, memory fusion, Superpowers-style workflows, verification gates, and durable handoffs.

## Current Assets

Jarvis already has the main ingredients:

- Obsidian vault at `E:\Claude\Obsidian\Claude\Brain` for long-term operator/project memory.
- CodeGraph index at `E:\Claude\OpenJarvis\.codegraph` for source-code structure.
- Graphify/vault graph endpoints and HUD visualization.
- AgentMemory client integration and sidecar config, with graceful degradation when offline.
- Qwen local department and BeeLlama DFlash route through LiteLLM.
- Agent runner, project plan persistence, session notes, schedules, and outcome records.
- Codex-side Superpowers installed locally, but not enforced inside Jarvis.

The gap is orchestration: Jarvis does not consistently gather the right context before acting, expose progress while acting, or require verification evidence before saying work is complete.

## User Experience

Jarvis Studio becomes a Codex-like workspace inside Operations Center:

- Left rail: New chat, Search, Plugins, Automations, Projects, Chat history, Settings.
- Center: project conversation stream, work status blocks, and a composer for instructions.
- Right drawer: project files, memory signals, CodeGraph/Graphify/AgentMemory status, plugins, automations, model/runtime state, and run controls.
- Mobile: hamburger/project drawer, full-width chat, slide-over context drawer.
- Startup: Studio shows a cyan-on-black boot screen while it loads project state and memory/tool health. The visual language should echo falling code/data streams with a centered `LOADING...` panel and progress bar, then fade into the workspace. It must be CSS/canvas-based, non-blocking, and hidden once the first `/studio/state` load completes or a visible error is shown.

The operator should be able to create a project, discuss it through to completion, launch local Qwen tasks, see progress, review evidence, and keep all memory attached to that project.

## Scope

### 1. Jarvis Studio Workspace

Add a new `/studio` page and API surface for projects, chats, and sessions. V1 should persist enough state for real use:

- projects
- chats
- messages
- task runs
- run events
- memory snapshots

The first implementation can use JSON files under `~/.openjarvis/studio/` to avoid a database migration. It must be structured so SQLite can replace it later without changing the browser API.

### 2. Project Context Pack

Before Qwen answers or acts inside Studio, Jarvis builds a context pack:

- project metadata and recent chat messages
- Obsidian project notes and recent session handoffs
- AgentMemory hits when the sidecar is online
- CodeGraph status and, for code tasks, a source-code signal
- Graphify/vault status
- current runtime/model status

The pack is visible in the right drawer and passed into agent prompts.

### 3. Native Workflow Engine

Add Superpowers-style workflow states inside Jarvis:

- `brainstorm`
- `spec`
- `plan`
- `execute`
- `debug`
- `verify`
- `review`
- `handoff`

Studio does not need every Superpowers feature in v1. It needs a deterministic state machine that can say: this task needs a spec first, this bug needs reproduction first, this frontend change needs browser verification, this task is allowed to execute now.

### 4. Visible Task Runs

When the operator starts a task, Studio creates a run with live events:

- queued
- building context
- selecting workflow
- running local Qwen
- dispatching agent/tool
- reading/writing files
- running verification
- writing memory
- completed/failed/blocked

The browser must show these events instead of vague “running long” messages.

### 5. Verification Gates

Jarvis cannot mark a task complete unless it records evidence. V1 gates:

- command/test evidence when code changes are made
- browser/screenshot evidence for UI changes when available
- API smoke evidence for backend routes
- git diff summary for repo changes
- memory writeback evidence
- explicit blocker evidence if verification cannot run

The result can be `completed`, `completed_with_warnings`, `blocked`, or `failed`. It must not silently become “done”.

### 6. Memory Writeback

Every completed or blocked Studio run writes:

- a Studio run record
- a session note in the Obsidian vault
- optional project state update if the project maps to `Brain/Projects/<slug>/STATE.md`
- AgentMemory event when sidecar is online

The writeback must avoid secrets and should cross-link relevant vault notes.

### 7. Plugin, Search, and Automation Panels

V1 panels should be functional enough to support the operating layer:

- Search: query chats, projects, vault summaries, and local run records.
- Plugins: show available tool surfaces and health, including CodeGraph, Graphify, AgentMemory, Browser, Markets, Qwen, Claude/Codex escalation.
- Automations: list scheduled Jarvis jobs and project-linked recurring tasks.

Creating new plugins or automations can remain v1.1 if needed, but the panels must not be inert.

## Non-Goals

- Do not replace OpenJarvis with OpenHands, Aider, SWE-agent, or another framework.
- Do not give Qwen unrestricted filesystem or shell access.
- Do not add live trading or external account mutation.
- Do not edit `jarvis.bat`; it contains secrets and is gitignored.
- Do not claim Claude/Codex-level quality from tok/s alone.

## Architecture

Add a focused Studio subsystem:

- `openjarvis.tools.studio_store`: JSON-backed project/chat/run persistence.
- `openjarvis.tools.studio_context`: project context pack builder across vault, CodeGraph, Graphify, AgentMemory, and runtime status.
- `openjarvis.tools.studio_workflows`: workflow selection and state rules.
- `openjarvis.tools.studio_runner`: run lifecycle, event recording, Qwen/agent dispatch hooks, verification state.
- `openjarvis.cli.brain_server`: HTTP endpoints and SSE bridge.
- `jarvis_web/studio.html`: Codex-style workspace UI.

Keep boundaries small. The browser talks to Studio endpoints. The runner talks to existing Qwen/agent/memory tooling. The store owns persistence.

## Data Model

Project:

```json
{
  "id": "openjarvis",
  "title": "OpenJarvis",
  "created_at": "2026-05-25T20:00:00Z",
  "updated_at": "2026-05-25T20:00:00Z",
  "repo_root": "E:\\Claude\\OpenJarvis",
  "vault_project": "OpenJarvis",
  "status": "active"
}
```

Chat:

```json
{
  "id": "chat-...",
  "project_id": "openjarvis",
  "title": "Build Jarvis Studio workspace",
  "created_at": "2026-05-25T20:00:00Z",
  "updated_at": "2026-05-25T20:00:00Z"
}
```

Message:

```json
{
  "id": "msg-...",
  "chat_id": "chat-...",
  "role": "operator|jarvis|system",
  "content": "message text",
  "created_at": "2026-05-25T20:00:00Z",
  "run_id": "run-..."
}
```

Run:

```json
{
  "id": "run-...",
  "project_id": "openjarvis",
  "chat_id": "chat-...",
  "prompt": "operator request",
  "workflow": "execute",
  "status": "queued|running|completed|completed_with_warnings|blocked|failed",
  "model": "qwen3.6-27b-local",
  "created_at": "2026-05-25T20:00:00Z",
  "updated_at": "2026-05-25T20:00:00Z",
  "evidence": []
}
```

Run event:

```json
{
  "ts": "2026-05-25T20:00:00Z",
  "type": "context|workflow|tool|verification|memory|status",
  "message": "Building project context pack",
  "data": {}
}
```

## Safety

- File writes must stay inside allowed workspaces or existing Jarvis agent workspaces.
- External mutation, package installation, account connection, live trading, and destructive commands require explicit approval.
- Secrets must never be written to vault notes, Studio JSON, or browser payloads.
- Verification failure must be visible as blocked/failed, not converted into success.

## Testing

Required coverage:

- Store CRUD and corruption handling.
- Context pack degrades when AgentMemory or live server endpoints are offline.
- Workflow selection for build/debug/research/verify requests.
- Run lifecycle records ordered events and final status.
- Brain server exposes Studio static page and JSON endpoints.
- Studio HTML contains functional navigation, composer, context drawer, mobile drawer affordances, and no inert primary buttons.

## Acceptance Criteria

- `/studio` opens a Codex-style Jarvis workspace.
- `/studio` displays an animated cyber boot/loading screen before the workspace appears.
- The operator can create/select a project and chat.
- Sending a message creates a persistent message and a visible run.
- The run records context, workflow, progress, verification, and memory events.
- The right drawer shows memory/tool health from real endpoints or clear offline states.
- Search returns local Studio records.
- Plugins and automations panels show real health/list data.
- Jarvis writes a session note for completed/blocked runs.
- Tests pass for the new store, context, workflow, runner, server routes, and Studio page.

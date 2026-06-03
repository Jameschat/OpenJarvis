# Jarvis Desktop Studio Shell Design

## Status

Design draft for upgrading the existing Jarvis desktop app into the primary Codex-style operating surface.

## Context

Jarvis currently has two desktop-facing paths:

- `frontend/`: a React + Tauri desktop app with routes, native plugins, API helpers, memory/data-source screens, agent pages, command palette, notifications, and system panels.
- `src/openjarvis/desktop/app.py`: a pywebview wrapper that opens the existing Studio web page in a native WebView2 window.

The pywebview wrapper is useful as a fast bridge, but it still feels like a browser page in a window. The right long-term app should use the existing React/Tauri frontend as the main shell and consume the existing Jarvis backend APIs. The backend remains the engine for Qwen, Studio runs, memory, CodeGraph, vault context, remote workers, tools, automations, and approvals.

## Goals

- Make Jarvis feel like a real desktop app rather than an HTML dashboard.
- Bring the Studio project/chat/task workflow into the Tauri app as first-class React screens.
- Build the Brain experience into the app: vault, AgentMemory, CodeGraph, Graphify, project state, context handoffs, and memory recall.
- Preserve the existing backend and Studio API contracts so current browser Studio keeps working during migration.
- Improve usability for long-running Qwen tasks: visible progress, stop/cancel, outputs, tool calls, verification, files touched, and worker status.
- Keep the migration safe and incremental.

## Non-Goals

- Do not replace the Jarvis backend in this phase.
- Do not remove `jarvis_web/studio.html` until the Tauri Studio route is functionally equivalent.
- Do not give Qwen unrestricted shell, package install, or direct file-edit authority as part of the UI migration.
- Do not merge or rewrite Obsidian, AgentMemory, CodeGraph, and Graphify storage into one database yet. The app visualizes and orchestrates them first.

## Recommended Architecture

Use the existing Tauri app in `frontend/` as the main Jarvis desktop shell.

The app should add a new route:

```text
/studio
```

This route becomes the Codex-style Jarvis workspace:

- Left rail: projects, chats, plugins, automations, settings, search.
- Center: conversation, task replies, project workspaces, website/app previews.
- Right rail: progress, context, system health, Qwen runtime, worker node, outputs, sources, agents, file activity.
- Bottom composer: prompt input, stop button, profile selector, permissions selector, context attachments, steer/branch controls.

The React route should call the same backend endpoints that `jarvis_web/studio.html` currently uses:

- `/studio/state`
- `/studio/projects`
- `/studio/chats`
- `/studio/runs`
- `/studio/preview`
- `/studio/search`
- `/studio/qwen-profile`
- `/studio/worker-update`
- `/studio/qwen-proposals/apply`
- `/studio/runs/{id}/cancel`

This keeps backend behavior stable while improving the user interface.

## Desktop Brain Panels

The desktop app should expose Brain panels as app-native surfaces rather than hidden files:

- **Memory Recall**: search vault and AgentMemory, show source, confidence, timestamps, and related memories.
- **Code Context**: CodeGraph status, active repo, changed files, code entities, recent activity.
- **Project State**: render `PROJECT.md`, `REQUIREMENTS.md`, `ROADMAP.md`, `STATE.md`, and `CONTEXT.md` for the selected project.
- **Context Handoff**: show context pressure, saved handoff notes, continuation chat links, and current session summary.
- **Graph View**: visual summary of Vault, AgentMemory, CodeGraph, Graphify, Qwen, agents, markets, and learning loops.

The app should not require the operator to open Obsidian or raw markdown for normal use, but the raw vault remains the source of truth.

## Task Progress Model

Every Studio run should render a Codex-style live progress stack:

- Map request and create run.
- Load project, vault, memory, and CodeGraph context.
- Select workflow and verification path.
- Fetch web/GitHub research if needed.
- Route work to Qwen or escalation agent.
- Show tools requested and results returned.
- Show files proposed/written.
- Show verification evidence.
- Return final answer and next action.

The UI should show:

- Active agent and role.
- Selected Qwen profile.
- ECC/Superpowers/UI-UX skills in use.
- Tool calls and blocked capabilities.
- Outputs and artifact links.
- Stop/cancel state.
- Failure reason when a run times out or fails.

## Native Desktop Features

The Tauri app should add desktop capabilities that are hard to do well in a browser:

- Tray icon with backend status, Qwen status, and open/quit actions.
- Native notifications for completed tasks, failed tasks, approvals, worker offline, and long-running tasks.
- Command palette for New Chat, New Project, Open Brain, Open Preview, Update Worker, Change Qwen Profile.
- Popup/drawer panels for memory recall, file activity, task details, and approvals.
- Native file picker for adding context files/images.
- Worker update progress surfaced as a desktop notification and progress row.

## Migration Plan

### Phase 1 - Tauri Studio Route

Create the `/studio` React route with the Codex-style layout and connect it to current Studio APIs. Do not remove existing HTML Studio.

Acceptance:

- Tauri app can open Studio from sidebar.
- Chat list, project list, messages, composer, and run list load from backend.
- Sending prompts creates Studio runs.
- Stop/cancel works.
- Right rail shows progress, outputs, sources, agents, Qwen runtime, and worker status.
- Existing browser Studio remains usable.

### Phase 2 - Brain Panels

Add app-native Brain panels backed by existing vault, memory, and CodeGraph endpoints.

Acceptance:

- Search memory from the app.
- View project state files from the app.
- See CodeGraph and AgentMemory status.
- Open context handoff notes.
- Show context pressure and continuation chat state.

### Phase 3 - Native Controls

Add Tauri-specific desktop integrations.

Acceptance:

- Tray status is visible.
- Notifications fire for completed/failed/approval-needed tasks.
- Command palette includes Studio and Brain actions.
- Native file picker can attach context to a prompt.
- Worker update shows progress and result.

### Phase 4 - Retire Wrapper

Once the Tauri Studio route is equivalent or better, demote the pywebview wrapper to fallback/legacy.

Acceptance:

- Operator can perform normal Jarvis work entirely inside the Tauri app.
- Browser Studio is retained as fallback.
- `jarvis-app` documentation points to Tauri as the recommended desktop experience.

## Risks and Controls

- **Risk: UI migration breaks task execution.** Control: keep backend APIs unchanged and keep browser Studio live.
- **Risk: Tauri app duplicates Studio logic.** Control: API calls and state contracts should remain shared; avoid reimplementing workflow decisions in frontend.
- **Risk: native app hides failures.** Control: show raw status, errors, and task IDs in progress details.
- **Risk: Qwen still times out.** Control: this UI migration improves visibility and controls, but runtime reliability remains a separate backend issue.
- **Risk: context panels become crowded again.** Control: use scrollable rails, collapsible cards, and detail drawers rather than one long panel.

## Testing Strategy

- Add frontend tests or static marker tests for the new Studio route and panels.
- Keep existing Python Studio tests for backend behavior.
- Add API contract tests for fields the Tauri Studio route depends on.
- Run `npm run build` for frontend compile when dependencies are available.
- Run focused Python tests for Studio runner/store/web state.
- Verify the built Tauri app can load the route and talk to the backend.

## Open Decisions

- Whether `/studio` becomes the default Tauri landing page immediately or after Phase 1 reaches parity.
- Whether the old HTML Studio should embed inside Tauri temporarily as a fallback tab.
- Whether project website previews should appear inside the center workspace or as a separate pop-out preview window.

## Recommendation

Start with Phase 1. Build a Tauri `/studio` route that consumes the existing Studio APIs and recreates the Codex-style shell in React. This gives the operator the biggest usability improvement without destabilizing Jarvis's backend, Qwen routing, memory systems, or worker-node setup.

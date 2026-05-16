# Jarvis OS Design

Date: 2026-05-10
Status: Approved visual direction, ready for implementation planning

## Goal

Build a Jarvis OS surface inside Operations Center: a browser-based operating shell for running Jarvis sessions, projects, plugins, and autonomous work. It must feel like entering a Jarvis desktop rather than opening another dashboard.

The shell is local-first. Its default work engine is `qwen3.6:27b` through the existing LiteLLM/Ollama route. Claude and Codex are available only as escalation helpers when Qwen needs specialist backup, higher-confidence coding assistance, or learning/research support.

## Product Direction

Jarvis OS is a desktop-first widget shell:

- wallpaper-style background
- large ambient clock / desktop surface
- translucent Start menu
- centered bottom taskbar
- desktop shortcuts
- floating app windows
- always-visible widgets for live Jarvis state

The visual style should be premium and OS-like, closer to a dark Windows 11 command desktop than to a web admin panel. The reference direction is a polished desktop with widgets and translucent window surfaces.

## Entry Point

Operations Center gets a `Jarvis OS` button.

Clicking the button opens the Jarvis OS shell full-screen in the existing browser app. The first implementation can use an internal route such as `/jarvis-os` and reuse the existing Jarvis web server rather than starting a separate process.

## First Screen

The first screen shows the desktop, not a project dashboard. It should include:

- desktop shortcuts: Jarvis, Missions, Projects, Plugins, Memory, Models, Alerts
- large clock / ambient status panel
- bottom taskbar with Start, active apps, and status tray
- Start menu with New Mission, New Project, Plugin Studio, Model Center, Memory, Settings
- floating mission window or quick command surface
- desktop widgets for the current Jarvis state

## Required Widgets

Widgets are first-class desktop tiles, not secondary dashboard cards:

- Qwen 3.6 27B local model status
- active missions
- agent activity / queue
- plugin learning queue
- market pulse
- GPU / local system load
- calendar / scheduled work
- inbox / approvals / escalations
- Brain memory / recall pulse

Use real data from existing endpoints where available. Where backend support does not yet exist, ship a clear placeholder state and keep the widget contract stable for later wiring.

## Core Apps

### Mission Session

The main workbench for asking Jarvis to do work. It should support:

- chat transcript
- plan view
- tool-call / command log
- file/result links
- escalation requests
- local Qwen-first execution status

### Projects

Project launcher for session-based work. It should show existing project sessions where available and provide a simple path to create a new Jarvis project mission.

### Plugin Studio

Plugin and skill creation surface. It should support the first local workflow:

- search GitHub or describe a needed capability
- review candidate tool/plugin/repo metadata
- create a local plugin or skill scaffold
- test locally
- request approval before installing unknown packages, touching secrets, connecting accounts, spending money, deleting data, or mutating external systems

### Operations Board

Live operational view for:

- active missions
- queued agents
- tool health
- model state
- escalations and approvals
- recent failures or blocked tasks

### Model Center

Shows the model policy:

- primary: local `qwen3.6:27b`
- cloud fallback in existing LiteLLM chain remains available for normal Jarvis runtime paths
- Claude/Codex: standby escalation helpers, not default Jarvis OS engines

The UI should make the local-first mode obvious.

### Memory

Brain-oriented surface for:

- search / recall
- recent notes
- learning queue
- capability gaps
- project context

## Model And Escalation Policy

Jarvis OS must prefer local Qwen for ordinary sessions and project work.

Escalate to Claude or Codex only when at least one condition is true:

- Qwen explicitly lacks confidence on a coding or architecture task
- a specialist assistant is likely to materially improve the result
- the task requires codebase editing where Codex is the safer execution agent
- the task is a learning/research loop where an external assistant is used to compare or critique

The shell should surface escalation as a visible decision, not an invisible background handoff.

## Safety Rules

Jarvis OS can plan, inspect, draft, scaffold, and test locally.

It must ask approval before:

- installing unknown packages
- editing `jarvis.bat`
- exposing or printing secrets
- connecting accounts
- spending money
- placing trades
- deleting data
- making irreversible external changes
- granting persistent tool permissions

These rules match the existing Jarvis autonomous capability doctrine.

## Architecture

Use the existing OpenJarvis web stack unless implementation discovery proves that impossible.

Expected shape:

- static/HTML route for the Jarvis OS shell
- lightweight JavaScript state/controller layer for windows, Start menu, taskbar, and widget refresh
- CSS isolated to the Jarvis OS route to avoid regressing Operations Center mobile/layout fixes
- backend endpoints reused where available
- small new endpoints only for missing widget data that already exists in local Jarvis state

The first implementation should avoid a full frontend framework migration. The goal is to ship a usable shell inside the current app, not rebuild the web client.

## Data Flow

Jarvis OS loads with a shell state payload:

- model status
- active missions / agents
- pending approvals
- recent memory or recall pulse
- scheduled items
- market pulse
- GPU/system status where locally available

Widgets refresh on a modest interval. User actions open app windows in the shell. Mission Session sends work through the existing Jarvis backend pathways, with local Qwen as the visible default.

## First Build Scope

The first build should include:

- Operations Center `Jarvis OS` button
- `/jarvis-os` shell route
- desktop-first layout with taskbar, Start menu, shortcuts, and widgets
- open/close behavior for the core app windows
- Mission Session placeholder wired enough to start a local-Qwen mission or clearly show the next backend step
- real data for any endpoint already available
- stable placeholder widgets for missing backend data
- responsive layout that remains usable on mobile, even if desktop is the primary target

Out of scope for the first build:

- replacing Windows
- native desktop app packaging
- draggable/resizable window physics beyond simple open/close/snap states
- automatic plugin installation without approval
- changing the LiteLLM fallback chain
- deleting old Qwen models

## Testing

Minimum verification:

- route loads from Operations Center
- Jarvis OS button opens the shell
- desktop layout is usable at desktop and mobile widths
- Start menu opens/closes
- app windows open/close
- widgets render without overlapping
- no regression to existing Operations Center layout
- any new backend endpoint has focused tests

Use browser screenshot checks for desktop and mobile before declaring the UI complete.

## Open Implementation Questions

- Which existing endpoint is best for active mission/session data?
- Which market pulse data is currently available without starting a costly fetch?
- Is GPU status already exposed to the web layer, or should the first build use a placeholder?
- Should Mission Session post through the existing chat route first, or use a dedicated Jarvis OS session endpoint?

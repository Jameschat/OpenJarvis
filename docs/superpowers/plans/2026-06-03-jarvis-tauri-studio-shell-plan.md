# Jarvis Tauri Studio Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Phase 1 of the Jarvis desktop evolution: a React/Tauri `/studio` route that feels like a Codex-style desktop workspace while reusing the existing Studio backend APIs.

**Architecture:** Keep the Python/Jarvis backend and existing `jarvis_web/studio.html` intact. Add a Tauri React Studio route in `frontend/src/pages/StudioPage.tsx`, backed by typed API helpers in `frontend/src/lib/studio-api.ts` and small focused React components under `frontend/src/components/Studio/`.

**Tech Stack:** React 19, React Router 7, TypeScript, Vite, Tauri 2, existing Jarvis `/studio/*` backend APIs, existing Python static marker tests, `npm run build` for TypeScript/frontend validation.

---

## File Structure

- Create `frontend/src/lib/studio-api.ts`  
  Typed API wrappers for `/studio/state`, `/studio/runs`, `/studio/preview`, `/studio/qwen-profile`, `/studio/worker-update`, and run cancellation.

- Create `frontend/src/pages/StudioPage.tsx`  
  Main Codex-style desktop Studio route. Owns selected project/chat, polling, composer state, active run detection, and layout composition.

- Create `frontend/src/components/Studio/StudioSidebar.tsx`  
  Left Studio rail for projects, chats, search, plugins, automations, and settings links.

- Create `frontend/src/components/Studio/StudioThread.tsx`  
  Center conversation, message rendering, timestamps, empty state, and auto-scroll.

- Create `frontend/src/components/Studio/StudioComposer.tsx`  
  Bottom prompt composer with return-to-send, shift-return newline, stop/cancel button, Qwen profile selector, permissions label, and context action controls.

- Create `frontend/src/components/Studio/StudioContextRail.tsx`  
  Right-side progress/context rail with scrollable cards for Progress, Qwen Runtime, System Health, Worker, Outputs, Agents, Sources, ECC skills, and File Activity.

- Create `frontend/src/components/Studio/types.ts`  
  Shared frontend-only types for Studio state, projects, chats, messages, runs, runtime lanes, outputs, and agents.

- Modify `frontend/src/App.tsx`  
  Register `/studio`.

- Modify `frontend/src/components/Sidebar/Sidebar.tsx`  
  Add a Studio navigation item.

- Modify `frontend/src/index.css`  
  Add focused Studio layout classes. Keep styling local to `.studio-shell` to avoid changing existing Chat/Dashboard pages.

- Modify `tests/web/test_studio.py`  
  Add static markers proving the Tauri frontend has a Studio route, API client, layout components, stop/cancel, right rail, and sidebar navigation.

- Modify `E:\Claude\Obsidian\Claude\Brain\Projects\OpenJarvis\STATE.md` at completion  
  Record the implementation result and verification.

---

## Task 1: Add Static Contract Tests for the Tauri Studio Route

**Files:**
- Modify: `tests/web/test_studio.py`

- [ ] **Step 1: Write failing route/component marker test**

Add this test near the existing Studio web tests:

```python
def test_tauri_frontend_has_studio_route_and_components():
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    sidebar = (ROOT / "frontend" / "src" / "components" / "Sidebar" / "Sidebar.tsx").read_text(encoding="utf-8")
    page = (ROOT / "frontend" / "src" / "pages" / "StudioPage.tsx").read_text(encoding="utf-8")

    assert "StudioPage" in app
    assert 'path="studio"' in app
    assert "label: 'Studio'" in sidebar or 'label: "Studio"' in sidebar
    assert "StudioSidebar" in page
    assert "StudioThread" in page
    assert "StudioComposer" in page
    assert "StudioContextRail" in page
```

- [ ] **Step 2: Write failing API/client marker test**

Add this test after the route marker test:

```python
def test_tauri_frontend_has_studio_api_client():
    api = (ROOT / "frontend" / "src" / "lib" / "studio-api.ts").read_text(encoding="utf-8")

    for marker in [
        "fetchStudioState",
        "startStudioRun",
        "cancelStudioRun",
        "setStudioQwenProfile",
        "startStudioPreview",
        "updateStudioWorker",
        "/studio/state",
        "/studio/runs",
        "/studio/qwen-profile",
        "/studio/worker-update",
    ]:
        assert marker in api
```

- [ ] **Step 3: Run tests and verify they fail**

Run:

```powershell
cd E:\Claude\OpenJarvis
.\.venv\Scripts\python.exe -m pytest tests\web\test_studio.py::test_tauri_frontend_has_studio_route_and_components tests\web\test_studio.py::test_tauri_frontend_has_studio_api_client -q
```

Expected: both tests fail because `StudioPage.tsx` and `studio-api.ts` do not exist yet.

- [ ] **Step 4: Commit tests**

```powershell
git add tests/web/test_studio.py
git commit -m "test(studio): cover Tauri Studio shell markers"
```

---

## Task 2: Add Typed Studio API Client

**Files:**
- Create: `frontend/src/components/Studio/types.ts`
- Create: `frontend/src/lib/studio-api.ts`
- Test: `tests/web/test_studio.py`

- [ ] **Step 1: Create shared Studio types**

Create `frontend/src/components/Studio/types.ts`:

```ts
export interface StudioProject {
  id: string;
  title?: string;
  repo_root?: string;
  vault_project?: string;
}

export interface StudioMessage {
  id?: string;
  role: 'operator' | 'jarvis' | 'system' | string;
  content: string;
  created_at?: string;
  run_id?: string;
}

export interface StudioChat {
  id: string;
  project_id?: string;
  title?: string;
  status?: string;
  messages?: StudioMessage[];
  updated_at?: string;
}

export interface StudioRunEvent {
  type: string;
  message?: string;
  details?: Record<string, unknown>;
  ts?: string;
}

export interface StudioRun {
  id: string;
  project_id?: string;
  chat_id?: string;
  prompt?: string;
  status?: string;
  workflow?: string;
  created_at?: string;
  updated_at?: string;
  events?: StudioRunEvent[];
  tasks?: string[];
  task_details?: Array<Record<string, unknown>>;
  progress_summary?: string;
  ecc_lite_skills?: string[];
  outputs?: Array<Record<string, unknown>>;
  file_activity?: Array<Record<string, unknown>>;
}

export interface StudioAgent {
  id?: string;
  name?: string;
  status?: string;
  provider?: string;
  department?: string;
}

export interface StudioQwenProfile {
  active?: 'fast' | 'quality' | 'remote' | string;
  profiles?: Record<string, { label?: string; model?: string; summary?: string }>;
}

export interface StudioRuntimeLane {
  id?: string;
  alias?: string;
  label?: string;
  online?: boolean;
  active?: boolean;
  latest_tok_s?: number;
  status?: string;
}

export interface StudioState {
  projects?: StudioProject[];
  chats?: StudioChat[];
  runs?: StudioRun[];
  agents?: StudioAgent[];
  plugins?: Array<Record<string, unknown>>;
  automations?: Array<Record<string, unknown>>;
  qwen_profile?: StudioQwenProfile;
  qwen_runtime?: { lanes?: StudioRuntimeLane[]; active?: string };
  runtime_health?: Record<string, unknown>;
  system?: Record<string, unknown>;
  approved?: boolean;
}
```

- [ ] **Step 2: Create API client**

Create `frontend/src/lib/studio-api.ts`:

```ts
import { getBase } from './api';
import type { StudioState } from '../components/Studio/types';

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getBase()}${path}`, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText);
    throw new Error(`${path} failed: ${response.status} ${detail}`);
  }
  return response.json() as Promise<T>;
}

export async function fetchStudioState(projectId?: string, chatId?: string): Promise<StudioState> {
  const params = new URLSearchParams();
  if (projectId) params.set('project_id', projectId);
  if (chatId) params.set('chat_id', chatId);
  const suffix = params.toString() ? `?${params.toString()}` : '';
  return requestJson<StudioState>(`/studio/state${suffix}`);
}

export async function startStudioRun(input: {
  projectId: string;
  chatId: string;
  prompt: string;
  approved?: boolean;
  branchFromMessageId?: string;
}): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>('/studio/runs', {
    method: 'POST',
    body: JSON.stringify({
      project_id: input.projectId,
      chat_id: input.chatId,
      prompt: input.prompt,
      approved: Boolean(input.approved),
      branch_from_message_id: input.branchFromMessageId || '',
    }),
  });
}

export async function cancelStudioRun(runId: string): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>(`/studio/runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

export async function setStudioQwenProfile(profile: 'fast' | 'quality' | 'remote'): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>('/studio/qwen-profile', {
    method: 'POST',
    body: JSON.stringify({ profile }),
  });
}

export async function startStudioPreview(projectId: string): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>('/studio/preview', {
    method: 'POST',
    body: JSON.stringify({ project_id: projectId }),
  });
}

export async function updateStudioWorker(): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>('/studio/worker-update', {
    method: 'POST',
    body: JSON.stringify({}),
  });
}
```

- [ ] **Step 3: Run API marker test**

Run:

```powershell
cd E:\Claude\OpenJarvis
.\.venv\Scripts\python.exe -m pytest tests\web\test_studio.py::test_tauri_frontend_has_studio_api_client -q
```

Expected: PASS.

- [ ] **Step 4: Run TypeScript build**

Run:

```powershell
cd E:\Claude\OpenJarvis\frontend
npm run build
```

Expected: TypeScript compile succeeds. If dependencies are missing, run `npm install` only with operator approval.

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/components/Studio/types.ts frontend/src/lib/studio-api.ts tests/web/test_studio.py
git commit -m "feat(studio): add Tauri Studio API client"
```

---

## Task 3: Add Tauri Studio Route Skeleton and Sidebar Entry

**Files:**
- Create: `frontend/src/pages/StudioPage.tsx`
- Create: `frontend/src/components/Studio/StudioSidebar.tsx`
- Create: `frontend/src/components/Studio/StudioThread.tsx`
- Create: `frontend/src/components/Studio/StudioComposer.tsx`
- Create: `frontend/src/components/Studio/StudioContextRail.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Sidebar/Sidebar.tsx`
- Test: `tests/web/test_studio.py`

- [ ] **Step 1: Create initial components with real layout roles**

Create `frontend/src/components/Studio/StudioSidebar.tsx`:

```tsx
import type { StudioChat, StudioProject } from './types';

export function StudioSidebar({
  projects,
  chats,
  activeProjectId,
  activeChatId,
  onSelectProject,
  onSelectChat,
}: {
  projects: StudioProject[];
  chats: StudioChat[];
  activeProjectId: string;
  activeChatId: string;
  onSelectProject: (id: string) => void;
  onSelectChat: (id: string) => void;
}) {
  return (
    <aside className="studio-panel studio-left-rail" aria-label="Studio projects and chats">
      <button className="studio-primary-action" type="button">New chat</button>
      <div className="studio-rail-section">Projects</div>
      {projects.map((project) => (
        <button
          key={project.id}
          type="button"
          className={project.id === activeProjectId ? 'studio-row active' : 'studio-row'}
          onClick={() => onSelectProject(project.id)}
        >
          {project.title || project.id}
        </button>
      ))}
      <div className="studio-rail-section">Chats</div>
      {chats.map((chat) => (
        <button
          key={chat.id}
          type="button"
          className={chat.id === activeChatId ? 'studio-row active' : 'studio-row'}
          onClick={() => onSelectChat(chat.id)}
        >
          {chat.title || 'New chat'}
        </button>
      ))}
    </aside>
  );
}
```

Create `frontend/src/components/Studio/StudioThread.tsx`:

```tsx
import type { StudioMessage } from './types';

function formatTime(value?: string) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function StudioThread({ messages }: { messages: StudioMessage[] }) {
  return (
    <section className="studio-thread" aria-label="Studio conversation">
      {messages.length === 0 ? (
        <div className="studio-empty-state">
          <h1>Jarvis Studio</h1>
          <p>Plan projects, run Qwen workflows, inspect memory, and track outputs from one desktop workspace.</p>
        </div>
      ) : (
        messages.map((message, index) => (
          <article key={message.id || `${message.role}-${index}`} className={`studio-message ${message.role}`}>
            <div className="studio-bubble">{message.content}</div>
            <time>{formatTime(message.created_at)}</time>
          </article>
        ))
      )}
    </section>
  );
}
```

Create `frontend/src/components/Studio/StudioComposer.tsx`:

```tsx
import { useState } from 'react';

export function StudioComposer({
  disabled,
  running,
  qwenProfile,
  onSend,
  onCancel,
}: {
  disabled?: boolean;
  running?: boolean;
  qwenProfile?: string;
  onSend: (prompt: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState('');

  const submit = () => {
    const prompt = value.trim();
    if (!prompt || disabled || running) return;
    setValue('');
    onSend(prompt);
  };

  return (
    <form className="studio-composer" onSubmit={(event) => { event.preventDefault(); submit(); }}>
      <textarea
        value={value}
        aria-label="Ask Jarvis to plan, build, test, search memory, or run a local Qwen workflow"
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
      />
      <div className="studio-composer-bar">
        <span>Qwen {qwenProfile || 'fast'}</span>
        <span>Default permissions</span>
        {running ? (
          <button type="button" className="studio-stop-button" onClick={onCancel}>Stop</button>
        ) : (
          <button type="submit" className="studio-send-button" disabled={disabled}>Send</button>
        )}
      </div>
    </form>
  );
}
```

Create `frontend/src/components/Studio/StudioContextRail.tsx`:

```tsx
import type { StudioAgent, StudioRun, StudioRuntimeLane } from './types';

function latestRun(runs: StudioRun[]) {
  return [...runs].sort((a, b) => String(b.updated_at || b.created_at || '').localeCompare(String(a.updated_at || a.created_at || '')))[0];
}

export function StudioContextRail({
  runs,
  agents,
  lanes,
}: {
  runs: StudioRun[];
  agents: StudioAgent[];
  lanes: StudioRuntimeLane[];
}) {
  const run = latestRun(runs);
  const skills = run?.ecc_lite_skills || [];
  return (
    <aside className="studio-panel studio-context-rail" aria-label="Studio progress and context">
      <section className="studio-card">
        <h2>Progress</h2>
        <p>{run?.progress_summary || run?.status || 'idle'}</p>
        <div className="studio-chip-row">
          {skills.map((skill) => <span key={skill} className="studio-chip">{skill}</span>)}
        </div>
      </section>
      <section className="studio-card">
        <h2>Qwen Runtime</h2>
        {lanes.slice(0, 4).map((lane) => (
          <p key={lane.id || lane.alias}>{lane.label || lane.alias || lane.id}: {lane.online ? 'online' : 'offline'}</p>
        ))}
      </section>
      <section className="studio-card">
        <h2>Agents</h2>
        {agents.slice(0, 8).map((agent) => (
          <p key={agent.id || agent.name}>{agent.name || agent.id}: {agent.status || 'idle'}</p>
        ))}
      </section>
    </aside>
  );
}
```

- [ ] **Step 2: Create Studio page shell**

Create `frontend/src/pages/StudioPage.tsx`:

```tsx
import { useEffect, useMemo, useState } from 'react';
import { StudioSidebar } from '../components/Studio/StudioSidebar';
import { StudioThread } from '../components/Studio/StudioThread';
import { StudioComposer } from '../components/Studio/StudioComposer';
import { StudioContextRail } from '../components/Studio/StudioContextRail';
import type { StudioChat, StudioState } from '../components/Studio/types';
import { cancelStudioRun, fetchStudioState, startStudioRun } from '../lib/studio-api';

function activeRun(state: StudioState) {
  return (state.runs || []).find((run) => run.status === 'running' || run.status === 'queued');
}

export function StudioPage() {
  const [state, setState] = useState<StudioState>({});
  const [activeProjectId, setActiveProjectId] = useState('openjarvis');
  const [activeChatId, setActiveChatId] = useState('');
  const [error, setError] = useState('');

  const chats = useMemo(
    () => (state.chats || []).filter((chat) => !activeProjectId || chat.project_id === activeProjectId || !chat.project_id),
    [state.chats, activeProjectId],
  );
  const chat: StudioChat | undefined = chats.find((item) => item.id === activeChatId) || chats[0];
  const running = activeRun(state);

  const refresh = async () => {
    try {
      const next = await fetchStudioState(activeProjectId, chat?.id || activeChatId);
      setState(next);
      if (!activeChatId && next.chats && next.chats[0]) setActiveChatId(next.chats[0].id);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load Studio state');
    }
  };

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, running ? 2500 : 8000);
    return () => window.clearInterval(timer);
  }, [activeProjectId, activeChatId, Boolean(running)]);

  const sendPrompt = async (prompt: string) => {
    if (!chat?.id) return;
    await startStudioRun({ projectId: activeProjectId, chatId: chat.id, prompt });
    await refresh();
  };

  const cancelRun = async () => {
    if (!running?.id) return;
    await cancelStudioRun(running.id);
    await refresh();
  };

  return (
    <div className="studio-shell">
      <StudioSidebar
        projects={state.projects || []}
        chats={chats}
        activeProjectId={activeProjectId}
        activeChatId={chat?.id || ''}
        onSelectProject={setActiveProjectId}
        onSelectChat={setActiveChatId}
      />
      <main className="studio-main-workspace">
        {error && <div className="studio-error">{error}</div>}
        <StudioThread messages={chat?.messages || []} />
        <StudioComposer
          running={Boolean(running)}
          qwenProfile={state.qwen_profile?.active}
          onSend={sendPrompt}
          onCancel={cancelRun}
        />
      </main>
      <StudioContextRail
        runs={state.runs || []}
        agents={state.agents || []}
        lanes={state.qwen_runtime?.lanes || []}
      />
    </div>
  );
}
```

- [ ] **Step 3: Register route**

Modify `frontend/src/App.tsx`:

```tsx
import { StudioPage } from './pages/StudioPage';
```

Add inside the existing `<Route element={<Layout />}>` block:

```tsx
<Route path="studio" element={<StudioPage />} />
```

- [ ] **Step 4: Add sidebar navigation**

Modify `frontend/src/components/Sidebar/Sidebar.tsx` imports to include `PanelsTopLeft`:

```tsx
  PanelsTopLeft,
```

Add to `navItems` after Chat:

```tsx
{ path: '/studio', icon: PanelsTopLeft, label: 'Studio' },
```

- [ ] **Step 5: Run route marker test**

Run:

```powershell
cd E:\Claude\OpenJarvis
.\.venv\Scripts\python.exe -m pytest tests\web\test_studio.py::test_tauri_frontend_has_studio_route_and_components -q
```

Expected: PASS.

- [ ] **Step 6: Run frontend build**

Run:

```powershell
cd E:\Claude\OpenJarvis\frontend
npm run build
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add frontend/src/App.tsx frontend/src/components/Sidebar/Sidebar.tsx frontend/src/pages/StudioPage.tsx frontend/src/components/Studio/StudioSidebar.tsx frontend/src/components/Studio/StudioThread.tsx frontend/src/components/Studio/StudioComposer.tsx frontend/src/components/Studio/StudioContextRail.tsx
git commit -m "feat(studio): add Tauri Studio route shell"
```

---

## Task 4: Add Studio Desktop Styling

**Files:**
- Modify: `frontend/src/index.css`
- Test: `tests/web/test_studio.py`

- [ ] **Step 1: Add style marker test**

Add to `tests/web/test_studio.py`:

```python
def test_tauri_frontend_has_studio_shell_styles():
    css = (ROOT / "frontend" / "src" / "index.css").read_text(encoding="utf-8")

    for marker in [
        ".studio-shell",
        ".studio-left-rail",
        ".studio-main-workspace",
        ".studio-context-rail",
        ".studio-composer",
        ".studio-card",
        ".studio-chip",
    ]:
        assert marker in css
```

- [ ] **Step 2: Run style test and verify it fails**

Run:

```powershell
cd E:\Claude\OpenJarvis
.\.venv\Scripts\python.exe -m pytest tests\web\test_studio.py::test_tauri_frontend_has_studio_shell_styles -q
```

Expected: FAIL because the classes are not defined yet.

- [ ] **Step 3: Add scoped Studio CSS**

Append this inside `frontend/src/index.css` under `@layer components`:

```css
  .studio-shell {
    height: 100%;
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(220px, 260px) minmax(0, 1fr) minmax(320px, 420px);
    background: var(--color-bg);
    color: var(--color-text);
    overflow: hidden;
  }

  .studio-panel {
    min-height: 0;
    min-width: 0;
    border-color: var(--color-border);
    background: color-mix(in srgb, var(--color-surface) 88%, transparent);
  }

  .studio-left-rail {
    border-right: 1px solid var(--color-border);
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    overflow-y: auto;
  }

  .studio-main-workspace {
    min-width: 0;
    min-height: 0;
    display: grid;
    grid-template-rows: auto minmax(0, 1fr) auto;
    overflow: hidden;
  }

  .studio-context-rail {
    border-left: 1px solid var(--color-border);
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    overflow-y: auto;
    scrollbar-gutter: stable;
  }

  .studio-thread {
    min-height: 0;
    overflow-y: auto;
    padding: 24px clamp(16px, 5vw, 80px);
  }

  .studio-message {
    max-width: 820px;
    margin: 0 auto 16px;
    display: grid;
    gap: 6px;
  }

  .studio-message.operator {
    justify-items: end;
  }

  .studio-bubble {
    max-width: min(720px, 100%);
    border: 1px solid var(--color-border);
    border-radius: 14px;
    padding: 11px 14px;
    background: var(--color-bg-secondary);
    color: var(--color-text);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .studio-message.operator .studio-bubble {
    background: var(--color-user-bubble);
    color: var(--color-user-bubble-text);
  }

  .studio-message time {
    font-size: 10px;
    color: var(--color-text-tertiary);
  }

  .studio-composer {
    margin: 12px auto 16px;
    width: min(780px, calc(100% - 32px));
    border: 1px solid var(--color-border);
    border-radius: 16px;
    background: var(--color-bg-secondary);
    box-shadow: var(--shadow-lg);
    padding: 10px;
  }

  .studio-composer textarea {
    width: 100%;
    min-height: 84px;
    resize: vertical;
    border: 0;
    outline: 0;
    background: transparent;
    color: var(--color-text);
  }

  .studio-composer-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--color-text-tertiary);
    font-size: 12px;
  }

  .studio-send-button,
  .studio-stop-button,
  .studio-primary-action {
    margin-left: auto;
    border: 0;
    border-radius: 999px;
    padding: 7px 12px;
    background: var(--color-accent);
    color: var(--color-on-accent);
    cursor: pointer;
  }

  .studio-stop-button {
    background: var(--color-error);
    color: white;
  }

  .studio-card {
    border: 1px solid var(--color-border);
    border-radius: 12px;
    padding: 10px;
    background: var(--color-bg-secondary);
  }

  .studio-card h2,
  .studio-rail-section {
    margin-bottom: 8px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0;
    color: var(--color-text-tertiary);
  }

  .studio-row {
    width: 100%;
    min-height: 32px;
    border: 0;
    border-radius: 8px;
    padding: 0 9px;
    background: transparent;
    color: var(--color-text-secondary);
    text-align: left;
    cursor: pointer;
  }

  .studio-row.active,
  .studio-row:hover {
    background: var(--color-accent-subtle);
    color: var(--color-text);
  }

  .studio-chip-row {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
    margin-top: 8px;
  }

  .studio-chip {
    border: 1px solid color-mix(in srgb, var(--color-accent) 35%, transparent);
    border-radius: 999px;
    padding: 3px 7px;
    color: var(--color-accent);
    background: var(--color-accent-subtle);
    font-size: 10px;
  }

  .studio-empty-state,
  .studio-error {
    display: grid;
    place-items: center;
    gap: 8px;
    min-height: 220px;
    color: var(--color-text-secondary);
    text-align: center;
  }

  .studio-error {
    min-height: auto;
    padding: 8px 12px;
    color: var(--color-error);
    border-bottom: 1px solid var(--color-border);
  }

  @media (max-width: 1100px) {
    .studio-shell {
      grid-template-columns: minmax(190px, 230px) minmax(0, 1fr);
    }

    .studio-context-rail {
      display: none;
    }
  }
```

- [ ] **Step 4: Run style test**

Run:

```powershell
cd E:\Claude\OpenJarvis
.\.venv\Scripts\python.exe -m pytest tests\web\test_studio.py::test_tauri_frontend_has_studio_shell_styles -q
```

Expected: PASS.

- [ ] **Step 5: Run frontend build**

Run:

```powershell
cd E:\Claude\OpenJarvis\frontend
npm run build
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/index.css tests/web/test_studio.py
git commit -m "feat(studio): style Tauri Studio shell"
```

---

## Task 5: Wire Run Actions, Profile Switching, Preview, and Worker Update

**Files:**
- Modify: `frontend/src/pages/StudioPage.tsx`
- Modify: `frontend/src/components/Studio/StudioComposer.tsx`
- Modify: `frontend/src/components/Studio/StudioContextRail.tsx`
- Test: `tests/web/test_studio.py`

- [ ] **Step 1: Add action marker test**

Add to `tests/web/test_studio.py`:

```python
def test_tauri_frontend_studio_exposes_core_actions():
    page = (ROOT / "frontend" / "src" / "pages" / "StudioPage.tsx").read_text(encoding="utf-8")
    composer = (ROOT / "frontend" / "src" / "components" / "Studio" / "StudioComposer.tsx").read_text(encoding="utf-8")
    rail = (ROOT / "frontend" / "src" / "components" / "Studio" / "StudioContextRail.tsx").read_text(encoding="utf-8")

    for marker in [
        "setStudioQwenProfile",
        "startStudioPreview",
        "updateStudioWorker",
        "cancelStudioRun",
        "onProfileChange",
        "onOpenPreview",
        "onUpdateWorker",
    ]:
        assert marker in page or marker in composer or marker in rail
```

- [ ] **Step 2: Run action test and verify it fails**

Run:

```powershell
cd E:\Claude\OpenJarvis
.\.venv\Scripts\python.exe -m pytest tests\web\test_studio.py::test_tauri_frontend_studio_exposes_core_actions -q
```

Expected: FAIL because the shell does not expose profile/preview/update actions yet.

- [ ] **Step 3: Extend composer props**

Modify `frontend/src/components/Studio/StudioComposer.tsx` props:

```tsx
  onProfileChange: (profile: 'fast' | 'quality' | 'remote') => void;
```

Add buttons inside `.studio-composer-bar` before the send/stop button:

```tsx
        <button type="button" className="studio-profile-button" onClick={() => onProfileChange('fast')}>Fast</button>
        <button type="button" className="studio-profile-button" onClick={() => onProfileChange('quality')}>Quality</button>
        <button type="button" className="studio-profile-button" onClick={() => onProfileChange('remote')}>Remote</button>
```

- [ ] **Step 4: Extend context rail props**

Modify `frontend/src/components/Studio/StudioContextRail.tsx` props:

```tsx
  onOpenPreview: () => void;
  onUpdateWorker: () => void;
```

Add buttons in a new `studio-card`:

```tsx
      <section className="studio-card">
        <h2>Desktop Actions</h2>
        <button type="button" className="studio-row" onClick={onOpenPreview}>Open Preview</button>
        <button type="button" className="studio-row" onClick={onUpdateWorker}>Update Worker</button>
      </section>
```

- [ ] **Step 5: Wire page handlers**

Modify imports in `frontend/src/pages/StudioPage.tsx`:

```tsx
import {
  cancelStudioRun,
  fetchStudioState,
  setStudioQwenProfile,
  startStudioPreview,
  startStudioRun,
  updateStudioWorker,
} from '../lib/studio-api';
```

Add handlers:

```tsx
  const changeProfile = async (profile: 'fast' | 'quality' | 'remote') => {
    await setStudioQwenProfile(profile);
    await refresh();
  };

  const openPreview = async () => {
    const result = await startStudioPreview(activeProjectId);
    const url = typeof result.url === 'string' ? result.url : '';
    if (url) window.open(url, '_blank', 'noopener,noreferrer');
    await refresh();
  };

  const updateWorker = async () => {
    await updateStudioWorker();
    await refresh();
  };
```

Pass props:

```tsx
        <StudioComposer
          running={Boolean(running)}
          qwenProfile={state.qwen_profile?.active}
          onSend={sendPrompt}
          onCancel={cancelRun}
          onProfileChange={changeProfile}
        />
```

```tsx
      <StudioContextRail
        runs={state.runs || []}
        agents={state.agents || []}
        lanes={state.qwen_runtime?.lanes || []}
        onOpenPreview={openPreview}
        onUpdateWorker={updateWorker}
      />
```

- [ ] **Step 6: Add profile button CSS**

Add to `frontend/src/index.css` near composer button styles:

```css
  .studio-profile-button {
    border: 1px solid var(--color-border);
    border-radius: 999px;
    padding: 5px 9px;
    background: var(--color-bg-tertiary);
    color: var(--color-text-secondary);
    cursor: pointer;
  }
```

- [ ] **Step 7: Run action test and build**

Run:

```powershell
cd E:\Claude\OpenJarvis
.\.venv\Scripts\python.exe -m pytest tests\web\test_studio.py::test_tauri_frontend_studio_exposes_core_actions -q
cd E:\Claude\OpenJarvis\frontend
npm run build
```

Expected: test PASS and frontend build PASS.

- [ ] **Step 8: Commit**

```powershell
git add frontend/src/pages/StudioPage.tsx frontend/src/components/Studio/StudioComposer.tsx frontend/src/components/Studio/StudioContextRail.tsx frontend/src/index.css tests/web/test_studio.py
git commit -m "feat(studio): wire Tauri Studio actions"
```

---

## Task 6: Final Verification and Handoff

**Files:**
- Modify: `E:\Claude\Obsidian\Claude\Brain\Projects\OpenJarvis\STATE.md`

- [ ] **Step 1: Run all affected Python marker tests**

Run:

```powershell
cd E:\Claude\OpenJarvis
.\.venv\Scripts\python.exe -m pytest tests\web\test_studio.py -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend build**

Run:

```powershell
cd E:\Claude\OpenJarvis\frontend
npm run build
```

Expected: PASS.

- [ ] **Step 3: Run diff check**

Run:

```powershell
git -C E:\Claude\OpenJarvis diff --check
```

Expected: no whitespace errors. Line-ending warnings are acceptable if they match existing repo behavior.

- [ ] **Step 4: Update OpenJarvis project state**

Append to `E:\Claude\Obsidian\Claude\Brain\Projects\OpenJarvis\STATE.md`:

```markdown
## Latest (2026-06-03, Codex)
2026-06-03 (Codex) — Implemented Phase 1 of the Tauri Studio shell. The React/Tauri app now has a `/studio` route backed by the existing Studio APIs, a Codex-style left rail, conversation center, composer, and right progress/context rail. Existing browser Studio remains intact. Verification passed: `tests/web/test_studio.py`, frontend `npm run build`, and `git diff --check`. Existing unrelated `uv.lock` drift remains untouched. Restart/rebuild the Tauri app before expecting the new route live.
```

- [ ] **Step 5: Commit final state if code changed after previous commits**

If only the vault state changed, do not commit it in Git unless vault files are intentionally tracked in this repo. If frontend/code changes remain staged from previous tasks, commit them with a focused message.

- [ ] **Step 6: Push**

Run:

```powershell
git -C E:\Claude\OpenJarvis push origin feat/qwen-autonomy
```

Expected: branch pushes successfully.

---

## Self-Review

- Spec coverage: this plan implements Phase 1 only, which the spec recommends as the first testable slice. Phase 2 Brain panels, Phase 3 native controls, and Phase 4 wrapper retirement remain later plans.
- Filler-instruction scan: clean.
- Type consistency: `StudioState`, `StudioRun`, `StudioChat`, and component prop names are introduced before use and reused consistently.
- Risk control: existing browser Studio and backend APIs remain untouched. The plan adds a Tauri route and uses current endpoints.

## Execution Options

Plan complete and saved to `docs/superpowers/plans/2026-06-03-jarvis-tauri-studio-shell-plan.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh worker per task, review between tasks, fast iteration.
2. **Inline Execution** - execute tasks in this session using executing-plans, with checkpoints after each task.

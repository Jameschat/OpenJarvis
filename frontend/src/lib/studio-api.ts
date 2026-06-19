import { getBase } from './api';
import type { StudioChat, StudioState } from '../components/Studio/types';

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

function queryString(params: Record<string, string | undefined>): string {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value) query.set(key, value);
  });
  const raw = query.toString();
  return raw ? `?${raw}` : '';
}

export async function fetchStudioState(projectId?: string, chatId?: string): Promise<StudioState> {
  return requestJson<StudioState>(`/studio/state${queryString({ project_id: projectId, chat_id: chatId })}`);
}

export async function createStudioChat(projectId: string, title = 'New chat'): Promise<{ chat: StudioChat }> {
  return requestJson<{ chat: StudioChat }>('/studio/chats', {
    method: 'POST',
    body: JSON.stringify({ project_id: projectId, title }),
  });
}

export async function archiveStudioChat(chatId: string): Promise<{ chat: StudioChat; action: string }> {
  return requestJson<{ chat: StudioChat; action: string }>(`/studio/chats/${encodeURIComponent(chatId)}/archive`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

export async function deleteStudioChat(chatId: string): Promise<{ chat: StudioChat; action: string }> {
  return requestJson<{ chat: StudioChat; action: string }>(`/studio/chats/${encodeURIComponent(chatId)}/delete`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

export async function searchStudio(query: string): Promise<{ results: Array<Record<string, unknown>> }> {
  return requestJson<{ results: Array<Record<string, unknown>> }>(`/studio/search${queryString({ q: query })}`);
}

export async function startStudioRun(input: {
  projectId: string;
  chatId?: string;
  prompt: string;
  approved?: boolean;
  branchFromMessageId?: string;
  selectedSkills?: string[];
}): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>('/studio/runs', {
    method: 'POST',
    body: JSON.stringify({
      project_id: input.projectId,
      chat_id: input.chatId || '',
      prompt: input.prompt,
      approved: Boolean(input.approved),
      branch_from_message_id: input.branchFromMessageId || '',
      selected_skills: input.selectedSkills || [],
    }),
  });
}

export async function cancelStudioRun(runId: string): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>(`/studio/runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

export async function setStudioQwenProfile(profile: 'local35' | 'fast' | 'quality' | 'remote' | 'coder'): Promise<Record<string, unknown>> {
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

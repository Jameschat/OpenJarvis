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

export async function fetchStudioState(_projectId?: string, _chatId?: string): Promise<StudioState> {
  return requestJson<StudioState>('/studio/state');
}

export async function startStudioRun(input: {
  projectId: string;
  chatId?: string;
  prompt: string;
  approved?: boolean;
  branchFromMessageId?: string;
}): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>('/studio/runs', {
    method: 'POST',
    body: JSON.stringify({
      project_id: input.projectId,
      chat_id: input.chatId || '',
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

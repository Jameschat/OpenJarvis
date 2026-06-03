import { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { StudioComposer } from '../components/Studio/StudioComposer';
import { StudioContextRail } from '../components/Studio/StudioContextRail';
import { StudioSidebar } from '../components/Studio/StudioSidebar';
import { StudioThread } from '../components/Studio/StudioThread';
import type { StudioChat, StudioState } from '../components/Studio/types';
import {
  cancelStudioRun,
  fetchStudioState,
  setStudioQwenProfile,
  startStudioPreview,
  startStudioRun,
  updateStudioWorker,
} from '../lib/studio-api';

function getActiveRun(state: StudioState) {
  return (state.runs || []).find((run) => ['running', 'queued', 'pending'].includes(String(run.status || '').toLowerCase()));
}

function getSelectedChat(chats: StudioChat[], selectedChatId: string): StudioChat | undefined {
  return chats.find((chat) => chat.id === selectedChatId) || chats[0];
}

export function StudioPage() {
  const [state, setState] = useState<StudioState>({});
  const [selectedProjectId, setSelectedProjectId] = useState('');
  const [selectedChatId, setSelectedChatId] = useState('');
  const [composerValue, setComposerValue] = useState('');
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const nextState = await fetchStudioState();
    setState(nextState);
    setSelectedProjectId((current) => current || nextState.projects?.[0]?.id || '');
    setSelectedChatId((current) => current || nextState.chats?.[0]?.id || '');
    setLoading(false);
  }, []);

  useEffect(() => {
    refresh().catch((error) => {
      setLoading(false);
      toast.error('Studio state failed to load', { description: error.message });
    });
  }, [refresh]);

  useEffect(() => {
    const interval = setInterval(() => {
      refresh().catch(() => {});
    }, getActiveRun(state) ? 1500 : 8000);
    return () => clearInterval(interval);
  }, [refresh, state]);

  const selectedChat = useMemo(() => getSelectedChat(state.chats || [], selectedChatId), [state.chats, selectedChatId]);
  const activeRun = getActiveRun(state);
  const loadedProjectId = state.projects?.[0]?.id || '';

  const handleSelectProject = (projectId: string) => {
    if (!loadedProjectId || projectId === loadedProjectId) {
      setSelectedProjectId(projectId);
      setSelectedChatId(state.chats?.[0]?.id || '');
      return;
    }
    toast.info('Project switching needs scoped Studio state first', {
      description: 'Staying on the loaded project so tasks cannot mix project and chat context.',
    });
  };

  const handleSend = async () => {
    const prompt = composerValue.trim();
    if (!prompt || activeRun) return;
    const projectId = selectedProjectId || state.projects?.[0]?.id || 'openjarvis';
    const chatId = selectedChat?.id || selectedChatId || 'default';
    setComposerValue('');
    try {
      await startStudioRun({ projectId, chatId, prompt, approved: true });
      await refresh();
    } catch (error: any) {
      toast.error('Studio run failed', { description: error?.message || String(error) });
    }
  };

  const handleCancel = async () => {
    if (!activeRun?.id) return;
    try {
      await cancelStudioRun(activeRun.id);
      await refresh();
    } catch (error: any) {
      toast.error('Could not stop task', { description: error?.message || String(error) });
    }
  };

  const changeProfile = async (profile: 'fast' | 'quality' | 'remote') => {
    try {
      await setStudioQwenProfile(profile);
      await refresh();
      toast.success(`Qwen profile set to ${profile}`);
    } catch (error: any) {
      toast.error('Could not change Qwen profile', { description: error?.message || String(error) });
    }
  };

  const openPreview = async () => {
    const projectId = loadedProjectId || selectedProjectId || state.projects?.[0]?.id || 'openjarvis';
    try {
      const result = await startStudioPreview(projectId);
      const url = typeof result.url === 'string' ? result.url : '';
      if (url) window.open(url, '_blank', 'noopener,noreferrer');
      await refresh();
    } catch (error: any) {
      toast.error('Could not open preview', { description: error?.message || String(error) });
    }
  };

  const updateWorker = async () => {
    try {
      await updateStudioWorker();
      await refresh();
      toast.success('Worker update started');
    } catch (error: any) {
      toast.error('Could not update worker', { description: error?.message || String(error) });
    }
  };

  return (
    <div className="studio-shell">
      <StudioSidebar
        projects={state.projects || []}
        chats={state.chats || []}
        selectedProjectId={selectedProjectId}
        selectedChatId={selectedChatId}
        onSelectProject={handleSelectProject}
        onSelectChat={setSelectedChatId}
      />
      <main className="studio-main">
        <header className="studio-header">
          <div>
            <h1>Jarvis Studio</h1>
            <p>{loading ? 'Loading Studio...' : 'Local Qwen workspace with Codex-style project sessions.'}</p>
          </div>
          <span>{state.qwen_profile?.active || state.qwen_runtime?.active || 'qwen3.6-27b-local'}</span>
        </header>
        <StudioThread messages={selectedChat?.messages || []} activeRun={activeRun} />
        <StudioComposer
          value={composerValue}
          activeRunId={activeRun?.id}
          qwenProfile={state.qwen_profile?.active || 'fast'}
          onChange={setComposerValue}
          onSend={handleSend}
          onCancel={handleCancel}
          onProfileChange={changeProfile}
        />
      </main>
      <StudioContextRail
        state={state}
        activeRun={activeRun}
        onOpenPreview={openPreview}
        onUpdateWorker={updateWorker}
      />
    </div>
  );
}

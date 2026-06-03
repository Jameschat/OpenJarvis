import { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { StudioComposer } from '../components/Studio/StudioComposer';
import { StudioContextRail } from '../components/Studio/StudioContextRail';
import { StudioSidebar } from '../components/Studio/StudioSidebar';
import { StudioThread } from '../components/Studio/StudioThread';
import type { StudioChat, StudioState } from '../components/Studio/types';
import {
  archiveStudioChat,
  cancelStudioRun,
  createStudioChat,
  deleteStudioChat,
  fetchStudioState,
  searchStudio,
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
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<Array<Record<string, unknown>>>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async (projectId?: string, chatId?: string) => {
    const nextState = await fetchStudioState(projectId || selectedProjectId, chatId || selectedChatId);
    setState(nextState);
    setSelectedProjectId(nextState.project_id || projectId || nextState.projects?.[0]?.id || '');
    setSelectedChatId(nextState.chat_id || chatId || nextState.chats?.[0]?.id || '');
    setLoading(false);
  }, [selectedChatId, selectedProjectId]);

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
  const handleSelectProject = async (projectId: string) => {
    setSelectedProjectId(projectId);
    setSelectedChatId('');
    try {
      await refresh(projectId, '');
    } catch (error: any) {
      toast.error('Could not load project', { description: error?.message || String(error) });
    }
  };

  const handleSelectChat = async (chatId: string) => {
    setSelectedChatId(chatId);
    try {
      await refresh(selectedProjectId, chatId);
    } catch (error: any) {
      toast.error('Could not load chat', { description: error?.message || String(error) });
    }
  };

  const handleCreateChat = async () => {
    const projectId = selectedProjectId || state.project_id || state.projects?.[0]?.id || 'openjarvis';
    try {
      const result = await createStudioChat(projectId);
      await refresh(projectId, result.chat.id);
    } catch (error: any) {
      toast.error('Could not create chat', { description: error?.message || String(error) });
    }
  };

  const handleArchiveChat = async (chatId: string) => {
    try {
      await archiveStudioChat(chatId);
      await refresh(selectedProjectId, selectedChatId === chatId ? '' : selectedChatId);
    } catch (error: any) {
      toast.error('Could not archive chat', { description: error?.message || String(error) });
    }
  };

  const handleDeleteChat = async (chatId: string) => {
    try {
      await deleteStudioChat(chatId);
      await refresh(selectedProjectId, selectedChatId === chatId ? '' : selectedChatId);
    } catch (error: any) {
      toast.error('Could not delete chat', { description: error?.message || String(error) });
    }
  };

  const handleSearchQueryChange = async (query: string) => {
    setSearchQuery(query);
    if (!query.trim()) {
      setSearchResults([]);
      return;
    }
    try {
      const result = await searchStudio(query.trim());
      setSearchResults(result.results || []);
    } catch {
      setSearchResults([]);
    }
  };

  const handleSend = async () => {
    const prompt = composerValue.trim();
    if (!prompt || activeRun) return;
    const projectId = selectedProjectId || state.project_id || state.projects?.[0]?.id || 'openjarvis';
    const chatId = selectedChat?.id || selectedChatId || undefined;
    setComposerValue('');
    try {
      await startStudioRun({ projectId, chatId, prompt, approved: true });
      await refresh(projectId, chatId);
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
    const projectId = state.project_id || selectedProjectId || state.projects?.[0]?.id || 'openjarvis';
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
        plugins={state.plugins || []}
        automations={state.automations || []}
        selectedProjectId={selectedProjectId}
        selectedChatId={selectedChatId}
        searchQuery={searchQuery}
        searchResults={searchResults}
        onSearchQueryChange={handleSearchQueryChange}
        onCreateChat={handleCreateChat}
        onArchiveChat={handleArchiveChat}
        onDeleteChat={handleDeleteChat}
        onSelectProject={handleSelectProject}
        onSelectChat={handleSelectChat}
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

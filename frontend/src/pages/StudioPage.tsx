import { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { StudioComposer } from '../components/Studio/StudioComposer';
import { StudioContextRail } from '../components/Studio/StudioContextRail';
import { StudioSidebar } from '../components/Studio/StudioSidebar';
import { StudioThread } from '../components/Studio/StudioThread';
import type { StudioChat, StudioMessage, StudioState } from '../components/Studio/types';
import { getBase, isTauri } from '../lib/api';
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
  const [contextOpen, setContextOpen] = useState(false);
  const [contextDraft, setContextDraft] = useState('');
  const [contextItems, setContextItems] = useState<string[]>([]);
  const [steeringMessageId, setSteeringMessageId] = useState('');
  const [steeringSummary, setSteeringSummary] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [lastLoadedAt, setLastLoadedAt] = useState('');

  const refresh = useCallback(async (projectId?: string, chatId?: string) => {
    try {
      const nextState = await fetchStudioState(projectId || selectedProjectId, chatId || selectedChatId);
      setState(nextState);
      setSelectedProjectId(nextState.project_id || projectId || nextState.projects?.[0]?.id || '');
      setSelectedChatId(nextState.chat_id || chatId || nextState.chats?.[0]?.id || '');
      setLoadError('');
      setLastLoadedAt(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
      setLoading(false);
    } catch (error: any) {
      setLoadError(error?.message || String(error));
      setLoading(false);
      throw error;
    }
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
  const activeRuntimeLane = state.qwen_runtime?.lanes?.find((lane) => lane.active) || state.qwen_runtime?.lanes?.[0];
  const remoteProfileOnline = Boolean(
    state.qwen_runtime?.lanes?.some((lane) => {
      const id = String(lane.id || '');
      const role = String(lane.role || '');
      return (id.includes('remote') || role === 'remote-worker') && lane.online !== false;
    })
  );
  const backendOnline = !loadError && state.runtime_health?.ok !== false;
  const settingsItems = useMemo(() => [
    { label: 'Project', value: state.project_id || selectedProjectId || 'openjarvis' },
    { label: 'Chat', value: selectedChat?.title || selectedChatId || 'New chat' },
    { label: 'Qwen profile', value: String(state.qwen_profile?.active || 'fast') },
    { label: 'Runtime', value: String(activeRuntimeLane?.label || activeRuntimeLane?.alias || state.qwen_runtime?.active || 'qwen local') },
    { label: 'Provider', value: String(state.provider || 'auto') },
  ], [activeRuntimeLane, selectedChat?.title, selectedChatId, selectedProjectId, state.project_id, state.provider, state.qwen_profile?.active, state.qwen_runtime?.active]);
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

  const handleAddContext = () => {
    const nextContext = contextDraft.trim();
    if (!nextContext) return;
    setContextItems((items) => [...items, nextContext]);
    setContextDraft('');
  };

  const handleRemoveContext = (index: number) => {
    setContextItems((items) => items.filter((_, itemIndex) => itemIndex !== index));
  };

  const handleSteerMessage = (message: StudioMessage) => {
    if (!message?.id) return;
    setSteeringMessageId(String(message.id));
    setSteeringSummary(String(message.content || '').replace(/\s+/g, ' ').slice(0, 140));
    setComposerValue('');
  };

  const handleCancelSteer = () => {
    setSteeringMessageId('');
    setSteeringSummary('');
  };

  const handleSend = async () => {
    const prompt = composerValue.trim();
    if (!prompt || activeRun) return;
    const projectId = selectedProjectId || state.project_id || state.projects?.[0]?.id || 'openjarvis';
    const chatId = selectedChat?.id || selectedChatId || undefined;
    const promptWithContext = contextItems.length
      ? `[Studio attached context]\n${contextItems.map((item, index) => `${index + 1}. ${item}`).join('\n\n')}\n\n[Operator request]\n${prompt}`
      : prompt;
    setComposerValue('');
    try {
      const result = await startStudioRun({
        projectId,
        chatId,
        prompt: promptWithContext,
        approved: true,
        branchFromMessageId: steeringMessageId || undefined,
      });
      const resultRun = result.run as { chat_id?: string } | undefined;
      const nextChatId = resultRun?.chat_id || chatId;
      setContextItems([]);
      setContextOpen(false);
      handleCancelSteer();
      await refresh(projectId, nextChatId);
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
    if (profile === 'remote' && !remoteProfileOnline) {
      toast.error('Remote unavailable', { description: 'The remote Qwen worker is offline, so Studio will stay on the local profile.' });
      return;
    }
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

  const handleRetryConnection = async () => {
    setLoading(true);
    try {
      await refresh();
      toast.success('Jarvis backend reconnected');
    } catch (error: any) {
      setLoading(false);
      toast.error('Jarvis backend still unavailable', { description: error?.message || String(error) });
    }
  };

  return (
    <div className="studio-shell">
      <StudioSidebar
        projects={state.projects || []}
        chats={state.chats || []}
        plugins={state.plugins || []}
        automations={state.automations || []}
        settingsItems={settingsItems}
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
          <div className="studio-header-actions">
            <span className="studio-native-badge">{isTauri() ? 'Native desktop' : 'Browser preview'}</span>
            <span className={`studio-backend-badge ${backendOnline ? 'online' : 'offline'}`}>
              {backendOnline ? 'Backend online' : 'Backend offline'}
            </span>
            <span>{state.qwen_profile?.active || state.qwen_runtime?.active_alias || state.qwen_runtime?.active || 'qwen3.6-27b-local'}</span>
            <button type="button" className="studio-mini-action studio-refresh-action" onClick={() => refresh().catch(() => {})}>
              Refresh
            </button>
          </div>
        </header>
        {loadError && (
          <section className="studio-backend-banner">
            <div>
              <strong>Jarvis backend is not responding</strong>
              <span>{getBase()} returned: {loadError}</span>
            </div>
            <button type="button" onClick={handleRetryConnection}>
              {loading ? 'Retrying...' : 'Retry connection'}
            </button>
          </section>
        )}
        <StudioThread
          messages={selectedChat?.messages || []}
          activeRun={activeRun}
          steeringMessageId={steeringMessageId}
          onSteerMessage={handleSteerMessage}
        />
        <StudioComposer
          value={composerValue}
          activeRunId={activeRun?.id}
          qwenProfile={state.qwen_profile?.active || 'fast'}
          remoteProfileOnline={remoteProfileOnline}
          contextOpen={contextOpen}
          contextDraft={contextDraft}
          contextItems={contextItems}
          steeringSummary={steeringSummary}
          onChange={setComposerValue}
          onSend={handleSend}
          onCancel={handleCancel}
          onProfileChange={changeProfile}
          onToggleContext={() => setContextOpen((open) => !open)}
          onContextDraftChange={setContextDraft}
          onAddContext={handleAddContext}
          onRemoveContext={handleRemoveContext}
          onCancelSteer={handleCancelSteer}
        />
      </main>
      <StudioContextRail
        state={state}
        activeRun={activeRun}
        lastLoadedAt={lastLoadedAt}
        backendOnline={backendOnline}
        onOpenPreview={openPreview}
        onUpdateWorker={updateWorker}
      />
    </div>
  );
}

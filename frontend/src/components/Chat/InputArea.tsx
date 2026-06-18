import { useState, useRef, useCallback, useEffect } from 'react';
import { Send, Square, Paperclip } from 'lucide-react';
import { useAppStore, generateId } from '../../lib/store';
import { streamChat } from '../../lib/sse';
import { fetchSavings, getBase, sanitizeModelId } from '../../lib/api';
import { MicButton } from './MicButton';
import { useSpeech } from '../../hooks/useSpeech';
import type { ChatMessage, ToolCallInfo, TokenUsage, MessageTelemetry } from '../../types';
import { initAccumulator, reduceStreamEvent } from '../../lib/streamReducer';
import { parseSlashCommand, SLASH_HELP, type SlashCommand } from '../../lib/slashCommands';
import { ComposerControls } from './ComposerControls';
import { buildComposerSystemMessage } from '../../lib/store';

export function InputArea() {
  const defaultLocalModel = 'qwen3.6-27b-local';
  const [input, setInput] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const activeId = useAppStore((s) => s.activeId);
  const selectedModel = useAppStore((s) => s.selectedModel);
  // Legacy invariant: const activeModel = selectedModel || defaultLocalModel;
  const activeModel = sanitizeModelId(selectedModel);
  const streamState = useAppStore((s) => s.streamState);
  const messages = useAppStore((s) => s.messages);
  const speechEnabled = useAppStore((s) => s.settings.speechEnabled);
  const maxTokens = useAppStore((s) => s.settings.maxTokens);
  const temperature = useAppStore((s) => s.settings.temperature);
  const createConversation = useAppStore((s) => s.createConversation);
  const setSelectedModel = useAppStore((s) => s.setSelectedModel);
  const addMessage = useAppStore((s) => s.addMessage);
  const updateLastAssistant = useAppStore((s) => s.updateLastAssistant);
  const setStreamState = useAppStore((s) => s.setStreamState);
  const resetStream = useAppStore((s) => s.resetStream);
  const modelLoading = useAppStore((s) => s.modelLoading);
  const selectedSkills = useAppStore((s) => s.selectedSkills);
  const permissionMode = useAppStore((s) => s.permissionMode);
  const contextItems = useAppStore((s) => s.contextItems);
  const clearContextItems = useAppStore((s) => s.clearContextItems);

  const { state: speechState, available: speechAvailable, startRecording, stopRecording } = useSpeech();

  // Abort in-flight stream when the user switches models mid-generation.
  // This prevents errors from trying to continue a stream with a stale model.
  const prevModelRef = useRef(selectedModel);
  useEffect(() => {
    if (prevModelRef.current !== selectedModel && streamState.isStreaming) {
      abortRef.current?.abort();
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      resetStream();
      abortRef.current = null;
    }
    prevModelRef.current = selectedModel;
  }, [selectedModel, streamState.isStreaming, resetStream]);

  const micDisabled = !speechEnabled || !speechAvailable || streamState.isStreaming;
  const micReason: 'not-enabled' | 'no-backend' | 'streaming' | undefined =
    !speechEnabled ? 'not-enabled'
    : !speechAvailable ? 'no-backend'
    : streamState.isStreaming ? 'streaming'
    : undefined;

  const handleMicClick = useCallback(async () => {
    if (speechState === 'recording') {
      try {
        const text = await stopRecording();
        if (text) {
          setInput((prev) => (prev ? prev + ' ' + text : text));
        }
      } catch {
        // Error is captured in useSpeech
      }
    } else {
      await startRecording();
    }
  }, [speechState, startRecording, stopRecording]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
  }, [input]);

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    resetStream();
  }, [resetStream]);

  const handleSlash = useCallback((slash: SlashCommand) => {
    if (slash.cmd === 'clear') {
      createConversation(activeModel);
      return;
    }
    if (slash.cmd === 'model') {
      if (slash.arg) setSelectedModel(sanitizeModelId(slash.arg));
      return;
    }
    // help — render the command list as a local assistant message
    let convId = activeId;
    if (!convId) convId = createConversation(activeModel);
    const helpText = ['**Slash commands**', ...SLASH_HELP.map((c) => `- \`${c.name}\` — ${c.desc}`)].join('\n');
    addMessage(convId, {
      id: generateId(),
      role: 'assistant',
      content: helpText,
      timestamp: Date.now(),
    });
  }, [activeId, activeModel, createConversation, setSelectedModel, addMessage]);

  const sendMessage = useCallback(async () => {
    const content = input.trim();
    if (!content || streamState.isStreaming) return;

    const slash = parseSlashCommand(content);
    if (slash) {
      setInput('');
      handleSlash(slash);
      return;
    }

    setInput('');

    let convId = activeId;
    if (!convId) {
      convId = createConversation(activeModel);
    }

    const userMsg: ChatMessage = {
      id: generateId(),
      role: 'user',
      content,
      timestamp: Date.now(),
    };
    addMessage(convId, userMsg);

    // Build API messages before adding assistant placeholder. Composer controls
    // (skills + permission mode + pinned context) are carried as a leading
    // system message — the honest equivalent of Studio's run fields on the
    // plain /v1/chat/completions path. Context is one-shot: cleared after send.
    const currentMessages = useAppStore.getState().messages;
    const apiMessages: Array<{ role: string; content: string }> = currentMessages.map((m) => ({
      role: m.role as string,
      content: m.content,
    }));
    const systemPreamble = buildComposerSystemMessage(selectedSkills, permissionMode, contextItems);
    if (systemPreamble) {
      apiMessages.unshift({ role: 'system', content: systemPreamble });
    }
    if (contextItems.length) clearContextItems();

    const assistantMsg: ChatMessage = {
      id: generateId(),
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
    };
    addMessage(convId, assistantMsg);

    // Start streaming
    const startTime = Date.now();
    const timer = setInterval(() => {
      setStreamState({ elapsedMs: Date.now() - startTime });
    }, 100);
    timerRef.current = timer;

    const controller = new AbortController();
    abortRef.current = controller;

    let acc = initAccumulator();
    let usage: TokenUsage | undefined;
    let complexity: { score: number; tier: string; suggested_max_tokens: number } | undefined;
    let serverTelemetry: Partial<MessageTelemetry> | undefined;
    let lastFlush = 0;
    let ttftMs: number | undefined;

    setStreamState({
      isStreaming: true,
      phase: 'Generating...',
      elapsedMs: 0,
      activeToolCalls: [],
      content: '',
      tokens: 0,
      thinking: '',
      activity: [],
      plan: [],
    });
    useAppStore.getState().addLogEntry({
      timestamp: Date.now(),
      level: 'info',
      category: 'chat',
      message: `Request: "${content.slice(0, 80)}${content.length > 80 ? '...' : ''}" → ${activeModel}`,
    });

    // Named events flush immediately (tool/plan/edit/etc. feel responsive);
    // content chunks throttle to ~12/s so long replies don't freeze the thread.
    const NAMED_FLUSH = new Set([
      'tool_call_start', 'tool_call_end', 'plan', 'file_edit',
      'escalation', 'routing', 'verification', 'inference_start', 'agent_turn_start',
    ]);

    try {
      for await (const sseEvent of streamChat(
        { model: activeModel, messages: apiMessages, stream: true, temperature, max_tokens: maxTokens },
        controller.signal,
      )) {
        const before = acc.toolCalls.length;
        acc = reduceStreamEvent(acc, sseEvent, { startTime, now: Date.now() });
        usage = acc.usage ?? usage;
        complexity = acc.complexity ?? complexity;
        serverTelemetry = acc.telemetry ?? serverTelemetry;
        ttftMs = acc.ttftMs ?? ttftMs;

        if (sseEvent.event === 'tool_call_start' && acc.toolCalls.length > before) {
          const tc = acc.toolCalls[acc.toolCalls.length - 1];
          useAppStore.getState().addLogEntry({
            timestamp: Date.now(), level: 'info', category: 'tool',
            message: `Calling ${tc.tool}(${tc.arguments || ''})`,
          });
        }
        if (sseEvent.event === 'inference_start') {
          useAppStore.getState().addLogEntry({
            timestamp: Date.now(), level: 'info', category: 'chat',
            message: `Generating with ${activeModel}...`,
          });
        }

        const now = Date.now();
        const isNamed = !!sseEvent.event && NAMED_FLUSH.has(sseEvent.event);
        if (isNamed || now - lastFlush >= 80) {
          setStreamState({
            content: acc.content,
            phase: acc.phase,
            tokens: acc.tokens,
            elapsedMs: now - startTime,
            activeToolCalls: [...acc.toolCalls],
            thinking: acc.thinking,
            activity: [...acc.activity],
            plan: [...acc.plan],
            routing: acc.routing,
          });
          updateLastAssistant(
            convId,
            acc.content,
            acc.toolCalls.length > 0 ? [...acc.toolCalls] : undefined,
            undefined,
            undefined,
            undefined,
            acc.activity.length > 0 ? [...acc.activity] : undefined,
            acc.plan.length > 0 ? [...acc.plan] : undefined,
          );
          lastFlush = now;
        }

        if (acc.done) break;
      }
    } catch (err: any) {
      if (err.name === 'AbortError') {
        // User cancelled or model switch — keep whatever was accumulated
        if (!acc.content) acc.content = '(Generation stopped)';
      } else {
        const errMsg = err?.message || String(err);
        acc.content =
          acc.content || `Error: ${errMsg}`;
        useAppStore.getState().addLogEntry({
          timestamp: Date.now(), level: 'error', category: 'chat',
          message: `Stream error: ${errMsg}`,
        });
      }
    } finally {
      if (!acc.content) {
        acc.content = 'No response was generated. Please try again.';
      }
      const totalMs = Date.now() - startTime;
      const _CLOUD_PREFIXES = ['gpt-', 'o1-', 'o3-', 'o4-', 'claude-', 'gemini-', 'openrouter/', 'MiniMax-', 'chatgpt-'];
      const engineLabel = _CLOUD_PREFIXES.some(p => activeModel.startsWith(p)) ? 'cloud' : 'ollama';
      const telemetry: MessageTelemetry = {
        engine: serverTelemetry?.engine ?? engineLabel,
        model_id: activeModel,
        total_ms: totalMs,
        ttft_ms: ttftMs,
        // Prefer the real llama.cpp decode speed; fall back to the client-side
        // estimate (completion_tokens / wall-clock, which overcounts TTFT +
        // thinking tokens) only when the server did not report timings.
        tokens_per_sec: serverTelemetry?.decode_tok_s
          ?? (usage?.completion_tokens
            ? usage.completion_tokens / (totalMs / 1000)
            : undefined),
        decode_tok_s: serverTelemetry?.decode_tok_s,
        prefill_tok_s: serverTelemetry?.prefill_tok_s,
        accept_rate: serverTelemetry?.accept_rate,
        predicted_n: serverTelemetry?.predicted_n,
        complexity_score: complexity?.score,
        complexity_tier: complexity?.tier,
        suggested_max_tokens: complexity?.suggested_max_tokens,
      };
      // Check if the response has digest audio available
      let audioMeta: { url: string } | undefined;
      try {
        const digestRes = await fetch(`${getBase()}/api/digest`);
        if (digestRes.ok) {
          const digest = await digestRes.json();
          if (digest.audio_available) {
            audioMeta = { url: `${getBase()}/api/digest/audio` };
          }
        }
      } catch {
        // Not a digest response or server unavailable — skip
      }

      updateLastAssistant(
        convId,
        acc.content,
        acc.toolCalls.length > 0 ? acc.toolCalls : undefined,
        usage,
        telemetry,
        audioMeta,
        acc.activity.length > 0 ? acc.activity : undefined,
        acc.plan.length > 0 ? acc.plan : undefined,
      );
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      resetStream();
      useAppStore.getState().addLogEntry({
        timestamp: Date.now(), level: 'info', category: 'chat',
        message: `Response: ${acc.content.length} chars`,
      });
      abortRef.current = null;

      fetchSavings()
        .then((data) => useAppStore.getState().setSavings(data))
        .catch(() => {});
    }
  }, [
    input,
    activeId,
    activeModel,
    streamState.isStreaming,
    createConversation,
    addMessage,
    updateLastAssistant,
    setStreamState,
    resetStream,
    handleSlash,
    selectedSkills,
    permissionMode,
    contextItems,
    clearContextItems,
  ]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div className="px-4 pb-4 pt-2" style={{ maxWidth: 'var(--chat-max-width)', margin: '0 auto', width: '100%' }}>
      <div
        className="flex items-center gap-2 rounded-2xl px-4 py-3 transition-shadow"
        style={{
          background: 'var(--color-input-bg)',
          border: '1px solid var(--color-input-border)',
          boxShadow: 'var(--shadow-sm)',
        }}
      >
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Message OpenJarvis..."
          rows={1}
          className="flex-1 bg-transparent outline-none resize-none text-sm leading-relaxed"
          style={{ color: 'var(--color-text)', maxHeight: '200px' }}
          disabled={streamState.isStreaming || modelLoading}
        />
        {streamState.isStreaming ? (
          <button
            onClick={stopStreaming}
            className="p-2 rounded-xl transition-colors shrink-0 cursor-pointer"
            style={{ background: 'var(--color-error)', color: 'var(--color-on-accent)' }}
            title="Stop generating"
          >
            <Square size={16} />
          </button>
        ) : (
          <div className="flex items-center gap-1">
            <MicButton
              state={speechState}
              onClick={handleMicClick}
              disabled={micDisabled}
              reason={micReason}
            />
            <button
              onClick={sendMessage}
              disabled={!input.trim() || modelLoading}
              className="p-2 rounded-xl transition-colors shrink-0 cursor-pointer disabled:opacity-30 disabled:cursor-default"
              style={{
                background: input.trim() ? 'var(--color-accent)' : 'var(--color-bg-tertiary)',
                color: input.trim() ? 'white' : 'var(--color-text-tertiary)',
              }}
              title="Send message"
            >
              <Send size={16} />
            </button>
          </div>
        )}
      </div>
      <ComposerControls />
      <div className="flex items-center justify-center mt-2 text-[11px]" style={{ color: 'var(--color-text-tertiary)' }}>
        <span>
          <kbd className="font-mono">Enter</kbd> to send &middot;{' '}
          <kbd className="font-mono">Shift+Enter</kbd> for new line
        </span>
      </div>
    </div>
  );
}

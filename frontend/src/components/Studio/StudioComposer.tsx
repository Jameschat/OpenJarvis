import { Paperclip, Send, Square } from 'lucide-react';
import type { ChangeEvent, KeyboardEvent } from 'react';

interface StudioComposerProps {
  value: string;
  activeRunId?: string;
  qwenProfile: string;
  remoteProfileOnline?: boolean;
  contextOpen: boolean;
  contextDraft: string;
  contextItems: string[];
  steeringSummary?: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onCancel: () => void;
  onProfileChange: (profile: 'fast' | 'quality' | 'remote') => void;
  onToggleContext: () => void;
  onContextDraftChange: (value: string) => void;
  onAddContext: () => void;
  onRemoveContext: (index: number) => void;
  onCancelSteer: () => void;
}

export function StudioComposer({
  value,
  activeRunId,
  qwenProfile,
  remoteProfileOnline = true,
  contextOpen,
  contextDraft,
  contextItems,
  steeringSummary,
  onChange,
  onSend,
  onCancel,
  onProfileChange,
  onToggleContext,
  onContextDraftChange,
  onAddContext,
  onRemoveContext,
  onCancelSteer,
}: StudioComposerProps) {
  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      onSend();
    }
  };

  const handleProfileSelect = (event: ChangeEvent<HTMLSelectElement>) => {
    const next = event.target.value;
    if (next === 'fast' || next === 'quality' || next === 'remote') {
      onProfileChange(next);
    }
  };

  return (
    <footer className="studio-composer">
      {steeringSummary && (
        <section className="studio-steer-banner">
          <div>
            <strong>Steering from message</strong>
            <span>{steeringSummary}</span>
          </div>
          <button type="button" onClick={onCancelSteer}>Cancel steer</button>
        </section>
      )}
      {contextOpen && (
        <section className="studio-context-drawer">
          <div className="studio-context-drawer-header">
            <strong>Run context</strong>
            <span>{contextItems.length} attached</span>
          </div>
          {contextItems.length > 0 && (
            <div className="studio-context-items">
              {contextItems.map((item, index) => (
                <div className="studio-context-item" key={`${index}-${item.slice(0, 16)}`}>
                  <span>{item}</span>
                  <button type="button" onClick={() => onRemoveContext(index)}>Remove</button>
                </div>
              ))}
            </div>
          )}
          <textarea
            className="studio-context-input"
            value={contextDraft}
            onChange={(event) => onContextDraftChange(event.target.value)}
            placeholder="Paste project notes, requirements, URLs, or constraints to attach to the next message..."
          />
          <button className="studio-context-add" type="button" onClick={onAddContext} disabled={!contextDraft.trim()}>
            Add context
          </button>
        </section>
      )}
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Ask Jarvis to plan, build, test, search memory, or run a local Qwen workflow..."
      />
      <div className="studio-composer-actions">
        <button className={`studio-pill ${contextOpen ? 'active' : ''}`} type="button" onClick={onToggleContext}>
          <Paperclip size={14} />
          Context {contextItems.length ? `(${contextItems.length})` : ''}
        </button>
        <span className="studio-pill">Default permissions</span>
        <label className="studio-model-select" title="Select model profile">
          <span className={`studio-model-status ${remoteProfileOnline ? 'online' : 'degraded'}`} />
          <select
            aria-label="Select model profile"
            value={qwenProfile || 'fast'}
            onChange={handleProfileSelect}
          >
            <option value="fast">Qwen 27B Fast</option>
            <option value="quality">Qwen 27B Quality</option>
            <option value="remote" disabled={!remoteProfileOnline}>
              {remoteProfileOnline ? 'Remote 35B-A3B' : 'Remote 35B-A3B offline'}
            </option>
            <option value="escalation" disabled>Claude/Codex escalation</option>
          </select>
        </label>
        <button
          className="studio-send-button"
          onClick={activeRunId ? onCancel : onSend}
          type="button"
          aria-label={activeRunId ? 'Stop current Jarvis task' : 'Send message'}
        >
          {activeRunId ? <Square size={16} /> : <Send size={16} />}
        </button>
      </div>
    </footer>
  );
}

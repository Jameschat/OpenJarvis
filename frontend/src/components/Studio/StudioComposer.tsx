import { Paperclip, Send, Square } from 'lucide-react';
import type { KeyboardEvent } from 'react';

interface StudioComposerProps {
  value: string;
  activeRunId?: string;
  qwenProfile: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onCancel: () => void;
  onProfileChange: (profile: 'fast' | 'quality' | 'remote') => void;
}

export function StudioComposer({
  value,
  activeRunId,
  qwenProfile,
  onChange,
  onSend,
  onCancel,
  onProfileChange,
}: StudioComposerProps) {
  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      onSend();
    }
  };

  return (
    <footer className="studio-composer">
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Ask Jarvis to plan, build, test, search memory, or run a local Qwen workflow..."
      />
      <div className="studio-composer-actions">
        <button className="studio-pill" type="button" disabled title="Context attachments are wired in the next Studio phase">
          <Paperclip size={14} />
          Context
        </button>
        <span className="studio-pill">Default permissions</span>
        <span className="studio-pill">Qwen {qwenProfile || 'fast'}</span>
        <button
          type="button"
          className={`studio-profile-button ${qwenProfile === 'fast' ? 'active' : ''}`}
          onClick={() => onProfileChange('fast')}
        >
          Fast
        </button>
        <button
          type="button"
          className={`studio-profile-button ${qwenProfile === 'quality' ? 'active' : ''}`}
          onClick={() => onProfileChange('quality')}
        >
          Quality
        </button>
        <button
          type="button"
          className={`studio-profile-button ${qwenProfile === 'remote' ? 'active' : ''}`}
          onClick={() => onProfileChange('remote')}
        >
          Remote
        </button>
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

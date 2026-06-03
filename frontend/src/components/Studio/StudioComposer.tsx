import { Paperclip, Send, Square } from 'lucide-react';
import type { KeyboardEvent } from 'react';

interface StudioComposerProps {
  value: string;
  activeRunId?: string;
  qwenProfile: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onCancel: () => void;
}

export function StudioComposer({
  value,
  activeRunId,
  qwenProfile,
  onChange,
  onSend,
  onCancel,
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
        <button className="studio-pill" type="button">
          <Paperclip size={14} />
          Context
        </button>
        <span className="studio-pill">Default permissions</span>
        <span className="studio-pill">Qwen {qwenProfile || 'fast'}</span>
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

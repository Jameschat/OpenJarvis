import { useState } from 'react';
import { Brain, ChevronRight, ChevronDown } from 'lucide-react';

interface Props {
  thinking: string;
  // True while the answer hasn't started yet — keep it open so the user sees
  // live reasoning; once the visible answer begins, default to collapsed.
  active?: boolean;
}

export function ThinkingBlock({ thinking, active = false }: Props) {
  const [open, setOpen] = useState(active);
  if (!thinking) return null;
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div
      className="mb-3 rounded-lg text-xs"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border-subtle)' }}
    >
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 w-full px-3 py-1.5 cursor-pointer"
        style={{ color: 'var(--color-text-tertiary)', background: 'transparent', border: 'none' }}
      >
        <Brain size={12} />
        <span className="font-mono">{active ? 'Thinking…' : 'Thought process'}</span>
        <Chevron size={12} className="ml-auto" />
      </button>
      {open && (
        <div
          className="px-3 pb-2 pt-0 font-mono leading-relaxed whitespace-pre-wrap"
          style={{ color: 'var(--color-text-tertiary)', maxHeight: 280, overflowY: 'auto' }}
        >
          {thinking}
        </div>
      )}
    </div>
  );
}

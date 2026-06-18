import { useState } from 'react';
import { FileDiff, ChevronRight, ChevronDown } from 'lucide-react';

interface Props {
  path: string;
  diff: string;
  added: number;
  removed: number;
}

function lineStyle(line: string): React.CSSProperties {
  if (line.startsWith('+') && !line.startsWith('+++')) {
    return { background: 'rgba(34,197,94,0.14)', color: 'var(--color-text)' };
  }
  if (line.startsWith('-') && !line.startsWith('---')) {
    return { background: 'rgba(239,68,68,0.14)', color: 'var(--color-text)' };
  }
  if (line.startsWith('@@')) {
    return { color: 'var(--color-accent)' };
  }
  return { color: 'var(--color-text-tertiary)' };
}

export function DiffBlock({ path, diff, added, removed }: Props) {
  const lines = diff ? diff.split('\n') : [];
  const [open, setOpen] = useState(lines.length <= 40);
  const name = path.split(/[\\/]/).pop() || path;
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div
      className="mb-3 rounded-lg text-xs overflow-hidden"
      style={{ background: 'var(--color-code-bg, var(--color-bg-secondary))', border: '1px solid var(--color-border-subtle)' }}
    >
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 w-full px-3 py-1.5 cursor-pointer"
        style={{ color: 'var(--color-text-secondary)', background: 'transparent', border: 'none' }}
      >
        <FileDiff size={12} />
        <span className="font-mono" title={path}>{name}</span>
        <span className="font-mono" style={{ color: 'rgb(34,197,94)' }}>+{added}</span>
        <span className="font-mono" style={{ color: 'rgb(239,68,68)' }}>-{removed}</span>
        <Chevron size={12} className="ml-auto" />
      </button>
      {open && (
        <pre
          className="m-0 px-3 py-2 font-mono leading-relaxed overflow-x-auto"
          style={{ maxHeight: 360, overflowY: 'auto' }}
        >
          {lines.map((ln, i) => (
            <div key={i} style={{ ...lineStyle(ln), whiteSpace: 'pre' }}>{ln || ' '}</div>
          ))}
        </pre>
      )}
    </div>
  );
}

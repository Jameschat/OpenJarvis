import { Circle, CircleDot, CheckCircle2 } from 'lucide-react';
import type { PlanItem } from '../../types';

const ICON = {
  pending: Circle,
  in_progress: CircleDot,
  completed: CheckCircle2,
} as const;

const COLOR = {
  pending: 'var(--color-text-tertiary)',
  in_progress: 'var(--color-accent)',
  completed: 'var(--color-success, #22c55e)',
} as const;

export function PlanChecklist({ items }: { items: PlanItem[] }) {
  if (!items || items.length === 0) return null;
  return (
    <div
      className="mb-3 rounded-lg px-3 py-2 text-xs flex flex-col gap-1"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border-subtle)' }}
    >
      {items.map((it) => {
        const Icon = ICON[it.status];
        return (
          <div key={it.id} className="flex items-center gap-2">
            <Icon size={13} style={{ color: COLOR[it.status], flexShrink: 0 }} />
            <span
              style={{
                color: it.status === 'completed' ? 'var(--color-text-tertiary)' : 'var(--color-text-secondary)',
                textDecoration: it.status === 'completed' ? 'line-through' : 'none',
              }}
            >
              {it.title}
            </span>
          </div>
        );
      })}
    </div>
  );
}

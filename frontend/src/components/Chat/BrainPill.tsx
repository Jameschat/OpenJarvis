import { Cpu } from 'lucide-react';
import type { RoutingInfo } from '../../types';

const HEALTH_COLOR: Record<string, string> = {
  ok: 'rgb(34,197,94)',
  degraded: 'rgb(234,179,8)',
  down: 'rgb(239,68,68)',
};

export function BrainPill({ routing }: { routing?: RoutingInfo }) {
  if (!routing || !routing.brain) return null;
  const dot = HEALTH_COLOR[routing.health || ''] || 'var(--color-text-tertiary)';
  return (
    <span
      className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-mono"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border-subtle)', color: 'var(--color-text-secondary)' }}
      title={routing.model || routing.brain}
    >
      <Cpu size={11} />
      <span>{routing.brain}</span>
      {routing.lane && <span style={{ color: 'var(--color-text-tertiary)' }}>{routing.lane}</span>}
      <span style={{ width: 6, height: 6, borderRadius: 9999, background: dot }} />
    </span>
  );
}

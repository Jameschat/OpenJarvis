import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { FileText } from 'lucide-react';
import { fetchStudioState } from '../../lib/studio-api';
import type { StudioState } from '../Studio/types';
import { useAppStore } from '../../lib/store';

// The live instruments rail for Chat: System Health from the /studio/state poll;
// File Activity and Progress come from the LIVE chat store so they light up during
// a chat. (Lane status moved to the LED next to the model selector in the composer.)

function StatusCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="studio-context-card">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function pct(v: unknown): string {
  if (typeof v === 'number') return `${Math.round(v)}%`;
  if (typeof v === 'string' && v.trim()) return v;
  return 'pending';
}

function txt(v: unknown, fallback = ''): string {
  return v == null ? fallback : String(v);
}

export function JarvisRail() {
  const [state, setState] = useState<StudioState | null>(null);
  const messages = useAppStore((s) => s.messages);
  const streamState = useAppStore((s) => s.streamState);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      fetchStudioState()
        .then((s) => {
          if (alive) setState(s);
        })
        .catch(() => {
          /* best-effort; System Health shows 'pending' when offline */
        });
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const system = (state?.system || {}) as Record<string, unknown>;
  const gpu = (system.gpu || {}) as Record<string, unknown>;

  // Live activity/plan: streaming message while generating, else last assistant.
  const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant');
  const activity = streamState.isStreaming ? streamState.activity : lastAssistant?.activity || [];
  const plan = streamState.isStreaming ? streamState.plan : lastAssistant?.plan || [];
  const fileEdits = activity.filter(
    (a): a is Extract<typeof a, { kind: 'file_edit' }> => a.kind === 'file_edit',
  );

  return (
    <aside
      className="studio-context-rail"
      style={{ width: 300, flexShrink: 0, overflowY: 'auto', padding: '12px' }}
    >
      <StatusCard title="System Health">
        <div className="studio-metric-row">
          <span>GPU</span>
          <strong>{txt(gpu.name, 'GPU')} {pct(gpu.util_percent)}</strong>
        </div>
        <div className="studio-metric-row">
          <span>VRAM</span>
          <strong>{pct(gpu.memory_percent)}</strong>
        </div>
        <div className="studio-metric-row">
          <span>CPU</span>
          <strong>{pct(system.cpu_percent)}</strong>
        </div>
        <div className="studio-metric-row">
          <span>RAM</span>
          <strong>{pct(system.ram_percent)}</strong>
        </div>
      </StatusCard>

      <StatusCard title="Progress">
        {plan.length === 0 ? (
          <div className="studio-muted">No active task steps</div>
        ) : (
          plan.map((p) => (
            <div className="studio-metric-row" key={p.id}>
              <span>{p.title}</span>
              <strong>{p.status}</strong>
            </div>
          ))
        )}
      </StatusCard>

      <StatusCard title="File Activity">
        {fileEdits.length === 0 ? (
          <div className="studio-muted">No file edits</div>
        ) : (
          fileEdits.map((a, i) => (
            <div className="studio-progress-row" key={a.editId || i}>
              <FileText size={14} />
              <span>{a.path.split(/[\\/]/).pop() || a.path}</span>
              <strong style={{ color: 'rgb(34,197,94)' }}>+{a.added}</strong>
              <strong style={{ color: 'rgb(239,68,68)' }}>-{a.removed}</strong>
            </div>
          ))
        )}
      </StatusCard>
    </aside>
  );
}

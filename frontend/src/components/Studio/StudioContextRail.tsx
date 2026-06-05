import { Activity, Copy, Cpu, ExternalLink, FileText, GitBranch, Globe, PlugZap } from 'lucide-react';
import type { ReactNode } from 'react';
import type { StudioAgent, StudioRun, StudioState } from './types';

interface StudioContextRailProps {
  state: StudioState;
  activeRun?: StudioRun;
  lastLoadedAt?: string;
  backendOnline?: boolean;
  onOpenPreview: () => void;
  onUpdateWorker: () => void;
}

function StatusCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="studio-context-card">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function AgentRow({ agent }: { agent: StudioAgent }) {
  return (
    <div className={`studio-agent-row agent-${agent.provider || 'qwen'}`}>
      <span>{agent.name || agent.id || 'agent'}</span>
      <strong>{agent.status || 'idle'}</strong>
    </div>
  );
}

function copyText(value: string) {
  if (!value) return;
  navigator.clipboard?.writeText(value).catch(() => {});
}

function textValue(value: unknown, fallback = ''): string {
  if (value == null) return fallback;
  return String(value);
}

function percentValue(value: unknown): string {
  if (typeof value === 'number') return `${Math.round(value)}%`;
  if (typeof value === 'string' && value.trim()) return value;
  return 'pending';
}

function runtimeSpeed(lane: Record<string, unknown>): string {
  const benchmark = lane.benchmark;
  if (benchmark && typeof benchmark === 'object') {
    const latest = (benchmark as Record<string, unknown>).latest_tok_s;
    const tiny = (benchmark as Record<string, unknown>).tiny_tok_s;
    const studio = (benchmark as Record<string, unknown>).studio_json_tok_s;
    const value = latest || tiny || studio;
    if (typeof value === 'number') return `${value.toFixed(1)} tok/s`;
  }
  return '';
}

export function StudioContextRail({
  state,
  activeRun,
  lastLoadedAt,
  backendOnline = true,
  onOpenPreview,
  onUpdateWorker,
}: StudioContextRailProps) {
  const runtimeLane = state.qwen_runtime?.lanes?.find((lane) => lane.active) || state.qwen_runtime?.lanes?.[0];
  const qwenRuntime = state.qwen_runtime;
  const runtimeHealth = state.runtime_health;
  const runtimeServices = state.runtime_health?.services || [];
  const system = state.system || {};
  const gpu = (system.gpu || {}) as Record<string, unknown>;
  const remoteLane = qwenRuntime?.lanes?.find((lane) => String(lane.id || '').includes('remote') || lane.role === 'remote-worker');
  const tasks = activeRun?.task_details || activeRun?.tasks || [];
  const outputs = activeRun?.outputs || [];
  const fileActivity = activeRun?.file_activity || [];
  const previewAvailable = state.preview?.available === true;
  const eccCommand = activeRun?.ecc_command;

  return (
    <aside className="studio-context-rail">
      <section className="studio-card">
        <h2>Desktop Actions</h2>
        <div className={`studio-native-status ${backendOnline ? 'online' : 'offline'}`}>
          <span>{backendOnline ? 'Native app connected' : 'Backend unavailable'}</span>
          <strong>{lastLoadedAt || 'not loaded'}</strong>
        </div>
        <button type="button" className="studio-row" onClick={onOpenPreview}>Open Preview</button>
        <button type="button" className="studio-row" onClick={onUpdateWorker}>Update Worker</button>
      </section>

      <StatusCard title="Run Summary">
        {activeRun ? (
          <>
            <div className="studio-run-summary-grid">
              <span>Status</span>
              <strong>{activeRun.status || 'running'}</strong>
              <span>Workflow</span>
              <strong>{activeRun.workflow || 'direct'}</strong>
              <span>ECC profile</span>
              <strong className={eccCommand ? 'studio-ecc-command-pill' : ''}>{eccCommand || 'none'}</strong>
              <span>Steps</span>
              <strong>{tasks.length}</strong>
              <span>Outputs</span>
              <strong>{outputs.length}</strong>
            </div>
            {eccCommand ? (
              <p className="studio-run-summary-note studio-ecc-command-note">
                ECC guidance only. Commands stay blocked unless explicitly approved.
              </p>
            ) : null}
            {activeRun.progress_summary ? (
              <p className="studio-run-summary-note">{activeRun.progress_summary}</p>
            ) : null}
            <button
              type="button"
              className="studio-mini-action studio-run-copy"
              onClick={() => copyText(activeRun.id)}
              title="Copy run id"
            >
              <Copy size={12} />
              Copy run id
            </button>
          </>
        ) : (
          <div className="studio-muted">No active run</div>
        )}
      </StatusCard>

      <StatusCard title="Progress">
        {tasks.length === 0 ? (
          <div className="studio-muted">No active task steps</div>
        ) : (
          tasks.map((task, index) => (
            <div className="studio-progress-row" key={index}>
              <Activity size={14} />
              <span>{typeof task === 'string' ? task : String(task.title || task.step || task.status || 'Task')}</span>
            </div>
          ))
        )}
      </StatusCard>

      <StatusCard title="Qwen Runtime">
        <div className="studio-metric-row">
          <Cpu size={14} />
          <span>{runtimeLane?.label || runtimeLane?.alias || state.qwen_runtime?.active || 'qwen3.6-27b-local'}</span>
          <strong>{runtimeLane?.online === false ? 'offline' : 'online'}</strong>
        </div>
        {qwenRuntime?.promotion_verdict ? (
          <p className="studio-run-summary-note">{qwenRuntime.promotion_verdict}</p>
        ) : null}
      </StatusCard>

      <StatusCard title="Remote Worker">
        <div className="studio-metric-row">
          <span className={`studio-led ${remoteLane?.online === false ? 'offline' : 'online'}`} />
          <span>{remoteLane?.label || 'Remote 35B worker'}</span>
          <strong>{remoteLane?.online === false ? 'offline' : 'online'}</strong>
        </div>
        <div className="studio-metric-row">
          <span>{remoteLane?.host || '192.168.1.191'}:{remoteLane?.port || 4000}</span>
          <strong>{remoteLane?.context_tokens ? `${remoteLane.context_tokens / 1000}K ctx` : '128K ctx'}</strong>
        </div>
      </StatusCard>

      <StatusCard title="Runtime Readiness">
        <p className="studio-run-summary-note">{runtimeHealth?.summary || 'Runtime health pending.'}</p>
        {runtimeServices.slice(0, 6).map((service, index) => (
          <div className="studio-metric-row" key={String(service.id || service.label || index)}>
            <span className={`studio-led ${service.ok === false ? 'offline' : 'online'}`} />
            <span>{textValue(service.label || service.id, 'Runtime service')}</span>
            <strong>{service.ok === false ? 'down' : 'ok'}</strong>
          </div>
        ))}
      </StatusCard>

      <StatusCard title="System Health">
        <div className="studio-metric-row">
          <span>GPU</span>
          <strong>{textValue(gpu.name, 'RTX')} {percentValue(gpu.util_percent)}</strong>
        </div>
        <div className="studio-metric-row">
          <span>VRAM</span>
          <strong>{percentValue(gpu.memory_percent)}</strong>
        </div>
        <div className="studio-metric-row">
          <span>CPU</span>
          <strong>{percentValue(system.cpu_percent)}</strong>
        </div>
        <div className="studio-metric-row">
          <span>RAM</span>
          <strong>{percentValue(system.ram_percent)}</strong>
        </div>
      </StatusCard>

      <StatusCard title="Qwen Lanes">
        {(qwenRuntime?.lanes || []).slice(0, 5).map((lane) => (
          <div className="studio-progress-row studio-runtime-lane" key={lane.id || lane.alias || lane.label}>
            <span className={`studio-led ${lane.online === false ? 'offline' : 'online'}`} />
            <span>{lane.label || lane.alias || lane.id}</span>
            <strong>{runtimeSpeed(lane as Record<string, unknown>) || lane.verdict || lane.status || ''}</strong>
          </div>
        ))}
      </StatusCard>

      <StatusCard title="Outputs">
        {outputs.length === 0 ? (
          <div className="studio-muted">No outputs yet</div>
        ) : (
          outputs.map((output, index) => (
            <div className="studio-progress-row studio-output-row" key={index}>
              <FileText size={14} />
              <span>{textValue(output.path || output.title || output.type, 'Output')}</span>
              <button
                type="button"
                className="studio-mini-action"
                onClick={() => copyText(textValue(output.path || output.title || output.type))}
                title="Copy output reference"
              >
                <Copy size={12} />
              </button>
            </div>
          ))
        )}
      </StatusCard>

      <StatusCard title="File Activity">
        {fileActivity.length === 0 ? (
          <div className="studio-muted">No file edits</div>
        ) : (
          fileActivity.slice(0, 8).map((file, index) => (
            <div className="studio-progress-row studio-file-row" key={index}>
              <GitBranch size={14} />
              <span>{textValue(file.path || file.file || file.title, 'File')}</span>
              <strong className="diff-add">+{textValue(file.additions ?? file.added ?? 0, '0')}</strong>
              <strong className="diff-del">-{textValue(file.deletions ?? file.removed ?? 0, '0')}</strong>
            </div>
          ))
        )}
      </StatusCard>

      <StatusCard title="Browser">
        <div className="studio-metric-row">
          <Globe size={14} />
          <span>{previewAvailable ? 'Project preview available' : 'No preview target'}</span>
          <button type="button" className="studio-mini-action" onClick={onOpenPreview}>
            <ExternalLink size={12} />
          </button>
        </div>
      </StatusCard>

      <StatusCard title="Sources">
        <div className="studio-metric-row">
          <GitBranch size={14} />
          <span>Code Review Graph</span>
          <strong>{state.plugins?.some((plugin) => plugin.id === 'codegraph' && plugin.status === 'online') ? 'online' : 'ready'}</strong>
        </div>
        <div className="studio-metric-row">
          <Globe size={14} />
          <span>Web search</span>
          <strong>{state.runtime_health ? 'ready' : 'unknown'}</strong>
        </div>
      </StatusCard>

      <StatusCard title="Agents">
        {(state.agents || []).slice(0, 8).map((agent, index) => (
          <AgentRow agent={agent} key={agent.id || agent.name || index} />
        ))}
      </StatusCard>

      <StatusCard title="Plugins">
        {(state.plugins || []).slice(0, 6).map((plugin, index) => (
          <div className="studio-metric-row" key={index}>
            <PlugZap size={14} />
            <span>{String(plugin.name || plugin.id || 'Plugin')}</span>
            <strong>{String(plugin.status || 'online')}</strong>
          </div>
        ))}
      </StatusCard>
    </aside>
  );
}

import { Activity, Copy, Cpu, ExternalLink, FileText, GitBranch, Globe, PlugZap } from 'lucide-react';
import type { ReactNode } from 'react';
import type { StudioAgent, StudioRun, StudioState } from './types';

interface StudioContextRailProps {
  state: StudioState;
  activeRun?: StudioRun;
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

export function StudioContextRail({ state, activeRun, onOpenPreview, onUpdateWorker }: StudioContextRailProps) {
  const runtimeLane = state.qwen_runtime?.lanes?.find((lane) => lane.active) || state.qwen_runtime?.lanes?.[0];
  const tasks = activeRun?.task_details || activeRun?.tasks || [];
  const outputs = activeRun?.outputs || [];
  const fileActivity = activeRun?.file_activity || [];
  const previewAvailable = state.preview?.available === true;

  return (
    <aside className="studio-context-rail">
      <section className="studio-card">
        <h2>Desktop Actions</h2>
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
              <span>Steps</span>
              <strong>{tasks.length}</strong>
              <span>Outputs</span>
              <strong>{outputs.length}</strong>
            </div>
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

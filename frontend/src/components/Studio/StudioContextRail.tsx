import { Activity, Bot, Cpu, FileText, PlugZap } from 'lucide-react';
import type { ReactNode } from 'react';
import type { StudioAgent, StudioRun, StudioState } from './types';

interface StudioContextRailProps {
  state: StudioState;
  activeRun?: StudioRun;
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

export function StudioContextRail({ state, activeRun }: StudioContextRailProps) {
  const runtimeLane = state.qwen_runtime?.lanes?.find((lane) => lane.active) || state.qwen_runtime?.lanes?.[0];
  const tasks = activeRun?.task_details || activeRun?.tasks || [];
  const outputs = activeRun?.outputs || [];

  return (
    <aside className="studio-context-rail">
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
            <div className="studio-progress-row" key={index}>
              <FileText size={14} />
              <span>{String(output.path || output.title || output.type || 'Output')}</span>
            </div>
          ))
        )}
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

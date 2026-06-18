import type {
  ActivityItem,
  MessageTelemetry,
  PlanItem,
  RoutingInfo,
  SSEEvent,
  TokenUsage,
  ToolCallInfo,
} from '../types';

export interface StreamComplexity {
  score: number;
  tier: string;
  suggested_max_tokens: number;
}

export interface StreamAccumulator {
  content: string;
  thinking: string;
  toolCalls: ToolCallInfo[];
  activity: ActivityItem[];
  plan: PlanItem[];
  routing?: RoutingInfo;
  usage?: TokenUsage;
  complexity?: StreamComplexity;
  telemetry?: Partial<MessageTelemetry>;
  phase: string;
  tokens: number;
  ttftMs?: number;
  done: boolean;
}

export interface ReduceCtx {
  startTime: number;
  now: number;
}

let _seq = 0;
function nextId(): string {
  _seq += 1;
  return `act-${_seq}-${Math.random().toString(36).slice(2, 7)}`;
}

function planId(title: string): string {
  // Stable id derived from title so re-emitted plans reconcile by item.
  let h = 0;
  for (let i = 0; i < title.length; i += 1) {
    h = (h * 31 + title.charCodeAt(i)) | 0;
  }
  return `plan-${(h >>> 0).toString(36)}`;
}

export function initAccumulator(): StreamAccumulator {
  return {
    content: '',
    thinking: '',
    toolCalls: [],
    activity: [],
    plan: [],
    phase: 'Generating...',
    tokens: 0,
    done: false,
  };
}

function parse(data: string): any | null {
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}

/**
 * Pure reduction of one SSE event onto the accumulator. Mutates and returns the
 * same object (callers treat it as owned). Never throws on malformed payloads.
 */
export function reduceStreamEvent(
  acc: StreamAccumulator,
  ev: SSEEvent,
  ctx: ReduceCtx,
): StreamAccumulator {
  const name = ev.event;

  if (name === 'agent_turn_start') {
    acc.phase = 'Agent thinking...';
    return acc;
  }
  if (name === 'inference_start') {
    acc.phase = 'Generating...';
    return acc;
  }
  if (name === 'inference_end') {
    return acc;
  }
  if (name === 'tool_call_start') {
    const d = parse(ev.data);
    if (d && d.tool) {
      const tc: ToolCallInfo = {
        id: nextId(),
        tool: d.tool,
        arguments: d.arguments || '',
        status: 'running',
      };
      acc.toolCalls.push(tc);
      acc.activity.push({
        kind: 'tool',
        id: tc.id,
        tool: tc.tool,
        arguments: tc.arguments,
        status: 'running',
      });
      acc.phase = `Calling ${d.tool}...`;
    }
    return acc;
  }
  if (name === 'tool_call_end') {
    const d = parse(ev.data);
    if (d && d.tool) {
      const tc = acc.toolCalls.find((t) => t.tool === d.tool && t.status === 'running');
      if (tc) {
        tc.status = d.success ? 'success' : 'error';
        tc.latency = d.latency;
        tc.result = d.result;
        const a = acc.activity.find(
          (x) => x.kind === 'tool' && x.id === tc.id,
        ) as Extract<ActivityItem, { kind: 'tool' }> | undefined;
        if (a) {
          a.status = tc.status;
          a.latency = tc.latency;
          a.result = tc.result;
        }
      }
      // Any tool whose result carries a unified diff (e.g. file_edit) becomes a
      // file_edit activity so the chat renders it as an inline diff.
      const md = d.metadata;
      if (md && md.diff && md.path) {
        acc.activity.push({
          kind: 'file_edit',
          editId: md.edit_id || nextId(),
          path: md.path,
          diff: md.diff,
          added: md.added || 0,
          removed: md.removed || 0,
        });
      }
      acc.phase = 'Generating...';
    }
    return acc;
  }
  if (name === 'thinking_delta') {
    const d = parse(ev.data);
    if (d && typeof d.text === 'string') acc.thinking += d.text;
    return acc;
  }
  if (name === 'plan') {
    const d = parse(ev.data);
    if (d && Array.isArray(d.items)) {
      acc.plan = d.items.map((it: any) => ({
        id: it.id || planId(String(it.title || '')),
        title: String(it.title || ''),
        status: it.status === 'in_progress' || it.status === 'completed' ? it.status : 'pending',
      }));
      acc.activity.push({ kind: 'plan', items: acc.plan });
    }
    return acc;
  }
  if (name === 'file_edit') {
    const d = parse(ev.data);
    if (d && d.path) {
      acc.activity.push({
        kind: 'file_edit',
        editId: d.edit_id || nextId(),
        path: d.path,
        diff: d.diff || '',
        added: d.added || 0,
        removed: d.removed || 0,
      });
    }
    return acc;
  }
  if (name === 'escalation') {
    const d = parse(ev.data);
    if (d) {
      acc.activity.push({
        kind: 'escalation',
        from: d.from || '',
        to: d.to || '',
        reason: d.reason || '',
        score: d.score,
      });
    }
    return acc;
  }
  if (name === 'routing') {
    const d = parse(ev.data);
    if (d && d.brain) {
      acc.routing = { brain: d.brain, model: d.model || '', lane: d.lane, health: d.health };
      acc.activity.push({ kind: 'routing', brain: d.brain, model: d.model || '', lane: d.lane, health: d.health });
    }
    return acc;
  }
  if (name === 'citation') {
    const d = parse(ev.data);
    if (d && d.url) {
      acc.activity.push({ kind: 'citation', ref: d.ref || '', url: d.url, title: d.title });
    }
    return acc;
  }
  if (name === 'verification') {
    const d = parse(ev.data);
    if (d && typeof d.cmd === 'string') {
      acc.activity.push({ kind: 'verification', cmd: d.cmd, passed: !!d.passed, output: d.output });
    }
    return acc;
  }

  // Default: OpenAI-compatible chunk (no explicit event name).
  const d = parse(ev.data);
  if (!d) return acc;
  if (d.usage) acc.usage = d.usage;
  if (d.complexity) acc.complexity = d.complexity;
  if (d.telemetry) acc.telemetry = d.telemetry;
  const delta = d.choices?.[0]?.delta;
  if (delta?.content) {
    if (acc.ttftMs === undefined) acc.ttftMs = ctx.now - ctx.startTime;
    acc.content += delta.content;
    acc.tokens = Math.ceil(acc.content.length / 4);
    acc.phase = '';
  }
  if (d.choices?.[0]?.finish_reason === 'stop') acc.done = true;
  return acc;
}

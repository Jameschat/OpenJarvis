import { describe, it, expect } from 'vitest';
import { initAccumulator, reduceStreamEvent } from './streamReducer';

const ctx = { startTime: 1000, now: 1100 };

describe('reduceStreamEvent', () => {
  it('accumulates content from default chunks and detects finish', () => {
    let acc = initAccumulator();
    acc = reduceStreamEvent(acc, { event: undefined, data: JSON.stringify({ choices: [{ delta: { content: 'Hel' } }] }) }, ctx);
    acc = reduceStreamEvent(acc, { event: undefined, data: JSON.stringify({ choices: [{ delta: { content: 'lo' } }] }) }, ctx);
    expect(acc.content).toBe('Hello');
    expect(acc.done).toBe(false);
    acc = reduceStreamEvent(acc, { event: undefined, data: JSON.stringify({ choices: [{ delta: {}, finish_reason: 'stop' }] }) }, ctx);
    expect(acc.done).toBe(true);
  });

  it('records ttft on first content token', () => {
    let acc = initAccumulator();
    acc = reduceStreamEvent(acc, { event: undefined, data: JSON.stringify({ choices: [{ delta: { content: 'x' } }] }) }, { startTime: 1000, now: 1250 });
    expect(acc.ttftMs).toBe(250);
  });

  it('captures usage, complexity, and telemetry from chunks', () => {
    let acc = initAccumulator();
    acc = reduceStreamEvent(acc, { event: undefined, data: JSON.stringify({ usage: { prompt_tokens: 3, completion_tokens: 2, total_tokens: 5 }, complexity: { score: 0.4, tier: 'moderate', suggested_max_tokens: 4096 }, telemetry: { decode_tok_s: 60 } }) }, ctx);
    expect(acc.usage?.total_tokens).toBe(5);
    expect(acc.complexity?.tier).toBe('moderate');
    expect(acc.telemetry?.decode_tok_s).toBe(60);
  });

  it('opens a running tool call and closes it on tool_call_end', () => {
    let acc = initAccumulator();
    acc = reduceStreamEvent(acc, { event: 'tool_call_start', data: JSON.stringify({ tool: 'web_search', arguments: '{"q":"x"}' }) }, ctx);
    expect(acc.toolCalls).toHaveLength(1);
    expect(acc.toolCalls[0].status).toBe('running');
    expect(acc.activity.some((a) => a.kind === 'tool')).toBe(true);
    acc = reduceStreamEvent(acc, { event: 'tool_call_end', data: JSON.stringify({ tool: 'web_search', success: true, latency: 42, result: 'ok' }) }, ctx);
    expect(acc.toolCalls[0].status).toBe('success');
    expect(acc.toolCalls[0].latency).toBe(42);
    expect(acc.toolCalls[0].result).toBe('ok');
  });

  it('stringifies object tool_call_start arguments (live ToolExecutor sends objects)', () => {
    let acc = initAccumulator();
    acc = reduceStreamEvent(acc, { event: 'tool_call_start', data: JSON.stringify({ tool: 'file_edit', arguments: { path: 'a.py', old_string: 'x', new_string: 'y' } }) }, ctx);
    expect(typeof acc.toolCalls[0].arguments).toBe('string');
    expect(acc.toolCalls[0].arguments).toContain('a.py');
    const a = acc.activity.find((x) => x.kind === 'tool');
    expect(a && a.kind === 'tool' && typeof a.arguments).toBe('string');
  });

  it('creates a file_edit activity from tool_call_end diff metadata', () => {
    let acc = initAccumulator();
    acc = reduceStreamEvent(acc, { event: 'tool_call_start', data: JSON.stringify({ tool: 'file_edit', arguments: '{}' }) }, ctx);
    acc = reduceStreamEvent(acc, { event: 'tool_call_end', data: JSON.stringify({ tool: 'file_edit', success: true, latency: 5, result: 'Edited a.py (+1 -1)', metadata: { path: '/x/a.py', diff: '--- a\n+++ b\n-return 1\n+return 2', added: 1, removed: 1, edit_id: 'e9' } }) }, ctx);
    const edit = acc.activity.find((a) => a.kind === 'file_edit');
    expect(edit).toBeTruthy();
    expect(edit && edit.kind === 'file_edit' && edit.path).toBe('/x/a.py');
    expect(edit && edit.kind === 'file_edit' && edit.added).toBe(1);
    expect(edit && edit.kind === 'file_edit' && edit.editId).toBe('e9');
  });

  it('updates the plan from a tool result carrying an items array', () => {
    let acc = initAccumulator();
    acc = reduceStreamEvent(acc, { event: 'tool_call_end', data: JSON.stringify({ tool: 'todo_write', success: true, latency: 1, result: '2 tasks', metadata: { items: [{ title: 'A', status: 'completed' }, { title: 'B', status: 'in_progress' }] } }) }, ctx);
    expect(acc.plan).toHaveLength(2);
    expect(acc.plan[0].status).toBe('completed');
    expect(acc.plan[0].id).toBeTruthy();
    expect(acc.activity.some((a) => a.kind === 'plan')).toBe(true);
  });

  it('sets phase on agent_turn_start and inference_start', () => {
    let acc = initAccumulator();
    acc = reduceStreamEvent(acc, { event: 'agent_turn_start', data: '{}' }, ctx);
    expect(acc.phase).toBe('Agent thinking...');
    acc = reduceStreamEvent(acc, { event: 'inference_start', data: '{}' }, ctx);
    expect(acc.phase).toBe('Generating...');
  });

  it('appends thinking deltas', () => {
    let acc = initAccumulator();
    acc = reduceStreamEvent(acc, { event: 'thinking_delta', data: JSON.stringify({ text: 'reason ' }) }, ctx);
    acc = reduceStreamEvent(acc, { event: 'thinking_delta', data: JSON.stringify({ text: 'more' }) }, ctx);
    expect(acc.thinking).toBe('reason more');
  });

  it('replaces the plan list on plan events and tracks it in activity', () => {
    let acc = initAccumulator();
    acc = reduceStreamEvent(acc, { event: 'plan', data: JSON.stringify({ items: [{ title: 'Read file', status: 'in_progress' }, { title: 'Edit', status: 'pending' }] }) }, ctx);
    expect(acc.plan).toHaveLength(2);
    expect(acc.plan[0].id).toBeTruthy();
    expect(acc.plan[0].status).toBe('in_progress');
    acc = reduceStreamEvent(acc, { event: 'plan', data: JSON.stringify({ items: [{ title: 'Read file', status: 'completed' }] }) }, ctx);
    expect(acc.plan).toHaveLength(1);
    expect(acc.plan[0].status).toBe('completed');
  });

  it('records file_edit, escalation, routing, citation, verification in activity', () => {
    let acc = initAccumulator();
    acc = reduceStreamEvent(acc, { event: 'file_edit', data: JSON.stringify({ edit_id: 'e1', path: 'a.ts', diff: '+x', added: 1, removed: 0 }) }, ctx);
    acc = reduceStreamEvent(acc, { event: 'escalation', data: JSON.stringify({ from: 'local', to: 'cloud', reason: 'weak', score: 0.2 }) }, ctx);
    acc = reduceStreamEvent(acc, { event: 'routing', data: JSON.stringify({ brain: 'remote', model: 'qwen3.6:35b-a3b', lane: '35b', health: 'ok' }) }, ctx);
    acc = reduceStreamEvent(acc, { event: 'citation', data: JSON.stringify({ ref: '1', url: 'https://x', title: 'X' }) }, ctx);
    acc = reduceStreamEvent(acc, { event: 'verification', data: JSON.stringify({ cmd: 'pytest', passed: true }) }, ctx);
    const kinds = acc.activity.map((a) => a.kind);
    expect(kinds).toEqual(['file_edit', 'escalation', 'routing', 'citation', 'verification']);
    expect(acc.routing?.brain).toBe('remote');
  });

  it('ignores malformed JSON without throwing', () => {
    let acc = initAccumulator();
    expect(() => {
      acc = reduceStreamEvent(acc, { event: 'tool_call_start', data: 'not json' }, ctx);
      acc = reduceStreamEvent(acc, { event: undefined, data: 'not json' }, ctx);
    }).not.toThrow();
  });
});

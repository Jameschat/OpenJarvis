import { useCallback, useEffect, useRef, useState } from 'react';
import { getBase } from '../lib/api';

/**
 * Memory neural page (Phase 8 #4, operator-requested).
 *
 * A live constellation of the memory systems — Obsidian vault at the centre,
 * Graphify + Agent Memory in orbit, the actor brains (J.A.R.V.I.S., Qwen,
 * Claude, Codex) at the edges — with "neuron" pulses firing along the edges
 * for every read/write/append, and a realtime log table below. Data feed:
 * GET /memory/activity?since=<ts> (2s poll), shipped 2026-06-13.
 */

interface ActivityNode {
  id: string;
  label: string;
  online: boolean;
  stat: string;
}

interface ActivityPulse {
  ts: number;
  source: string;
  target: string;
  op: string;
  label: string;
  kind: string;
}

interface ActivityLogRow {
  ts: number;
  actor: string;
  op: string;
  label: string;
  kind: string;
}

interface ActivitySnapshot {
  ts: number;
  nodes: ActivityNode[];
  pulses: ActivityPulse[];
  log: ActivityLogRow[];
}

// Layout positions as fractions of the canvas (x, y).
const NODE_POS: Record<string, [number, number]> = {
  vault: [0.5, 0.44],
  graphify: [0.5, 0.12],
  agentmemory: [0.5, 0.8],
  jarvis: [0.13, 0.44],
  qwen: [0.87, 0.18],
  claude: [0.87, 0.5],
  codex: [0.87, 0.8],
};

const OP_COLOR: Record<string, string> = {
  write: '#ffd76a',
  append: '#b478ff',
  read: '#5ecfff',
  notify: '#ff6b9d',
};

const NODE_COLOR: Record<string, string> = {
  vault: '#ffd76a',
  graphify: '#5ee0a1',
  agentmemory: '#b478ff',
  jarvis: '#5ecfff',
  qwen: '#a3e3d8',
  claude: '#e8a87c',
  codex: '#74aa9c',
};

interface LivePulse extends ActivityPulse {
  startedAt: number; // performance.now() when the particle launched
  duration: number;
}

const PULSE_TRAVEL_MS = 1400;
const LOG_CAP = 200;

function formatTime(ts: number): string {
  try {
    return new Date(ts * 1000).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return '';
  }
}

export function MemoryPage() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [nodes, setNodes] = useState<ActivityNode[]>([]);
  const [log, setLog] = useState<ActivityLogRow[]>([]);
  const [error, setError] = useState('');
  const [paused, setPaused] = useState(false);
  const livePulsesRef = useRef<LivePulse[]>([]);
  const nodeGlowRef = useRef<Record<string, number>>({});
  const nodesRef = useRef<ActivityNode[]>([]);
  const sinceRef = useRef(0);
  const reducedMotion =
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

  const poll = useCallback(async () => {
    try {
      const since = sinceRef.current ? `?since=${sinceRef.current}` : '';
      const res = await fetch(`${getBase()}/memory/activity${since}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const snap: ActivitySnapshot = await res.json();
      sinceRef.current = snap.ts;
      setNodes(snap.nodes || []);
      nodesRef.current = snap.nodes || [];
      if (snap.log?.length) {
        setLog((prev) => [...snap.log, ...prev].slice(0, LOG_CAP));
      }
      if (snap.pulses?.length && !reducedMotion) {
        const now = performance.now();
        snap.pulses.forEach((p, i) => {
          livePulsesRef.current.push({
            ...p,
            startedAt: now + i * 220, // stagger bursts so they read as traffic
            duration: PULSE_TRAVEL_MS,
          });
        });
      }
      setError('');
    } catch (err) {
      setError(String(err));
    }
  }, [reducedMotion]);

  useEffect(() => {
    void poll();
    const id = setInterval(() => {
      if (!paused) void poll();
    }, 2000);
    return () => clearInterval(id);
  }, [poll, paused]);

  // Canvas render loop
  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    let raf = 0;

    const draw = () => {
      const rect = container.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      if (canvas.width !== rect.width * dpr || canvas.height !== rect.height * dpr) {
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const w = rect.width;
      const h = rect.height;
      ctx.clearRect(0, 0, w, h);
      const t = performance.now();

      const px = (id: string): [number, number] => {
        const [fx, fy] = NODE_POS[id] || [0.5, 0.5];
        return [fx * w, fy * h];
      };

      // Faint resting edges vault<->everything
      ctx.lineWidth = 1;
      for (const id of Object.keys(NODE_POS)) {
        if (id === 'vault') continue;
        const [x1, y1] = px('vault');
        const [x2, y2] = px(id);
        ctx.strokeStyle = 'rgba(120, 140, 170, 0.10)';
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.stroke();
      }

      // Live pulses
      livePulsesRef.current = livePulsesRef.current.filter((p) => {
        const progress = (t - p.startedAt) / p.duration;
        if (progress >= 1) {
          nodeGlowRef.current[p.target] = t; // flash the target on arrival
          return false;
        }
        if (progress < 0) return true; // staggered, not launched yet
        const [x1, y1] = px(p.source);
        const [x2, y2] = px(p.target);
        const x = x1 + (x2 - x1) * progress;
        const y = y1 + (y2 - y1) * progress;
        const color = OP_COLOR[p.op] || '#9aa7bd';
        // trail
        ctx.strokeStyle = `${color}33`;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(x1 + (x2 - x1) * Math.max(0, progress - 0.18), y1 + (y2 - y1) * Math.max(0, progress - 0.18));
        ctx.lineTo(x, y);
        ctx.stroke();
        // particle
        ctx.fillStyle = color;
        ctx.shadowColor = color;
        ctx.shadowBlur = 12;
        ctx.beginPath();
        ctx.arc(x, y, 3.4, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;
        return true;
      });

      // Nodes
      for (const node of nodesRef.current) {
        const [x, y] = px(node.id);
        const color = NODE_COLOR[node.id] || '#9aa7bd';
        const breath = 1 + 0.06 * Math.sin(t / 900 + x);
        const glowAge = t - (nodeGlowRef.current[node.id] || -1e9);
        const hit = glowAge < 600 ? (1 - glowAge / 600) : 0;
        const radius = (node.id === 'vault' ? 26 : 17) * breath + hit * 6;

        ctx.shadowColor = color;
        ctx.shadowBlur = node.online ? 14 + hit * 30 : 0;
        ctx.fillStyle = node.online ? `${color}2e` : 'rgba(100,110,130,0.12)';
        ctx.strokeStyle = node.online ? color : 'rgba(120,130,150,0.4)';
        ctx.lineWidth = 1.5 + hit * 1.5;
        ctx.beginPath();
        ctx.arc(x, y, radius, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        ctx.shadowBlur = 0;

        ctx.fillStyle = node.online ? '#e8eefc' : 'rgba(170,180,200,0.55)';
        ctx.font = '600 11px "IBM Plex Mono", monospace';
        ctx.textAlign = 'center';
        ctx.fillText(node.label, x, y + radius + 16);
        if (node.stat) {
          ctx.fillStyle = 'rgba(150,165,195,0.7)';
          ctx.font = '10px "IBM Plex Mono", monospace';
          ctx.fillText(node.stat, x, y + radius + 30);
        }
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div style={{ padding: '16px 20px 8px', display: 'flex', alignItems: 'baseline', gap: 12 }}>
        <h1 style={{ fontSize: 18, fontWeight: 700, color: 'var(--color-text)' }}>Memory</h1>
        <span style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>
          obsidian · graphify · agent memory · live neural activity
        </span>
        <span style={{ flex: 1 }} />
        {error && (
          <span style={{ fontSize: 11, color: 'var(--color-error, #ff6b6b)' }}>
            feed offline: {error}
          </span>
        )}
        <button
          type="button"
          onClick={() => setPaused((p) => !p)}
          style={{
            fontSize: 11,
            padding: '4px 10px',
            borderRadius: 6,
            border: '1px solid var(--color-border, #2a3346)',
            background: 'transparent',
            color: 'var(--color-text-secondary)',
            cursor: 'pointer',
          }}
        >
          {paused ? '▶ resume feed' : '⏸ pause feed'}
        </button>
      </div>

      <div
        ref={containerRef}
        style={{ flex: '1 1 55%', minHeight: 260, position: 'relative', margin: '0 12px' }}
      >
        <canvas ref={canvasRef} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }} />
      </div>

      <div
        style={{
          flex: '1 1 45%',
          minHeight: 140,
          margin: '8px 12px 12px',
          border: '1px solid var(--color-border, #2a3346)',
          borderRadius: 8,
          overflow: 'auto',
          fontFamily: '"IBM Plex Mono", monospace',
          fontSize: 11.5,
        }}
        data-testid="memory-log"
      >
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ position: 'sticky', top: 0, background: 'var(--color-bg-secondary, #10141d)' }}>
              {['time', 'actor', 'op', 'note / file', 'kind'].map((hdr) => (
                <th
                  key={hdr}
                  style={{
                    textAlign: 'left',
                    padding: '6px 10px',
                    color: 'var(--color-text-tertiary)',
                    fontWeight: 600,
                    borderBottom: '1px solid var(--color-border, #2a3346)',
                  }}
                >
                  {hdr}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {log.length === 0 && (
              <tr>
                <td colSpan={5} style={{ padding: 14, color: 'var(--color-text-tertiary)' }}>
                  Waiting for memory activity — reads, writes and file edits appear here in realtime.
                </td>
              </tr>
            )}
            {log.map((row, i) => (
              <tr key={`${row.ts}-${i}`} style={{ borderBottom: '1px solid rgba(80,95,125,0.12)' }}>
                <td style={{ padding: '5px 10px', color: 'var(--color-text-tertiary)', whiteSpace: 'nowrap' }}>
                  {formatTime(row.ts)}
                </td>
                <td style={{ padding: '5px 10px', color: NODE_COLOR[row.actor] || 'var(--color-text-secondary)' }}>
                  {row.actor}
                </td>
                <td style={{ padding: '5px 10px' }}>
                  <span
                    style={{
                      color: OP_COLOR[row.op] || 'var(--color-text-secondary)',
                      border: `1px solid ${(OP_COLOR[row.op] || '#9aa7bd')}55`,
                      borderRadius: 4,
                      padding: '1px 6px',
                    }}
                  >
                    {row.op}
                  </span>
                </td>
                <td style={{ padding: '5px 10px', color: 'var(--color-text)' }}>{row.label}</td>
                <td style={{ padding: '5px 10px', color: 'var(--color-text-tertiary)' }}>{row.kind}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

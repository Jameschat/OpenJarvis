interface Props {
  elapsedMs: number;
  tokens: number;
  phase?: string;
}

// "9m 24s" / "24s" / "1h 2m" — Claude/Codex-style elapsed.
export function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

// "5.0k" for >=1000, else the integer.
export function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return `${n}`;
}

// The live "working" line shown while Jarvis is generating — a twinkling glyph
// plus elapsed time and a running token count, mirroring the Claude/Codex CLI.
export function WorkingStatus({ elapsedMs, tokens, phase }: Props) {
  const showPhase = tokens === 0 && !!phase;
  const right = tokens > 0
    ? `${formatElapsed(elapsedMs)} · ${formatTokens(tokens)} tokens`
    : `${phase ? `${phase} · ` : ''}${formatElapsed(elapsedMs)}`;
  void showPhase;
  return (
    <div
      className="flex items-center gap-2 py-1.5"
      style={{ fontFamily: 'var(--font-hud)' }}
      aria-live="polite"
    >
      <span
        aria-hidden="true"
        style={{
          color: 'var(--color-accent)',
          fontSize: '13px',
          lineHeight: 1,
          display: 'inline-block',
          animation: 'spin 2.6s linear infinite',
        }}
      >
        {'✻'}
      </span>
      <span className="text-xs tabular-nums" style={{ color: 'var(--color-text-tertiary)' }}>
        {right}
      </span>
    </div>
  );
}

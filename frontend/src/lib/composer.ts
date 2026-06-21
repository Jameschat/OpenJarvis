// Composer controls (unified from Studio) — pure, side-effect-free helpers so
// they're testable without instantiating the zustand store (which touches
// localStorage at import). The store re-exports these.

// Profiles backed by a real, working lane:
//   local35 -> local 35B-A3B lane on :8084 (~140 tok/s, 256K) — DEFAULT, reasoning champ
//   coder   -> local 30B-Coder lane on :8084 (MoE, ~78 tok/s, 64K) — coding-specialized
//   fast    -> local 27B-MTP lane on :8084 (speculative, 32K)     — Qwen3.6-27B base
//   gptoss  -> local gpt-oss-20b lane on :8084 (~140 tok/s, 64K)  — fast/frugal agentic
//   remote  -> 35B-A3B on the LAN worker (via LiteLLM)            — off-box deep-work
// local35/coder/fast/gptoss all share the single 24GB GPU lane on :8084, so
// switching between them triggers a real lane swap (free VRAM + load target,
// ~1 min). They serve the `qwen3.6-27b-local` alias, so LiteLLM routing follows.
export type ComposerProfile = 'local35' | 'coder' | 'fast' | 'gptoss' | 'remote';
export type PermissionMode = 'default' | 'readonly' | 'auto';

export const PROFILE_MODEL: Record<ComposerProfile, string> = {
  local35: 'qwen3.6-27b-local',
  coder: 'qwen3.6-27b-local',
  fast: 'qwen3.6-27b-local',
  gptoss: 'qwen3.6-27b-local',
  remote: 'qwen3.6-35b-a3b-remote',
};

// Human-friendly lane labels (the LiteLLM model alias is shared across the local
// lanes, so the alias alone can't tell them apart in the UI).
export const PROFILE_LABELS: Record<ComposerProfile, string> = {
  local35: 'Qwen 35B-A3B (local, 256K)',
  coder: 'Qwen Coder 30B (local)',
  fast: 'Qwen 27B Fast (MTP)',
  gptoss: 'gpt-oss 20B (local, agentic)',
  remote: 'Remote 35B-A3B',
};

// The default profile when none is persisted — the 35B-A3B local lane.
export const DEFAULT_PROFILE: ComposerProfile = 'local35';

// Engine class for a given profile — drives the telemetry footer label so it
// reflects reality (local llama.cpp lane via LiteLLM / remote worker / cloud),
// never the old hardcoded "ollama".
export function engineForProfile(profile: ComposerProfile): string {
  return profile === 'remote' ? 'remote' : 'local';
}

// Local profiles share the :8084 GPU lane and need a swap when switched between.
export const LOCAL_SWAP_PROFILES: ComposerProfile[] = ['local35', 'coder', 'fast', 'gptoss'];

export function isComposerProfile(v: unknown): v is ComposerProfile {
  return v === 'local35' || v === 'coder' || v === 'fast' || v === 'gptoss' || v === 'remote';
}

export interface SkillOption {
  id: string;
  label: string;
  description: string;
}

export const COMPOSER_SKILLS: SkillOption[] = [
  { id: 'taste-skill', label: 'Taste Skill', description: 'Anti-slop frontend taste for landing pages, portfolios, and redesigns.' },
  { id: 'ui-ux-pro-max', label: 'UI UX Pro Max', description: 'Premium responsive UI, visual hierarchy, accessibility, and browser visual QA.' },
  { id: 'superpowers', label: 'Superpowers', description: 'Plan, verify, and keep the work scoped like Codex/Superpowers.' },
  { id: 'browser-qa', label: 'Browser QA', description: 'Preview the page and catch layout, alignment, and interaction issues.' },
  { id: 'context7-docs', label: 'Context7 Docs', description: 'Use current library documentation before coding against changing APIs.' },
  { id: 'shadcn-ui', label: 'shadcn/ui', description: 'Use shadcn/ui as the preferred component system for modern frontend builds.' },
  { id: 'ccpm-planning', label: 'CCPM Planning', description: 'Prototype PRD, issue, and phased project planning before larger builds.' },
];

const PERMISSION_DIRECTIVE: Record<PermissionMode, string> = {
  default: '',
  readonly:
    'Permission mode: READ-ONLY. You may read files, search, and inspect, but do NOT write, edit, or delete files or run mutating commands. Propose changes as diffs for approval instead of applying them.',
  auto:
    'Permission mode: FULL AUTO. Proceed end-to-end without pausing for confirmation. Use your tools (read/edit/write/preview) to complete the task, then report what you did.',
};

/**
 * Build the system preamble that carries the composer controls into the chat
 * run. Skills, permission mode, and pinned context all become a single system
 * message the orchestrator reads — the honest equivalent of Studio's run fields
 * on the plain /v1/chat/completions path. Returns '' when nothing is active.
 */
export function buildComposerSystemMessage(
  skillIds: string[],
  permissionMode: PermissionMode,
  contextItems: string[],
): string {
  const parts: string[] = [];
  const skills = COMPOSER_SKILLS.filter((s) => skillIds.includes(s.id));
  if (skills.length) {
    parts.push('[Active skills]\n' + skills.map((s) => `- ${s.label}: ${s.description}`).join('\n'));
  }
  const directive = PERMISSION_DIRECTIVE[permissionMode];
  if (directive) parts.push(directive);
  if (contextItems.length) {
    parts.push('[Attached context]\n' + contextItems.map((c, i) => `${i + 1}. ${c}`).join('\n\n'));
  }
  return parts.join('\n\n');
}

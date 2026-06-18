// Composer controls (unified from Studio) — pure, side-effect-free helpers so
// they're testable without instantiating the zustand store (which touches
// localStorage at import). The store re-exports these.

export type ComposerProfile = 'coder' | 'fast' | 'quality' | 'remote';
export type PermissionMode = 'default' | 'readonly' | 'auto';

// A profile maps to the LiteLLM model alias the chat run should request. The
// 'coder' profile also triggers a real local-lane swap (free VRAM + load the
// 30B coder lane) via the Studio qwen-profile endpoint.
export const PROFILE_MODEL: Record<ComposerProfile, string> = {
  coder: 'qwen3.6-27b-local',
  fast: 'qwen3.6-27b-local',
  quality: 'qwen3.6-27b-quality',
  remote: 'qwen3.6-35b-a3b-remote',
};

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

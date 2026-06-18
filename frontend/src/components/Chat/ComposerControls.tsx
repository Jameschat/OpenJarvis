import { useState } from 'react';
import { Paperclip, ChevronDown, ShieldCheck, Sparkles } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore } from '../../lib/store';
import { COMPOSER_SKILLS, type ComposerProfile, type PermissionMode } from '../../lib/store';
import { setStudioQwenProfile } from '../../lib/studio-api';

const PROFILE_LABELS: Record<ComposerProfile, string> = {
  coder: 'Qwen Coder 30B (local)',
  fast: 'Qwen 27B Fast',
  quality: 'Qwen 27B Quality',
  remote: 'Remote 35B-A3B',
};

const PERMISSION_LABELS: Record<PermissionMode, string> = {
  default: 'Default permissions',
  readonly: 'Read-only',
  auto: 'Full auto',
};

// Profiles that require a local-lane swap (free VRAM + load the target lane).
// 'fast' stays on the already-loaded lane, so no swap call is made.
const SWAP_PROFILES: ComposerProfile[] = ['coder', 'quality', 'remote'];

const pillStyle: React.CSSProperties = {
  background: 'var(--color-bg-tertiary)',
  color: 'var(--color-text-secondary)',
  // Longhand (not the `border` shorthand) so the active state can override
  // borderColor alone without React's shorthand/longhand-conflict warning.
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'var(--color-input-border)',
};

export function ComposerControls() {
  const composerProfile = useAppStore((s) => s.composerProfile);
  const selectedSkills = useAppStore((s) => s.selectedSkills);
  const permissionMode = useAppStore((s) => s.permissionMode);
  const contextItems = useAppStore((s) => s.contextItems);
  const setComposerProfile = useAppStore((s) => s.setComposerProfile);
  const toggleSkill = useAppStore((s) => s.toggleSkill);
  const setPermissionMode = useAppStore((s) => s.setPermissionMode);
  const addContextItem = useAppStore((s) => s.addContextItem);
  const removeContextItem = useAppStore((s) => s.removeContextItem);

  const [contextOpen, setContextOpen] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [permOpen, setPermOpen] = useState(false);
  const [contextDraft, setContextDraft] = useState('');

  const handleProfile = async (next: ComposerProfile) => {
    setComposerProfile(next);
    if (!SWAP_PROFILES.includes(next)) return;
    // Real local-lane swap: stop the active lane to free VRAM and load the
    // target. Maps to the same backend endpoint Studio uses.
    try {
      const res = await setStudioQwenProfile(next);
      if (res && (res as { switching?: boolean }).switching) {
        toast.success('Swapping local lane', {
          description: `Loading ${PROFILE_LABELS[next]} (~1 min). Local chat pauses until it is ready.`,
        });
      } else {
        toast.success(`Profile set to ${PROFILE_LABELS[next]}`);
      }
    } catch (error: any) {
      toast.error('Could not switch profile', { description: error?.message || String(error) });
    }
  };

  const selectedSkillLabels = COMPOSER_SKILLS.filter((s) => selectedSkills.includes(s.id)).map((s) => s.label);

  return (
    <div className="mt-2" style={{ maxWidth: 'var(--chat-max-width)', margin: '0.5rem auto 0', width: '100%' }}>
      {contextOpen && (
        <div
          className="mb-2 rounded-xl p-3 text-sm"
          style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-input-border)' }}
        >
          <div className="flex items-center justify-between mb-2" style={{ color: 'var(--color-text-secondary)' }}>
            <strong>Pinned context</strong>
            <span className="text-xs">{contextItems.length} attached</span>
          </div>
          {contextItems.length > 0 && (
            <div className="flex flex-col gap-1 mb-2">
              {contextItems.map((item, index) => (
                <div
                  key={`${index}-${item.slice(0, 16)}`}
                  className="flex items-center justify-between gap-2 rounded-lg px-2 py-1 text-xs"
                  style={{ background: 'var(--color-bg-tertiary)' }}
                >
                  <span className="truncate">{item}</span>
                  <button
                    type="button"
                    className="shrink-0 cursor-pointer hover:underline"
                    style={{ color: 'var(--color-error)' }}
                    onClick={() => removeContextItem(index)}
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
          )}
          <textarea
            value={contextDraft}
            onChange={(e) => setContextDraft(e.target.value)}
            placeholder="Paste project notes, requirements, file paths, URLs, or constraints to attach to the next message..."
            rows={2}
            className="w-full rounded-lg p-2 text-sm outline-none resize-none"
            style={{ background: 'var(--color-input-bg)', color: 'var(--color-text)', border: '1px solid var(--color-input-border)' }}
          />
          <button
            type="button"
            disabled={!contextDraft.trim()}
            onClick={() => {
              addContextItem(contextDraft);
              setContextDraft('');
            }}
            className="mt-2 rounded-lg px-3 py-1 text-xs font-medium cursor-pointer disabled:opacity-40"
            style={{ background: 'var(--color-accent)', color: 'white' }}
          >
            Add context
          </button>
        </div>
      )}

      <div className="flex items-center gap-2 flex-wrap text-xs">
        {/* Context */}
        <button
          type="button"
          onClick={() => setContextOpen((o) => !o)}
          className="flex items-center gap-1 rounded-full px-3 py-1 cursor-pointer"
          style={{ ...pillStyle, ...(contextOpen ? { borderColor: 'var(--color-accent)', color: 'var(--color-accent)' } : {}) }}
        >
          <Paperclip size={13} />
          Context{contextItems.length ? ` (${contextItems.length})` : ''}
        </button>

        {/* Skills */}
        <div className="relative">
          <button
            type="button"
            onClick={() => { setSkillsOpen((o) => !o); setPermOpen(false); }}
            className="flex items-center gap-1 rounded-full px-3 py-1 cursor-pointer"
            style={{ ...pillStyle, ...(selectedSkillLabels.length ? { borderColor: 'var(--color-accent)', color: 'var(--color-accent)' } : {}) }}
          >
            <Sparkles size={13} />
            {selectedSkillLabels.length ? `Skills (${selectedSkillLabels.length})` : 'Skills'}
            <ChevronDown size={12} />
          </button>
          {skillsOpen && (
            <div
              className="absolute bottom-full mb-1 left-0 z-20 w-72 max-h-72 overflow-auto rounded-xl p-1 shadow-lg"
              style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-input-border)' }}
            >
              {COMPOSER_SKILLS.map((skill) => {
                const active = selectedSkills.includes(skill.id);
                return (
                  <button
                    key={skill.id}
                    type="button"
                    onClick={() => toggleSkill(skill.id)}
                    className="w-full text-left rounded-lg px-2 py-1.5 cursor-pointer flex flex-col gap-0.5"
                    style={{ background: active ? 'var(--color-bg-tertiary)' : 'transparent' }}
                  >
                    <strong style={{ color: active ? 'var(--color-accent)' : 'var(--color-text)' }}>
                      {active ? '✓ ' : ''}{skill.label}
                    </strong>
                    <span style={{ color: 'var(--color-text-tertiary)' }}>{skill.description}</span>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Permissions */}
        <div className="relative">
          <button
            type="button"
            onClick={() => { setPermOpen((o) => !o); setSkillsOpen(false); }}
            className="flex items-center gap-1 rounded-full px-3 py-1 cursor-pointer"
            style={{ ...pillStyle, ...(permissionMode !== 'default' ? { borderColor: 'var(--color-accent)', color: 'var(--color-accent)' } : {}) }}
          >
            <ShieldCheck size={13} />
            {PERMISSION_LABELS[permissionMode]}
            <ChevronDown size={12} />
          </button>
          {permOpen && (
            <div
              className="absolute bottom-full mb-1 left-0 z-20 w-44 rounded-xl p-1 shadow-lg"
              style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-input-border)' }}
            >
              {(Object.keys(PERMISSION_LABELS) as PermissionMode[]).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => { setPermissionMode(mode); setPermOpen(false); }}
                  className="w-full text-left rounded-lg px-2 py-1.5 cursor-pointer"
                  style={{
                    background: permissionMode === mode ? 'var(--color-bg-tertiary)' : 'transparent',
                    color: permissionMode === mode ? 'var(--color-accent)' : 'var(--color-text)',
                  }}
                >
                  {permissionMode === mode ? '✓ ' : ''}{PERMISSION_LABELS[mode]}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Profile / lane */}
        <label className="flex items-center gap-1 rounded-full px-2 py-0.5 ml-auto" style={pillStyle} title="Model profile / local lane">
          <select
            aria-label="Model profile"
            value={composerProfile}
            onChange={(e) => handleProfile(e.target.value as ComposerProfile)}
            className="bg-transparent outline-none cursor-pointer text-xs py-0.5"
            style={{ color: 'var(--color-text-secondary)' }}
          >
            <option value="coder">{PROFILE_LABELS.coder}</option>
            <option value="fast">{PROFILE_LABELS.fast}</option>
            <option value="quality">{PROFILE_LABELS.quality}</option>
            <option value="remote">{PROFILE_LABELS.remote}</option>
          </select>
        </label>
      </div>
    </div>
  );
}

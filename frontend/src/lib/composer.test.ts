import { describe, it, expect } from 'vitest';
import { buildComposerSystemMessage, PROFILE_MODEL, COMPOSER_SKILLS } from './composer';

describe('buildComposerSystemMessage', () => {
  it('returns empty string when nothing is active', () => {
    expect(buildComposerSystemMessage([], 'default', [])).toBe('');
  });

  it('includes selected skills with their descriptions', () => {
    const out = buildComposerSystemMessage(['superpowers'], 'default', []);
    expect(out).toContain('[Active skills]');
    expect(out).toContain('Superpowers');
  });

  it('ignores unknown skill ids', () => {
    expect(buildComposerSystemMessage(['does-not-exist'], 'default', [])).toBe('');
  });

  it('emits a read-only directive', () => {
    const out = buildComposerSystemMessage([], 'readonly', []);
    expect(out).toContain('READ-ONLY');
    expect(out).not.toContain('FULL AUTO');
  });

  it('emits a full-auto directive', () => {
    expect(buildComposerSystemMessage([], 'auto', [])).toContain('FULL AUTO');
  });

  it('numbers attached context items', () => {
    const out = buildComposerSystemMessage([], 'default', ['note A', 'note B']);
    expect(out).toContain('[Attached context]');
    expect(out).toContain('1. note A');
    expect(out).toContain('2. note B');
  });

  it('combines skills, permission, and context in order', () => {
    const out = buildComposerSystemMessage(['browser-qa'], 'auto', ['ctx']);
    expect(out.indexOf('[Active skills]')).toBeLessThan(out.indexOf('FULL AUTO'));
    expect(out.indexOf('FULL AUTO')).toBeLessThan(out.indexOf('[Attached context]'));
  });
});

describe('profile/model map', () => {
  it('maps every profile to a model alias', () => {
    expect(PROFILE_MODEL.coder).toBe('qwen3.6-27b-local');
    expect(PROFILE_MODEL.remote).toBe('qwen3.6-35b-a3b-remote');
    expect(PROFILE_MODEL.quality).toBe('qwen3.6-27b-quality');
  });

  it('ships a non-empty skills catalog with unique ids', () => {
    const ids = COMPOSER_SKILLS.map((s) => s.id);
    expect(ids.length).toBeGreaterThan(0);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

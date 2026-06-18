import { describe, it, expect } from 'vitest';
import { parseSlashCommand } from './slashCommands';

describe('parseSlashCommand', () => {
  it('returns null for normal messages', () => {
    expect(parseSlashCommand('hello world')).toBeNull();
    expect(parseSlashCommand('what is /etc/hosts')).toBeNull();
  });

  it('parses /clear and /help', () => {
    expect(parseSlashCommand('/clear')).toEqual({ cmd: 'clear' });
    expect(parseSlashCommand('  /help ')).toEqual({ cmd: 'help' });
  });

  it('parses /model with an argument', () => {
    expect(parseSlashCommand('/model gpt-4o')).toEqual({ cmd: 'model', arg: 'gpt-4o' });
    expect(parseSlashCommand('/model')).toEqual({ cmd: 'model', arg: '' });
  });

  it('is case-insensitive on the command word', () => {
    expect(parseSlashCommand('/CLEAR')).toEqual({ cmd: 'clear' });
  });

  it('returns null for unknown slash commands', () => {
    expect(parseSlashCommand('/frobnicate now')).toBeNull();
  });
});

export type SlashCommand =
  | { cmd: 'clear' }
  | { cmd: 'model'; arg: string }
  | { cmd: 'help' };

export const SLASH_HELP: ReadonlyArray<{ name: string; desc: string }> = [
  { name: '/clear', desc: 'Start a new chat' },
  { name: '/model <id>', desc: 'Switch the active model' },
  { name: '/help', desc: 'Show available commands' },
];

/**
 * Parse a composer input into a slash command, or null if it is a normal
 * message. Pure — the caller performs the side effects.
 */
export function parseSlashCommand(text: string): SlashCommand | null {
  const t = text.trim();
  if (!t.startsWith('/')) return null;
  const parts = t.slice(1).split(/\s+/);
  const word = (parts[0] || '').toLowerCase();
  const arg = parts.slice(1).join(' ').trim();
  switch (word) {
    case 'clear':
      return { cmd: 'clear' };
    case 'model':
      return { cmd: 'model', arg };
    case 'help':
      return { cmd: 'help' };
    default:
      return null;
  }
}

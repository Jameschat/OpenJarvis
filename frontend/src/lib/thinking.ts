const OPEN = '<think>';
const CLOSE = '</think>';

/**
 * Split assistant content into reasoning (<think>…</think>) and the visible
 * answer. Handles streaming partials (open tag, no close yet) and a stray
 * closing tag with no opener (some models emit only </think>).
 */
export function splitThinking(content: string): { thinking: string; visible: string } {
  const openIdx = content.indexOf(OPEN);
  if (openIdx === -1) {
    const strayClose = content.indexOf(CLOSE);
    if (strayClose !== -1) {
      return {
        thinking: content.slice(0, strayClose).trim(),
        visible: content.slice(strayClose + CLOSE.length).trim(),
      };
    }
    return { thinking: '', visible: content };
  }
  const closeIdx = content.indexOf(CLOSE, openIdx);
  if (closeIdx === -1) {
    return {
      thinking: content.slice(openIdx + OPEN.length).trim(),
      visible: content.slice(0, openIdx).trim(),
    };
  }
  const thinking = content.slice(openIdx + OPEN.length, closeIdx).trim();
  const visible = (content.slice(0, openIdx) + content.slice(closeIdx + CLOSE.length)).trim();
  return { thinking, visible };
}

import { describe, it, expect } from 'vitest';
import { splitThinking } from './thinking';

describe('splitThinking', () => {
  it('returns content as visible when there is no think tag', () => {
    expect(splitThinking('hello world')).toEqual({ thinking: '', visible: 'hello world' });
  });

  it('separates a complete think block from the answer', () => {
    expect(splitThinking('<think>reason here</think>The answer')).toEqual({
      thinking: 'reason here',
      visible: 'The answer',
    });
  });

  it('treats an unclosed think tag as in-progress reasoning (streaming)', () => {
    expect(splitThinking('<think>still reasoning')).toEqual({
      thinking: 'still reasoning',
      visible: '',
    });
  });

  it('handles a stray closing tag with no opener', () => {
    expect(splitThinking('leading reasoning</think>final answer')).toEqual({
      thinking: 'leading reasoning',
      visible: 'final answer',
    });
  });

  it('keeps text before an opening think tag as visible', () => {
    expect(splitThinking('prefix <think>r</think> suffix')).toEqual({
      thinking: 'r',
      visible: 'prefix  suffix'.trim(),
    });
  });
});

import { useState, useMemo, memo } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import 'katex/dist/katex.min.css';
import { Copy, Check } from 'lucide-react';
import { AudioPlayer } from './AudioPlayer';
import { ToolCallCard } from './ToolCallCard';
import { XRayFooter } from './XRayFooter';
import type { ChatMessage } from '../../types';
import { splitThinking } from '../../lib/thinking';
import { ThinkingBlock } from './ThinkingBlock';
import { PlanChecklist } from './PlanChecklist';
import { DiffBlock } from './DiffBlock';

interface Props {
  message: ChatMessage;
  // While true (the actively-streaming assistant message), render cheap plain
  // text instead of the full markdown+highlight+katex pipeline — re-parsing the
  // growing message every 80ms flush otherwise saturates the main thread and
  // makes the whole UI (and the working indicator) janky.
  streaming?: boolean;
}

function getTextContent(node: any): string {
  if (typeof node === 'string' || typeof node === 'number') {
    return String(node);
  }
  if (Array.isArray(node)) {
    return node.map(getTextContent).join('');
  }
  if (node?.props?.children) {
    return getTextContent(node.props.children);
  }
  return '';
}

function CodeBlockPre({ children, ...props }: any) {
  const [copied, setCopied] = useState(false);
  const codeElement = Array.isArray(children) ? children[0] : children;
  const className = codeElement?.props?.className || '';
  const match = /language-([\w-]+)/.exec(className);
  const lang = match ? match[1] : '';
  const code = getTextContent(codeElement?.props?.children).replace(/\n$/, '');

  const handleCopy = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div
      className="code-block-wrapper relative my-3"
      style={{ borderRadius: 'var(--radius-md)', overflow: 'hidden' }}
    >
      <div
        className="flex items-center justify-between px-4 py-1.5 text-xs"
        style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text-tertiary)' }}
      >
        <span className="font-mono">{lang || 'code'}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 px-2 py-0.5 rounded transition-colors cursor-pointer"
          style={{ color: 'var(--color-text-tertiary)' }}
          onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--color-text-secondary)')}
          onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--color-text-tertiary)')}
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre {...props} style={{ margin: 0, borderRadius: 0 }}>
        {children}
      </pre>
    </div>
  );
}

function CopyMessageButton({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <button
      onClick={handleCopy}
      className="p-1 rounded opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer"
      style={{ color: 'var(--color-text-tertiary)' }}
      title="Copy message"
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
    </button>
  );
}

// Memoised: during streaming the whole message list re-renders on every token;
// without memo, every PRIOR assistant message re-runs its markdown+highlight
// pipeline each token and freezes the main thread. memo skips bubbles whose
// props (message ref / streaming flag) haven't changed.
function MessageBubbleImpl({ message, streaming = false }: Props) {
  const isUser = message.role === 'user';

  if (isUser) {
    return (
      <div className="flex justify-end mb-4">
        <div
          className="max-w-[85%] px-4 py-2.5 text-sm leading-relaxed"
          style={{
            background: 'var(--color-user-bubble)',
            color: 'var(--color-user-bubble-text)',
            borderRadius: 'var(--radius-xl) var(--radius-xl) var(--radius-sm) var(--radius-xl)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {message.content}
        </div>
      </div>
    );
  }

  const { thinking, visible } = useMemo(() => splitThinking(message.content), [message.content]);
  // While streaming, the answer hasn't started until visible content appears.
  const thinkingActive = streaming && !visible;

  return (
    <div className="group mb-6">
      {/* Plan checklist */}
      {message.plan && message.plan.length > 0 && <PlanChecklist items={message.plan} />}

      {/* Reasoning trace */}
      {thinking && <ThinkingBlock thinking={thinking} active={thinkingActive} />}

      {/* File edits (diffs) */}
      {message.activity
        ?.filter((a) => a.kind === 'file_edit')
        .map((a, i) =>
          a.kind === 'file_edit' ? (
            <DiffBlock key={a.editId || i} path={a.path} diff={a.diff} added={a.added} removed={a.removed} />
          ) : null,
        )}

      {/* Escalations */}
      {message.activity
        ?.filter((a) => a.kind === 'escalation')
        .map((a, i) =>
          a.kind === 'escalation' ? (
            <div
              key={i}
              className="mb-2 flex items-center gap-1.5 text-xs"
              style={{ color: 'var(--color-text-tertiary)' }}
            >
              <span style={{ color: 'var(--color-accent)' }}>⤴</span>
              <span>
                Escalated {a.from ? `from ${a.from} ` : ''}to <strong>{a.to}</strong>
                {a.reason ? ` — ${a.reason}` : ''}
              </span>
            </div>
          ) : null,
        )}

      {/* Tool calls */}
      {message.toolCalls && message.toolCalls.length > 0 && (
        <div className="mb-3 flex flex-col gap-2">
          {message.toolCalls.map((tc) => (
            <ToolCallCard key={tc.id} toolCall={tc} />
          ))}
        </div>
      )}

      {/* Audio player (e.g. morning digest) */}
      {message.audio?.url && <AudioPlayer src={message.audio.url} />}

      {/* Assistant message — plain text while streaming, full markdown once done */}
      {visible && (
        streaming ? (
          <div
            className="text-sm leading-relaxed"
            style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: 'var(--color-text)' }}
          >
            {visible}
          </div>
        ) : (
          <div className="prose max-w-none">
            <ReactMarkdown
              remarkPlugins={[remarkGfm, remarkMath]}
              rehypePlugins={[[rehypeHighlight, { detect: true }], rehypeKatex]}
              components={{
                pre: CodeBlockPre,
              }}
            >
              {visible}
            </ReactMarkdown>
          </div>
        )
      )}

      {/* Footer: copy + x-ray */}
      <div className="flex items-center gap-2 mt-1.5">
        <CopyMessageButton content={visible} />
      </div>
      <XRayFooter usage={message.usage} telemetry={message.telemetry} />
    </div>
  );
}

export const MessageBubble = memo(MessageBubbleImpl);

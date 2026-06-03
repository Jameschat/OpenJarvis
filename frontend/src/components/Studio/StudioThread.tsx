import { useEffect, useMemo, useRef } from 'react';
import type { StudioMessage, StudioRun } from './types';

interface StudioThreadProps {
  messages: StudioMessage[];
  activeRun?: StudioRun;
}

function formatTime(value?: string): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function StudioThread({ messages, activeRun }: StudioThreadProps) {
  const listRef = useRef<HTMLDivElement>(null);
  const visibleMessages = useMemo(() => messages || [], [messages]);

  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [visibleMessages, activeRun?.status]);

  return (
    <section className="studio-thread" ref={listRef}>
      {visibleMessages.length === 0 && !activeRun ? (
        <div className="studio-empty-state">
          <h2>Jarvis Studio</h2>
          <p>Plan projects, build websites, run tools, and keep the work tied to memory.</p>
        </div>
      ) : (
        <>
          {visibleMessages.map((message, index) => {
            const isOperator = message.role === 'operator' || message.role === 'user';
            return (
              <article
                className={`studio-message ${isOperator ? 'operator' : 'jarvis'}`}
                key={message.id || `${message.role}-${index}`}
              >
                <div className="studio-message-body">{message.content}</div>
                <time>{formatTime(message.created_at)}</time>
              </article>
            );
          })}
          {activeRun && (
            <article className="studio-message jarvis thinking">
              <div className="studio-message-body">
                Jarvis is thinking
                <span className="studio-thinking-dots" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </span>
              </div>
              <time>{formatTime(activeRun.created_at)}</time>
            </article>
          )}
        </>
      )}
    </section>
  );
}

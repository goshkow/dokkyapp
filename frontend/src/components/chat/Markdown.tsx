import { useLayoutEffect, useRef } from 'react';
import type { Message } from '../../lib/journal';
import { renderMarkdown } from '../../lib/markdown';

interface Props {
  text: string;
  className?: string;
  highlight?: boolean;
  message?: Message;
}

/** Готовая разметка Markdown в своём узле; перерисовывается только при смене текста. */
export function Markdown({ text, className = 'message-body', highlight = true, message }: Props) {
  const node = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (node.current) renderMarkdown(node.current, text, highlight, { message });
  }, [text, highlight, message]);
  return <div ref={node} className={className} />;
}

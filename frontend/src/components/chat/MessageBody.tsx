import { useLayoutEffect, useRef } from 'react';
import type { Message } from '../../lib/journal';
import { renderMarkdown } from '../../lib/markdown';
import {
  cancelSettle, reconcileStreamNodes, renderStream, setShownLength, settleStream, waitStreamAnimations,
} from '../../lib/streamMarkdown';
import { acquireBody, bufferContent, dropBody, onStream } from '../../state/stream';

interface BodyNode extends HTMLDivElement {
  _mode?: 'stream' | 'static';
  _content?: string;
  _decorated?: string;
}

interface Props {
  /** Номер сообщения или ключ строки журнала: по нему узел переезжает между лентой и журналом. */
  id: string;
  content: string;
  running: boolean;
  message?: Message;
  /** Подсветка кода только в готовых ответах; комментарии журнала идут без неё. */
  highlight?: boolean;
  variant?: 'message' | 'commentary';
  /** Готовый текст показан (поток дописан): можно показывать кнопки и вложения. */
  onSettled?: (settled: boolean) => void;
  /** Доработка готового текста (чипы источников); `decorateKey` перезапускает её при смене условий. */
  decorate?: (node: HTMLElement) => void;
  decorateKey?: string;
}

/** Тело ответа. Узел с текстом живёт в реестре и переезжает между лентой и журналом: напечатанное не пересоздаётся. */
export function AssistantBody({ id, content, running, message, highlight = true, variant = 'message', onSettled, decorate, decorateKey = '' }: Props) {
  const slot = useRef<HTMLDivElement>(null);
  const runningRef = useRef(running);
  const token = useRef(0);
  const report = useRef(onSettled);
  const source = useRef(message);
  const finish = useRef(decorate);
  report.current = onSettled;
  source.current = message;
  finish.current = decorate;
  runningRef.current = running;

  // узел из реестра встаёт на место; уходит со страницы, но остаётся жить, пока идёт ответ
  useLayoutEffect(() => {
    const node = acquireBody(id) as BodyNode;
    slot.current?.appendChild(node);
    return () => {
      node.remove();
      if (!runningRef.current) dropBody(id);
    };
  }, [id]);

  useLayoutEffect(() => {
    const node = acquireBody(id) as BodyNode;
    node.classList.toggle('journal-commentary', variant === 'commentary');
    const mine = ++token.current;
    if (running) {
      cancelSettle(node);
      report.current?.(false);
      const buffered = bufferContent(id);
      if (node._mode !== 'stream') {
        node._mode = 'stream';
        // уже полученный сервером текст показывается сразу, дальше идёт плавная печать
        if (content && node.childNodes.length === 0) {
          renderMarkdown(node, content, false, { message: source.current, streaming: true });
          setShownLength(node, content.length);
        }
      }
      renderStream(node, buffered !== undefined && buffered.length >= content.length ? buffered : content);
      return onStream(id, (text) => renderStream(node, text));
    }
    if (node._mode === 'stream') {
      // ответ завершён: дописать то, что печатается, и заменить итоговой разметкой в том же узле
      settleStream(node, async () => {
        await waitStreamAnimations(node);
        if (token.current !== mine || !node.isConnected) return;
        const draft = document.createElement('div');
        renderMarkdown(draft, content, highlight, { message: source.current });
        reconcileStreamNodes(node, draft);
        node._mode = 'static';
        node._content = content;
        node._decorated = decorateKey;
        finish.current?.(node);
        report.current?.(true);
      });
      return () => cancelSettle(node);
    }
    if (node._content !== content || node._mode !== 'static' || node._decorated !== decorateKey) {
      renderMarkdown(node, content, highlight, { message: source.current });
      node._mode = 'static';
      node._content = content;
      node._decorated = decorateKey;
      setShownLength(node, content.length);
      finish.current?.(node);
    }
    report.current?.(true);
    return undefined;
  }, [id, content, running, highlight, variant, decorateKey]);

  return <div className="message-body-slot" ref={slot} />;
}

/** Сообщение пользователя: обычный текст без разметки. */
export function UserBody({ content }: { content: string }) {
  return <div className="message-body selectable">{content}</div>;
}


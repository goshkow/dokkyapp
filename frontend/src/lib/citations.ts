/* Ссылки на источники прямо в тексте ответа Докки: после утверждения ставится чип с сайтом источника. */
import type { Message } from './journal';
import { renderMarkdown } from './markdown';
import { safeSource, sourceDomain, sourceLetter, type CloudSource } from './cloud';

export interface ChipHandlers {
  open(sources: CloudSource[], anchor: HTMLElement): void;
}

function icon(source: CloudSource): HTMLSpanElement {
  const node = document.createElement('span');
  node.className = 'ai-source-icon';
  node.textContent = sourceLetter(source);
  node.setAttribute('aria-hidden', 'true');
  return node;
}

function link(source: CloudSource, className: string): HTMLAnchorElement | null {
  const safe = safeSource(source);
  if (!safe) return null;
  const node = document.createElement('a');
  node.className = className;
  node.href = safe.href;
  node.target = '_blank';
  node.rel = 'noopener noreferrer';
  if (window.koddiDesktop) {
    node.onclick = (event) => {
      event.preventDefault();
      window.location.href = `koddi://open-url?url=${encodeURIComponent(safe.href)}`;
    };
  }
  return node;
}

function chip(sources: CloudSource[], handlers: ChipHandlers): HTMLElement | null {
  const first = sources[0];
  if (!first) return null;
  const node: HTMLElement | null = sources.length === 1 ? link(first, 'ai-source-chip') : document.createElement('button');
  if (!node) return null;
  node.className = 'ai-source-chip';
  node.setAttribute('aria-label', `Источники: ${sources.map((source) => sourceDomain(source)).join(', ')}`);
  if (node instanceof HTMLButtonElement) {
    node.type = 'button';
    node.onclick = () => handlers.open(sources, node);
  }
  const label = document.createElement('span');
  label.textContent = sourceDomain(first);
  node.append(icon(first), label);
  if (sources.length > 1) {
    const count = document.createElement('span');
    count.className = 'ai-citation-count';
    count.textContent = `+${sources.length - 1}`;
    node.append(count);
  }
  return node;
}

/** Вставляет чипы источников после утверждений, на которые они ссылаются. */
export function attachCitations(body: HTMLElement, message: Message, handlers: ChipHandlers): void {
  const citations = (message.citations ?? []) as CloudSource[];
  if (!citations.length) return;
  const groups = new Map<string, { end: number; sources: CloudSource[] }>();
  const source = String(message.content || '');
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  const texts: Array<{ node: Text; start: number; end: number }> = [];
  let text = '';
  while (walker.nextNode()) {
    const node = walker.currentNode as Text;
    if (node.parentElement?.closest('pre,code,button,a,.ai-source-chip')) continue;
    texts.push({ node, start: text.length, end: text.length + node.length });
    text += node.textContent ?? '';
  }
  for (const citation of citations) {
    if (!safeSource(citation)) continue;
    for (const span of citation.spans ?? []) {
      if (!Number.isInteger(span.start) || !Number.isInteger(span.end) || span.start < 0 || span.end <= span.start || span.end > source.length) continue;
      const fragment = document.createElement('div');
      renderMarkdown(fragment, source.slice(span.start, span.end), false);
      if (fragment.querySelector('pre,code,table')) continue;
      const quote = (fragment.textContent ?? '').trim();
      const position = text.indexOf(quote);
      // цитата должна находиться в тексте ровно один раз, иначе чип встанет не туда
      if (quote.length < 3 || position < 0 || position !== text.lastIndexOf(quote)) continue;
      const end = position + quote.length;
      const key = String(end);
      const entry = groups.get(key) ?? { end, sources: [] };
      if (!entry.sources.some((item) => item.url === citation.url)) entry.sources.push(citation);
      groups.set(key, entry);
    }
  }
  for (const { end, sources } of [...groups.values()].sort((a, b) => b.end - a.end)) {
    const place = texts.find((item) => end > item.start && end <= item.end);
    if (!place) continue;
    const node = chip(sources, handlers);
    if (!node) continue;
    const range = document.createRange();
    range.setStart(place.node, end - place.start);
    range.collapse(true);
    range.insertNode(node);
  }
}

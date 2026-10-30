/* Сверка узлов и плавное появление слов, перенесено с mini_app/assets/app.js сайта Докки. */
import { renderMarkdown } from './markdown';

export interface StreamHooks {
  /** Вызывается после каждой отрисовки порции: лента следует за текстом. */
  follow?: () => void;
}
export const streamHooks: StreamHooks = {};

type StreamNode = Node & { _streamNew?: boolean };
interface StreamTarget extends HTMLElement {
  _shownLength?: number;
}

export function reconcileStreamNodes(oldParent: Node, newParent: Node, enter: (node: Node) => void = () => undefined): void {
  const equal = (old: Node, fresh: Node): boolean => {
    if (old.nodeType === 1 && fresh.nodeType === 1 && (old as Element).matches('.table-scroll') && (fresh as Element).matches('.table-scroll')) {
      return old.childNodes.length === fresh.childNodes.length && [...old.childNodes].every((node, index) => node.isEqualNode(fresh.childNodes[index] ?? null));
    }
    return old.isEqualNode(fresh);
  };
  const wanted = [...newParent.childNodes];
  wanted.forEach((fresh, index) => {
    let old: Node | undefined = oldParent.childNodes[index];
    // готовый блок сохраняет узел даже при вставке нового блока перед ним
    if (old && !equal(old, fresh)) {
      const exact = [...oldParent.childNodes].slice(index + 1).find((node) => equal(node, fresh));
      if (exact) {
        oldParent.insertBefore(exact, old);
        old = exact;
      }
    }
    if (!old) {
      oldParent.appendChild(fresh);
      enter(fresh);
      return;
    }
    if (equal(old, fresh)) return;
    if (wanted.slice(index + 1).some((node) => equal(old as Node, node))) {
      oldParent.insertBefore(fresh, old);
      enter(fresh);
      return;
    }
    if (old.nodeType !== fresh.nodeType || (old.nodeType === 1 && old.nodeName !== fresh.nodeName)) {
      (old as ChildNode).replaceWith(fresh);
      enter(fresh);
      return;
    }
    if (old.nodeType === 3) {
      if (old.nodeValue !== fresh.nodeValue) old.nodeValue = fresh.nodeValue;
      return;
    }
    const oldElement = old as HTMLElement;
    const freshElement = fresh as HTMLElement;
    if (oldElement.matches('.rich-video-card')) {
      if (oldElement.dataset.videoId === freshElement.dataset.videoId && oldElement.getAttribute('href') === freshElement.getAttribute('href')) return;
      oldElement.replaceWith(freshElement);
      enter(freshElement);
      return;
    }
    const sameRichImage = oldElement.hasAttribute('data-rich-image') && oldElement.getAttribute('data-rich-image') === freshElement.getAttribute('data-rich-image');
    for (const attr of [...oldElement.attributes]) {
      if (
        !freshElement.hasAttribute(attr.name) &&
        !(oldElement.matches('.table-scroll') && attr.name === 'style') &&
        !(sameRichImage && ['src', 'data-media-path', 'data-source-url'].includes(attr.name))
      ) oldElement.removeAttribute(attr.name);
    }
    for (const attr of [...freshElement.attributes]) {
      if (oldElement.getAttribute(attr.name) !== attr.value) oldElement.setAttribute(attr.name, attr.value);
    }
    reconcileStreamNodes(oldElement, freshElement, enter);
  });
  while (oldParent.childNodes.length > wanted.length) (oldParent.lastChild as ChildNode).remove();
}

interface Job {
  full: string;
  shown: number;
  last: number;
  notBefore: number;
  raf: number;
  text: string | null;
  painted?: string;
  drained?: (() => void) | null;
  deadline?: number;
}

const jobs = new WeakMap<HTMLElement, Job>();
const motionOn = () => document.documentElement.dataset.motion !== 'reduced' && !matchMedia('(prefers-reduced-motion: reduce)').matches;
const SKIP = 'table,a,pre,code,.code-head,.katex,svg,textarea';
const FRAMES: Keyframe[] = [
  { opacity: 0, filter: 'blur(3px)', transform: 'translateY(4px)', offset: 0, easing: 'cubic-bezier(.2,.6,.3,1)' },
  { opacity: 0.65, filter: 'blur(1.5px)', transform: 'translateY(.5px)', offset: 0.38, easing: 'cubic-bezier(.2,.6,.3,1)' },
  { opacity: 1, filter: 'blur(0px)', transform: 'translateY(-.65px)', offset: 0.7, easing: 'cubic-bezier(.4,0,.35,1)' },
  { opacity: 1, filter: 'blur(0px)', transform: 'translateY(0)', offset: 1 },
];

/** Каждое слово отдельным узлом: новые проявляются, старые не перерисовываются. */
function wrapWords(root: HTMLElement, fromOffset: number): void {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const texts: Text[] = [];
  while (walker.nextNode()) texts.push(walker.currentNode as Text);
  let offset = 0;
  for (const text of texts) {
    const start = offset;
    offset += text.length;
    if (text.parentElement?.closest(SKIP)) continue;
    const fragment = document.createDocumentFragment();
    let position = start;
    for (const part of text.nodeValue?.match(/\s+|\S+/gu) ?? []) {
      if (/^\s+$/u.test(part)) fragment.append(document.createTextNode(part));
      else {
        const word = document.createElement('span') as HTMLSpanElement & StreamNode;
        word.className = 'stream-word';
        word.textContent = part;
        word._streamNew = position >= fromOffset;
        fragment.append(word);
      }
      position += part.length;
    }
    text.replaceWith(fragment);
  }
}

function paint(target: HTMLElement, text: string, job: Job): void {
  const draft = document.createElement('div');
  renderMarkdown(draft, text, false, { streaming: true });
  const previous = job.text ?? target.textContent ?? '';
  const next = draft.textContent ?? '';
  let common = 0;
  while (common < previous.length && common < next.length && previous[common] === next[common]) common++;
  job.text = next;
  const motion = motionOn();
  wrapWords(draft, motion ? common : Infinity);
  const positions = [...target.querySelectorAll<HTMLElement>('pre,table,.table-scroll')].map((node) => ({ node, left: node.scrollLeft }));
  reconcileStreamNodes(target, draft, (node) => {
    if (!motion || node.nodeType !== 1) return;
    const element = node as HTMLElement;
    const words = [...(element.matches('.stream-word') ? [element] : []), ...element.querySelectorAll<HTMLElement>('.stream-word')];
    for (const word of words) {
      if (!(word as unknown as StreamNode)._streamNew) continue;
      word.animate(FRAMES, { duration: 520, fill: 'both' }).finished.then((animation) => animation.cancel()).catch(() => undefined);
    }
  });
  for (const { node, left } of positions) {
    if (node.isConnected && Math.abs(node.scrollLeft - left) > 0.5) node.scrollLeft = left;
  }
}

function pump(target: HTMLElement): void {
  const job = jobs.get(target);
  if (!job) return;
  job.raf = 0;
  if (!target.isConnected) return;
  const now = performance.now();
  if (now < job.notBefore) {
    job.raf = requestAnimationFrame(() => pump(target));
    return;
  }
  const remaining = job.full.length - job.shown;
  if (remaining > 0) {
    const frames = Math.max(1, Math.min(6, Math.round((now - job.last) / 16)));
    let shown = Math.min(job.full.length, job.shown + frames * Math.min(72, Math.max(12, Math.ceil(remaining / 18))));
    const edge = job.full.charCodeAt(shown - 1);
    if (edge >= 0xd800 && edge <= 0xdbff && shown < job.full.length) shown++;
    job.shown = shown;
    job.last = now;
  }
  const visible = job.full.slice(0, job.shown);
  // замена текста (replace, поправка смещения) тоже должна дойти до экрана
  if (visible !== job.painted) {
    job.painted = visible;
    paint(target, visible, job);
    // разбор разметки занимает не больше половины времени кадров
    job.notBefore = performance.now() + (performance.now() - now);
    streamHooks.follow?.();
  }
  if (job.shown < job.full.length) job.raf = requestAnimationFrame(() => pump(target));
  else job.drained?.();
}

/** Показывает растущий текст: порциями по кадрам, без повторного набора уже показанного. */
export function renderStream(target: HTMLElement, source: string): void {
  let job = jobs.get(target);
  if (!job) {
    job = { full: '', shown: 0, last: performance.now(), notBefore: 0, raf: 0, text: null };
    // уже показанный текст не сворачивается и не печатается заново
    if (target.firstChild) {
      job.shown = (target as StreamTarget)._shownLength ?? Infinity;
      job.text = target.textContent;
    }
    jobs.set(target, job);
  }
  job.full = String(source ?? '');
  if (job.shown === Infinity) job.shown = job.full.length;
  if (job.shown > job.full.length) job.shown = job.full.length;
  if (!job.raf) job.raf = requestAnimationFrame(() => pump(target));
}

export const setShownLength = (target: HTMLElement, length: number) => { (target as StreamTarget)._shownLength = length; };

/** Ждёт, пока показанный текст догонит принятый, и только потом завершает ответ. */
export function settleStream(target: HTMLElement, settle: () => void): void {
  const job = jobs.get(target);
  if (!job || job.shown >= job.full.length || !target.isConnected) {
    if (job?.raf) cancelAnimationFrame(job.raf);
    jobs.delete(target);
    settle();
    return;
  }
  clearTimeout(job.deadline);
  const run = () => {
    clearTimeout(job.deadline);
    job.drained = null;
    if (job.raf) cancelAnimationFrame(job.raf);
    jobs.delete(target);
    settle();
  };
  job.drained = run;
  // длинный итог, пришедший одним куском, успевает допечататься, но завершение не ждёт дольше 8 секунд
  job.deadline = window.setTimeout(run, Math.min(8000, 1800 + (job.full.length - job.shown) * 0.6));
}

export function cancelSettle(target: HTMLElement): void {
  const job = jobs.get(target);
  if (!job?.drained) return;
  clearTimeout(job.deadline);
  job.drained = null;
}

export function cancelStream(target: HTMLElement): void {
  const job = jobs.get(target);
  if (!job) return;
  if (job.raf) cancelAnimationFrame(job.raf);
  clearTimeout(job.deadline);
  jobs.delete(target);
}

export async function waitStreamAnimations(target: HTMLElement, limit = 700): Promise<void> {
  const animations = target.getAnimations({ subtree: true });
  if (!animations.length) return;
  await Promise.race([Promise.allSettled(animations.map((animation) => animation.finished)), new Promise((resolve) => setTimeout(resolve, limit))]);
}

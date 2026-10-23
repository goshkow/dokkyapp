/* Плавное перемещение элементов, когда меняется раскладка (открылась или закрылась панель).
   Раскладка пересчитывается один раз сразу в итоговое состояние, а элементы «едут» из прежнего положения через transform
   (Web Animations, их ведёт композитор): ничего не пересчитывается на каждом кадре и ничего не телепортируется. */

import { kickGlass } from './glass';

export type Anchor = 'left' | 'center' | 'right';
export type Snapshot = Map<string, number>;

/** Что должно ехать вместе с раскладкой и по какому краю считать сдвиг (у элемента, который не меняет размер, все края дают одно и то же). */
const TARGETS: ReadonlyArray<readonly [string, Anchor]> = [
  ['.sidebar-panel', 'left'],
  ['.inspector-panel', 'left'],
  ['.shell-controls-left', 'left'],
  ['#title', 'left'],
  ['.ai-product-switch', 'center'],
  ['#feed', 'center'],
  ['#composer', 'center'],
  ['.message-rail', 'right'],
  ['#ai-search-surface', 'center'],
];

const edge = (rect: DOMRect, anchor: Anchor): number => (anchor === 'left' ? rect.left : anchor === 'right' ? rect.right : rect.left + rect.width / 2);

/** Положение каждого элемента сейчас, вместе с идущими анимациями: так прерванное движение продолжается без скачка. */
export function snapshotLayout(): Snapshot {
  const taken: Snapshot = new Map();
  for (const [selector, anchor] of TARGETS) {
    const node = document.querySelector(selector);
    if (node) taken.set(selector, edge(node.getBoundingClientRect(), anchor));
  }
  return taken;
}

/** Сбрасывает идущие движения и проигрывает новые из прежнего положения в нынешнее. */
export function playFlip(before: Snapshot, duration: number, easing: string): void {
  const nodes = TARGETS.map(([selector, anchor]) => ({ selector, anchor, node: document.querySelector(selector) }));
  for (const { node } of nodes) for (const running of node?.getAnimations() ?? []) running.cancel();
  for (const { selector, anchor, node } of nodes) {
    const was = before.get(selector);
    if (!node || was === undefined) continue;
    const shift = was - edge(node.getBoundingClientRect(), anchor);
    if (Math.abs(shift) < 0.5) continue;
    node.animate([{ transform: `translateX(${shift}px)` }, { transform: 'translateX(0)' }], { duration, easing, composite: 'add' });
  }
  kickGlass();
}

/* вертикальные списки: пункты плавно переезжают, когда состав списка меняется сразу */
const outermost = (root: Element, selector: string): Element[] =>
  [...root.querySelectorAll(selector)].filter((node) => !node.parentElement?.closest(selector));

export function snapshotVertical(root: Element | null, selector: string): Map<Element, number> {
  const taken = new Map<Element, number>();
  if (!root) return taken;
  const base = root.getBoundingClientRect().top - (root instanceof HTMLElement ? root.scrollTop : 0);
  for (const node of outermost(root, selector)) taken.set(node, node.getBoundingClientRect().top - base);
  return taken;
}

export function playVertical(root: Element | null, selector: string, before: Map<Element, number>, duration: number, easing: string): void {
  if (!root) return;
  const nodes = outermost(root, selector);
  for (const node of nodes) for (const running of node.getAnimations()) running.cancel();
  const base = root.getBoundingClientRect().top - (root instanceof HTMLElement ? root.scrollTop : 0);
  for (const node of nodes) {
    const was = before.get(node);
    if (was === undefined) continue;
    const shift = was - (node.getBoundingClientRect().top - base);
    if (Math.abs(shift) < 1) continue;
    node.animate([{ transform: `translateY(${shift}px)` }, { transform: 'translateY(0)' }], { duration, easing, composite: 'add' });
  }
}

/* горизонтальный сдвиг соседей, когда меняется ширина текста рядом */
export function snapshotX(nodes: Iterable<Element>): Map<Element, number> {
  return new Map([...nodes].map((node) => [node, node.getBoundingClientRect().left]));
}

export function playX(before: Map<Element, number>, duration: number, easing: string): void {
  for (const [node, was] of before) {
    if (!node.isConnected) continue;
    for (const running of node.getAnimations()) running.cancel();
    const shift = was - node.getBoundingClientRect().left;
    if (Math.abs(shift) < 1) continue;
    node.animate([{ transform: `translateX(${shift}px)` }, { transform: 'translateX(0)' }], { duration, easing, composite: 'add' });
  }
}

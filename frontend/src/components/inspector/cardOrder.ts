/**
 * Порядок блоков правой панели: блоки переставляют перетаскиванием за заголовок или Alt+стрелками.
 * Порядок хранится в настройках, поэтому одинаков в главном виде и в плитке.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type PointerEvent } from 'react';
import { kickGlass } from '../../lib/glass';
import { useData } from '../../state/data';
import { guarded } from '../../state/overlay';
import { saveInspectorOrder } from '../../state/remote';
import { ui } from '../../state/ui';

export const CARD_ORDER = ['environment', 'context', 'diff', 'files', 'map', 'quality', 'browser', 'plan'] as const;
export type CardName = (typeof CARD_ORDER)[number];

const THRESHOLD = 5;
const EASE = 'cubic-bezier(0.2, 0, 0, 1)';

/** Сохранённый порядок без неизвестных и повторов; новые блоки встают на свои места по умолчанию. */
export function normalizeOrder(saved: readonly unknown[] | null | undefined): CardName[] {
  const known = new Set<string>(CARD_ORDER);
  const order: CardName[] = [];
  for (const item of saved ?? []) {
    if (typeof item === 'string' && known.has(item) && !order.includes(item as CardName)) order.push(item as CardName);
  }
  for (const [index, name] of CARD_ORDER.entries()) {
    if (order.includes(name)) continue;
    const before = CARD_ORDER.slice(0, index).reverse().find((item) => order.includes(item));
    order.splice(before ? order.indexOf(before) + 1 : 0, 0, name);
  }
  return order;
}

/**
 * Поставить блок на место slot среди видимых блоков. Скрытые (обзорные при открытом блоке)
 * остаются рядом со своими соседями, а не уезжают в конец.
 */
export function placeCard(order: readonly CardName[], name: CardName, slot: number, visible: (name: CardName) => boolean): CardName[] {
  const rest = order.filter((item) => item !== name);
  const shown = rest.filter(visible);
  const next = [...rest];
  if (!shown.length) return [...order];
  const clamped = Math.max(0, Math.min(slot, shown.length));
  const at = clamped < shown.length ? next.indexOf(shown[clamped] as CardName) : next.indexOf(shown[shown.length - 1] as CardName) + 1;
  next.splice(at, 0, name);
  return next;
}

const same = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((item, index) => item === b[index]);

interface Drag { name: CardName; pointer: number; startY: number; startTop: number; grab: number; moved: boolean; before: CardName[] }

/** Перетаскивание блоков с FLIP-анимацией: соседи едут через element.animate, стекло под ними следует сдвигу. */
export function useCardOrder(hidden: (name: CardName) => boolean) {
  const saved = useData((s) => s.settings.inspector_order);
  const stored = normalizeOrder(saved);
  const [local, setLocal] = useState<CardName[] | null>(null);
  const order = local ?? stored;
  const box = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const tops = useRef<Map<string, number> | null>(null);
  const refocus = useRef<CardName | null>(null);
  const droppedAt = useRef(0);
  const escape = useRef<((event: globalThis.KeyboardEvent) => void) | null>(null);
  const [dragging, setDragging] = useState<CardName | null>(null);

  // сохранённый порядок догнал локальный: дальше снова показываем настройки
  const storedKey = stored.join();
  useEffect(() => {
    if (local && !drag.current && same(local, stored)) setLocal(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storedKey, local, dragging]);

  const cards = () => [...(box.current?.querySelectorAll<HTMLElement>(':scope > .inspector-card') ?? [])];
  const cardOf = (name: string) => cards().find((node) => node.dataset.card === name);
  const reduced = () => ui.get().motion === 'reduced';

  /** Запомнить видимое положение блоков (со сдвигом идущих анимаций) до перестановки, чтобы после неё доиграть движение. */
  const remember = () => {
    const top = box.current?.getBoundingClientRect().top ?? 0;
    tops.current = new Map(cards().map((node) => [node.dataset.card ?? '', node.getBoundingClientRect().top - top]));
  };

  const follow = () => {
    const current = drag.current;
    const node = current && cardOf(current.name);
    if (!current || !node) return;
    node.style.transform = `translateY(${current.pointer - current.startY - (node.offsetTop - current.startTop)}px)`;
  };

  useLayoutEffect(() => {
    const before = tops.current;
    tops.current = null;
    if (before) {
      for (const node of cards()) {
        const name = node.dataset.card ?? '';
        if (name === drag.current?.name) continue;
        const shift = (before.get(name) ?? node.offsetTop) - node.offsetTop;
        if (!shift || reduced()) continue;
        node.animate([{ transform: `translateY(${shift}px)` }, { transform: 'translateY(0px)' }], { duration: 220, easing: EASE });
      }
      follow();
      kickGlass();
    }
    if (refocus.current) {
      cardOf(refocus.current)?.querySelector<HTMLElement>('.card-head')?.focus();
      refocus.current = null;
    }
  });

  const commit = useCallback((next: CardName[], previous: CardName[]) => {
    if (same(next, previous)) return;
    setLocal(next);
    void guarded(async () => {
      try {
        await saveInspectorOrder(next);
      } catch (error) {
        remember();
        setLocal(null);
        throw error;
      }
    })();
  }, []);

  const finish = (cancel: boolean) => {
    const current = drag.current;
    if (!current) return;
    drag.current = null;
    if (escape.current) window.removeEventListener('keydown', escape.current, true);
    escape.current = null;
    setDragging(null);
    const node = cardOf(current.name);
    if (!current.moved) return;
    droppedAt.current = performance.now();
    if (cancel) {
      // блок едет на старое место из-под курсора вместе с соседями
      remember();
      if (node) node.style.transform = '';
      setLocal(current.before);
    } else {
      if (node) {
        const offset = node.getBoundingClientRect().top;
        node.style.transform = '';
        const shift = offset - node.getBoundingClientRect().top;
        if (shift && !reduced()) node.animate([{ transform: `translateY(${shift}px)` }, { transform: 'translateY(0px)' }], { duration: 200, easing: EASE });
      }
      commit(order, current.before);
    }
    kickGlass();
  };

  const latest = useRef(finish);
  latest.current = finish;

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || drag.current) return;
    const head = (event.target as Element).closest('.card-head');
    const node = head?.closest<HTMLElement>('.inspector-card');
    const name = node?.dataset.card as CardName | undefined;
    if (!head || !node || !name || hidden(name)) return;
    drag.current = { name, pointer: event.clientY, startY: event.clientY, startTop: node.offsetTop, grab: event.clientY - node.getBoundingClientRect().top, moved: false, before: order };
  };

  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const current = drag.current;
    if (!current) return;
    current.pointer = event.clientY;
    if (!current.moved) {
      if (Math.abs(event.clientY - current.startY) < THRESHOLD) return;
      current.moved = true;
      box.current?.setPointerCapture(event.pointerId);
      escape.current = (key) => {
        if (key.key !== 'Escape') return;
        key.preventDefault();
        key.stopPropagation();
        latest.current(true);
      };
      window.addEventListener('keydown', escape.current, true);
      setDragging(current.name);
    }
    const node = cardOf(current.name);
    if (!node || !box.current) return;
    const center = event.clientY - box.current.getBoundingClientRect().top + box.current.scrollTop - current.grab + node.offsetHeight / 2;
    const others = cards().filter((item) => item !== node && !hidden(item.dataset.card as CardName));
    const slot = others.filter((item) => item.offsetTop + item.offsetHeight / 2 < center).length;
    const next = placeCard(order, current.name, slot, (name) => !hidden(name));
    if (!same(next, order)) {
      remember();
      setLocal(next);
    } else follow();
    kickGlass();
  };

  const onPointerUp = () => finish(false);

  /** Клик, которым закончилось перетаскивание, не должен раскрывать блок. */
  const onClickCapture = (event: MouseEvent<HTMLDivElement>) => {
    // click приходит сразу за pointerup, но не обязательно в той же задаче
    if (performance.now() - droppedAt.current > 400) return;
    droppedAt.current = 0;
    event.preventDefault();
    event.stopPropagation();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!event.altKey || event.metaKey || event.ctrlKey || event.shiftKey || (event.key !== 'ArrowUp' && event.key !== 'ArrowDown')) return;
    const name = (event.target as Element).closest('.card-head')?.closest<HTMLElement>('.inspector-card')?.dataset.card as CardName | undefined;
    if (!name || drag.current) return;
    event.preventDefault();
    const shown = order.filter((item) => item !== name && !hidden(item));
    const slot = order.filter((item) => !hidden(item)).indexOf(name) + (event.key === 'ArrowUp' ? -1 : 1);
    if (slot < 0 || slot > shown.length) return;
    remember();
    refocus.current = name;
    commit(placeCard(order, name, slot, (item) => !hidden(item)), order);
  };

  return {
    order,
    dragging,
    box,
    handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel: () => finish(true), onClickCapture, onKeyDown },
  };
}

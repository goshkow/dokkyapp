import { useEffect, useRef, type KeyboardEvent, type PointerEvent } from 'react';
import { announcePanels } from '../../lib/bridge';

type Side = 'left' | 'right';

const STEP = 16;
// полоса не загорается, когда курсор просто проходит мимо ручки
const HOT_DELAY_MS = 160;
// плитки стекла - отдельные окна поверх страницы: уход курсора на них страница может не заметить, и полоса осталась бы гореть
const HOT_IDLE_MS = 1400;
// колонка чата не уже этого: панели не съедают переписку
const MIN_MAIN = 420;
const SIDES = {
  left: { key: 'dokky.sidebar-width', name: '--lw-open', min: 244, max: 460, label: 'Ширина левого меню', other: '--rw-open', otherClosed: 'right-collapsed', closed: 'left-collapsed' },
  right: { key: 'dokky.inspector-width', name: '--rw-open', min: 300, max: 900, label: 'Ширина правой панели', other: '--lw-open', otherClosed: 'left-collapsed', closed: 'right-collapsed' },
} as const;

const shell = () => document.getElementById('shell');
const px = (name: string): number => parseFloat(getComputedStyle(shell() as HTMLElement).getPropertyValue(name)) || 0;

/** Сколько можно отдать панели при текущей ширине окна и соседней панели. */
export function clampWidth(width: number, windowWidth: number, other: number, side: Side = 'right'): number {
  const { min, max } = SIDES[side];
  const room = Math.max(min, Math.min(max, windowWidth - other - MIN_MAIN));
  return Math.round(Math.min(room, Math.max(min, width)));
}

function saved(side: Side): number | null {
  try {
    const value = Number(localStorage.getItem(SIDES[side].key));
    return Number.isFinite(value) && value >= SIDES[side].min ? value : null;
  } catch {
    return null;
  }
}

/** Ширина панели: переменная сетки и, на macOS со стеклом, нативная плитка панели. */
function apply(side: Side, width: number | null, remember: boolean): void {
  const node = shell();
  if (!node) return;
  const config = SIDES[side];
  const before = node.style.getPropertyValue(config.name);
  if (width == null) node.style.removeProperty(config.name);
  else node.style.setProperty(config.name, `${clampWidth(width, window.innerWidth, node.classList.contains(config.otherClosed) ? 0 : px(config.other), side)}px`);
  // пока панель едет, подгонка без изменений не должна дёргать нативные плитки
  if (node.style.getPropertyValue(config.name) !== before || remember) {
    const left = !node.classList.contains('left-collapsed');
    const right = !node.classList.contains('right-collapsed');
    // что на самом деле показано, говорит сетка: в режиме чата правой панели нет, даже если она помнит ширину
    if (!node.classList.contains(config.closed)) announcePanels({ left, right, lw: px('--lw-open'), rw: px('--rw-open'), animate: false, ms: 0, c: [0.22, 1, 0.36, 1] });
  }
  if (!remember) return;
  try {
    if (width == null) localStorage.removeItem(config.key);
    else localStorage.setItem(config.key, String(px(config.name)));
  } catch {
    /* без сохранения */
  }
}

/** Ручка на границе панели с чатом: тянуть мышью, стрелки с клавиатуры, двойной щелчок - ширина по умолчанию. */
export function PanelResizer({ side = 'right' }: { side?: Side }) {
  const drag = useRef<{ id: number } | null>(null);
  const handle = useRef<HTMLDivElement>(null);
  const hotTimer = useRef(0);
  const config = SIDES[side];
  const setHot = (on: boolean) => handle.current?.toggleAttribute('data-hot', on);
  const cool = () => { window.clearTimeout(hotTimer.current); if (!drag.current) setHot(false); };
  const warm = (delay: number) => {
    window.clearTimeout(hotTimer.current);
    hotTimer.current = window.setTimeout(() => {
      setHot(true);
      hotTimer.current = window.setTimeout(cool, HOT_IDLE_MS);
    }, delay);
  };
  const keepHot = () => {
    if (!handle.current?.hasAttribute('data-hot')) return;
    window.clearTimeout(hotTimer.current);
    hotTimer.current = window.setTimeout(cool, HOT_IDLE_MS);
  };
  useEffect(() => () => window.clearTimeout(hotTimer.current), []);

  // сохранённая ширина при запуске и подгонка, когда сужается окно или открывается соседняя панель
  useEffect(() => {
    const restore = () => { const width = saved(side) ?? (shell()?.style.getPropertyValue(config.name) ? px(config.name) : null); if (width != null) apply(side, width, false); };
    restore();
    window.addEventListener('resize', restore);
    const node = shell();
    const classes = node ? new MutationObserver(restore) : null;
    if (node) classes?.observe(node, { attributes: true, attributeFilter: ['class'] });
    return () => { window.removeEventListener('resize', restore); classes?.disconnect(); };
  }, [side, config.name]);

  const move = (clientX: number) => {
    const box = shell()?.getBoundingClientRect();
    if (box) apply(side, side === 'left' ? clientX - box.left : box.right - clientX, false);
  };
  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    drag.current = { id: event.pointerId };
    window.clearTimeout(hotTimer.current);
    setHot(true);
    event.currentTarget.setPointerCapture(event.pointerId);
    document.documentElement.classList.add('panel-resizing');
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.id === event.pointerId) move(event.clientX);
    else keepHot();
  };
  const finish = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.id !== event.pointerId) return;
    drag.current = null;
    setHot(false);
    document.documentElement.classList.remove('panel-resizing');
    apply(side, px(config.name), true);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const wider = side === 'left' ? 'ArrowRight' : 'ArrowLeft';
    const narrower = side === 'left' ? 'ArrowLeft' : 'ArrowRight';
    const delta = event.key === wider ? STEP : event.key === narrower ? -STEP : 0;
    if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      apply(side, event.key === 'Home' ? 9999 : config.min, true);
    } else if (delta) {
      event.preventDefault();
      apply(side, px(config.name) + delta, true);
    }
  };
  return (
    <div
      ref={handle}
      className="panel-resizer"
      data-side={side}
      role="separator"
      aria-orientation="vertical"
      aria-label={config.label}
      aria-valuemin={config.min}
      tabIndex={0}
      onPointerEnter={() => { if (!drag.current) warm(HOT_DELAY_MS); }}
      onPointerLeave={cool}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={finish}
      onPointerCancel={finish}
      onLostPointerCapture={finish}
      onDoubleClick={() => apply(side, null, true)}
      onKeyDown={onKeyDown}
    />
  );
}

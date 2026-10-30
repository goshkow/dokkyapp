import { useEffect, useRef, type KeyboardEvent, type PointerEvent } from 'react';
import { announcePanels } from '../../lib/bridge';

const KEY = 'dokky.inspector-width';
const MIN = 300;
const STEP = 16;
// колонка чата не уже этого: правая панель не съедает переписку
const MIN_MAIN = 420;

const shell = () => document.getElementById('shell');
const px = (name: string): number => parseFloat(getComputedStyle(shell() as HTMLElement).getPropertyValue(name)) || 0;

/** Сколько можно отдать правой панели при текущей ширине окна и открытом меню слева. */
export function clampWidth(width: number, windowWidth: number, left: number): number {
  const max = Math.max(MIN, Math.min(900, windowWidth - left - MIN_MAIN));
  return Math.round(Math.min(max, Math.max(MIN, width)));
}

function saved(): number | null {
  try {
    const value = Number(localStorage.getItem(KEY));
    return Number.isFinite(value) && value >= MIN ? value : null;
  } catch {
    return null;
  }
}

/** Ширина правой панели: переменная сетки и, на macOS со стеклом, нативная плитка панели. */
function apply(width: number | null, remember: boolean): void {
  const node = shell();
  if (!node) return;
  if (width == null) node.style.removeProperty('--rw-open');
  else node.style.setProperty('--rw-open', `${clampWidth(width, window.innerWidth, node.classList.contains('left-collapsed') ? 0 : px('--lw-open'))}px`);
  // что на самом деле показано, говорит сетка: в режиме чата правой панели нет, даже если она «открыта»
  const right = !node.classList.contains('right-collapsed');
  if (right) announcePanels({ left: !node.classList.contains('left-collapsed'), right, lw: px('--lw-open'), rw: px('--rw-open'), animate: false, ms: 0, c: [0.22, 1, 0.36, 1] });
  if (!remember) return;
  try {
    if (width == null) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, String(px('--rw-open')));
  } catch {
    /* без сохранения */
  }
}

/** Ручка у левого края правой панели: тянуть мышью, стрелки с клавиатуры, двойной щелчок - ширина по умолчанию. */
export function PanelResizer() {
  const drag = useRef<{ id: number } | null>(null);

  // сохранённая ширина при запуске и подгонка под окно, когда оно сужается
  useEffect(() => {
    const restore = () => { const width = saved(); if (width != null) apply(width, false); };
    restore();
    window.addEventListener('resize', restore);
    return () => window.removeEventListener('resize', restore);
  }, []);

  const move = (clientX: number) => {
    const box = shell()?.getBoundingClientRect();
    if (box) apply(box.right - clientX, false);
  };
  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    drag.current = { id: event.pointerId };
    event.currentTarget.setPointerCapture(event.pointerId);
    document.documentElement.classList.add('panel-resizing');
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.id === event.pointerId) move(event.clientX);
  };
  const finish = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.id !== event.pointerId) return;
    drag.current = null;
    document.documentElement.classList.remove('panel-resizing');
    apply(px('--rw-open'), true);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const delta = event.key === 'ArrowLeft' ? STEP : event.key === 'ArrowRight' ? -STEP : 0;
    if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      apply(event.key === 'Home' ? 9999 : MIN, true);
    } else if (delta) {
      event.preventDefault();
      apply(px('--rw-open') + delta, true);
    }
  };
  return (
    <div
      className="panel-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label="Ширина правой панели"
      aria-valuemin={MIN}
      title="Потяните, чтобы изменить ширину. Двойной щелчок - по умолчанию"
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={finish}
      onPointerCancel={finish}
      onLostPointerCapture={finish}
      onDoubleClick={() => apply(null, true)}
      onKeyDown={onKeyDown}
    />
  );
}

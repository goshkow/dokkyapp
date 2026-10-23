import { useLayoutEffect, useRef } from 'react';
import { playFlip, snapshotLayout, type Snapshot } from '../lib/flip';
import { announcePanels, growWindow } from '../lib/bridge';
import { isShellReady } from './panels';
import { ui, useUi, type Scope } from './ui';
import { useView } from './view';

export interface Shown {
  left: boolean;
  right: boolean;
  /** Правая панель вообще возможна: режим разработки и открытый чат или проект. */
  rightAvailable: boolean;
}

/** Что показано на экране: правая панель живёт только в разработке и только там, где уже есть контекст. */
export function useShown(): Shown {
  const left = useUi((s) => s.leftOpen);
  const open = useUi((s) => s.rightOpen);
  const scope = useUi((s) => s.scope);
  const context = useView((s) => s.page === 'chat' && Boolean(s.chat || s.project));
  const rightAvailable = scope === 'development' && context;
  return { left, right: open && rightAvailable, rightAvailable };
}

const DURATION = 380;
const EASING = 'cubic-bezier(0.22, 1, 0.36, 1)';
const CURVE = [0.22, 1, 0.36, 1];

const px = (shell: HTMLElement, name: string): number => parseFloat(getComputedStyle(shell).getPropertyValue(name)) || 0;
const chatWidth = () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--chat-width')) || 760;
// колонка чата не уже своей ширины: иначе растёт окно, а не сжимаются текст и поле ввода
const minMain = () => Math.min(chatWidth() + 56, 760);

/**
 * Панели открываются без пересчёта чата на каждом кадре: сетка сразу получает итоговые колонки, а плавность дают
 * только transform (`lib/flip.ts`): едут панели, лента, поле ввода, название, переключатель и кнопки. Окно растёт только при нехватке места.
 * Положение «до» снимается во время рендера, пока DOM ещё старый.
 */
export function useShellMotion(left: boolean, right: boolean, scope: Scope): void {
  const committed = useRef({ left, right, scope, first: true });
  const before = useRef<Snapshot | null>(null);
  const state = committed.current;
  if (!state.first && (state.left !== left || state.right !== right)) before.current = snapshotLayout();
  useLayoutEffect(() => {
    const shell = document.getElementById('shell');
    const was = { ...committed.current };
    committed.current = { left, right, scope, first: false };
    const snapshot = before.current;
    before.current = null;
    if (shell && was.first) {
      // нативные плитки встают на место без движения
      announcePanels({ left, right, lw: px(shell, '--lw-open'), rw: px(shell, '--rw-open'), animate: false, ms: 0, c: CURVE });
    }
    if (!shell || was.first || (was.left === left && was.right === right)) return;
    const lwN = left ? px(shell, '--lw-open') : 0;
    const lwO = was.left ? px(shell, '--lw-open') : 0;
    const rwN = right ? px(shell, '--rw-open') : 0;
    const rwO = was.right ? px(shell, '--rw-open') : 0;
    const smooth = isShellReady() && was.scope === scope && ui.get().motion !== 'reduced';
    const width = window.innerWidth;
    const added = Math.max(0, lwN - lwO) + Math.max(0, rwN - rwO);
    let grow = 0;
    if (isShellReady() && added > 0) {
      const missing = minMain() - (width - lwN - rwN);
      if (missing > 0) grow = Math.min(added, Math.ceil(missing));
    }
    if (grow > 0) {
      const asked = growWindow(grow, smooth ? DURATION : 0, lwN > lwO ? 'left' : 'right');
      if (smooth && asked) {
        // колонка чата держит итоговую ширину, пока окно растёт: текст не сжимается и не переносится заново
        shell.style.setProperty('--main-hold', `${width + grow - lwN - rwN}px`);
        shell.classList.add('growing');
        window.setTimeout(() => shell.classList.remove('growing'), DURATION + 120);
      }
    }
    // после просьбы расширить окно: хост уже знает итоговую ширину и ставит правую плитку по ней
    announcePanels({ left, right, lw: lwN || px(shell, '--lw-open'), rw: rwN || px(shell, '--rw-open'), animate: smooth, ms: DURATION, c: CURVE });
    if (smooth && snapshot) playFlip(snapshot, DURATION, EASING);
  }, [left, right, scope]);
}

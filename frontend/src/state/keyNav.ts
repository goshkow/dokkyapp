/*
 * Навигация по интерфейсу с клавиатуры. Esc уводит в левое меню на открытый чат, Esc из меню - обратно в поле ввода
 * (на странице без поля - в её содержимое). В меню и правой панели стрелки двигают фокус по месту на экране, Home и
 * End - к первому и последнему пункту, Enter на чате открывает его и возвращает в поле, пробел открывает без перехода.
 * F6 и Shift+F6 переключают меню, поле и правую панель по кругу. На macOS со стеклом меню, поле и правая панель - свои
 * веб-виды: клавиатуру между ними переводит хост (focusView), а фокус внутри вида ставит сам вид (действия nav*).
 */
import { nativeTiles } from '../lib/glass';
import { call, exposeActions, isMirror, remote, role } from '../lib/mirror';
import { focusComposer } from './composer';
import { fieldActions, fieldHost } from './field';
import { setPanel } from './panels';
import { siteFieldActive } from './siteField';
import { SITE_ESCAPE } from './surface';
import { ui } from './ui';
import { view } from './view';

type Zone = 'sidebar' | 'composer' | 'inspector';
export type Direction = 'up' | 'down' | 'left' | 'right';
interface Box { left: number; top: number; right: number; bottom: number; width: number; height: number }

const FOCUSABLE = 'button:not([disabled]), a[href], input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
const REGIONS = '.sidebar-panel, .inspector-panel';
// у этих элементов стрелки свои: текст, списки, меню, вкладки, живой браузер
const OWN_ARROWS = 'input, textarea, select, [contenteditable=""], [contenteditable="true"], [role="tree"], [role="listbox"], [role="menu"], [role="tablist"], [role="slider"], [role="grid"], .browser-stage';
// пока открыто окно, меню или полноэкранный редактор, Esc закрывает их, а не переводит фокус
const BLOCKING = 'dialog[open], .popover[data-state="open"], .expanded-editor.open, .surface-scrim';
// ширина движения панели (state/shellMotion.ts): фокус ставится, когда меню уже выехало
const PANEL_MS = 400;

const multiView = (): boolean => isMirror || nativeTiles();
const keyboardMode = () => { document.documentElement.dataset.input = 'keyboard'; };
const toView = (target: Zone | 'main') => {
  if (!multiView()) return;
  try { window.koddiWindowBridge?.focusView?.(target); } catch { /* окно без моста */ }
};

const visible = (node: HTMLElement): boolean =>
  !node.closest('[inert]') && node.getClientRects().length > 0 && (node.checkVisibility?.({ opacityProperty: true, visibilityProperty: true }) ?? true);

export const focusables = (region: Element): HTMLElement[] => [...region.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(visible);

function focusOn(node: HTMLElement): void {
  keyboardMode();
  node.focus({ preventScroll: true });
  node.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
}

/** Ближайший пункт в сторону стрелки: сперва по расстоянию вдоль движения, потом по смещению поперёк него. */
export function nearest<T>(from: Box, items: { item: T; box: Box }[], direction: Direction): T | undefined {
  const cx = from.left + from.width / 2;
  const cy = from.top + from.height / 2;
  let best: T | undefined;
  let score = Infinity;
  for (const { item, box } of items) {
    const x = box.left + box.width / 2;
    const y = box.top + box.height / 2;
    const vertical = direction === 'up' || direction === 'down';
    const ahead = direction === 'down' ? y - cy : direction === 'up' ? cy - y : direction === 'right' ? x - cx : cx - x;
    // центр в той же строке (столбце) - не движение в эту сторону
    if (ahead <= (vertical ? Math.min(from.height, box.height) : Math.min(from.width, box.width)) / 2) continue;
    const gap = direction === 'down' ? box.top - from.bottom : direction === 'up' ? from.top - box.bottom : direction === 'right' ? box.left - from.right : from.left - box.right;
    const cross = vertical
      ? Math.max(0, box.left - from.right, from.left - box.right) * 2 + Math.abs(x - cx) * 0.05
      : Math.max(0, box.top - from.bottom, from.top - box.bottom) * 3 + Math.abs(y - cy) * 0.05;
    const value = Math.max(0, gap) + cross;
    if (value < score) { score = value; best = item; }
  }
  return best;
}

/* фокус внутри своего вида */
function sidebarRegion(): HTMLElement | null {
  const region = document.querySelector<HTMLElement>('.sidebar-panel');
  return region?.childElementCount ? region : null;
}

function enterSidebar(): boolean {
  const region = sidebarRegion();
  if (!region) return false;
  const target = [
    region.querySelector<HTMLElement>('.chat-row[data-selected] .chat-item'),
    region.querySelector<HTMLElement>('.chat-item'),
    focusables(region)[0],
  ].find((node) => node && visible(node));
  if (!target) return false;
  focusOn(target);
  return true;
}

function enterInspector(): boolean {
  const region = document.querySelector<HTMLElement>('.inspector-panel');
  const target = region?.childElementCount ? focusables(region)[0] : undefined;
  if (!target) return false;
  focusOn(target);
  return true;
}

/** Главный вид: клавиатура в поле ввода, а на странице без поля - во фрейм сайта или в первый элемент страницы. */
function enterComposer(): void {
  if (siteFieldActive() || (view.get().page === 'chat' && document.getElementById('composer'))) {
    focusComposer();
    toView('composer');
    return;
  }
  const main = document.getElementById('main');
  if (!main) return;
  const frame = main.querySelector<HTMLIFrameElement>('.site-surface:not([hidden]) iframe');
  toView('main');
  if (frame) { frame.focus(); return; }
  const first = focusables(main)[0];
  if (first) focusOn(first);
}

exposeActions({ navSidebar: enterSidebar, navInspector: enterInspector, navComposer: enterComposer });

/* переходы между зонами из любого вида */
const openLeft = (): boolean => {
  if (ui.get().leftOpen) return false;
  setPanel('left', true);
  return true;
};

export function goSidebar(): void {
  if (role === 'sidebar') { enterSidebar(); return; }
  const opened = openLeft();
  const run = () => {
    if (multiView()) {
      toView('sidebar');
      void call('sidebar', 'navSidebar', []).catch(() => undefined);
    } else enterSidebar();
  };
  if (opened) window.setTimeout(run, PANEL_MS);
  else run();
}

export const goComposer = remote('navComposer', enterComposer);

function goInspector(): boolean {
  const { rightOpen, scope } = ui.get();
  const { page, chat, project } = view.get();
  if (!rightOpen || scope !== 'development' || page !== 'chat' || !(chat || project)) return false;
  if (role === 'inspector') return enterInspector();
  if (multiView()) {
    toView('inspector');
    void call('inspector', 'navInspector', []).catch(() => undefined);
    return true;
  }
  return enterInspector();
}

function currentZone(): Zone {
  if (role === 'sidebar' || role === 'inspector') return role;
  if (role === 'composer') return 'composer';
  const active = document.activeElement;
  if (active?.closest('.sidebar-panel')) return 'sidebar';
  if (active?.closest('.inspector-panel')) return 'inspector';
  return 'composer';
}

function cycle(step: 1 | -1): void {
  const order: Zone[] = ['sidebar', 'composer', 'inspector'];
  let index = order.indexOf(currentZone());
  for (let tries = 0; tries < order.length; tries += 1) {
    index = (index + step + order.length) % order.length;
    const zone = order[index];
    if (zone === 'sidebar') { goSidebar(); return; }
    if (zone === 'composer') { void goComposer(); return; }
    if (goInspector()) return;
  }
}

function blocked(): boolean {
  try {
    return Boolean(document.querySelector(BLOCKING));
  } catch {
    return false;
  }
}

function move(region: HTMLElement, from: HTMLElement, key: string): boolean {
  const items = focusables(region);
  if (!items.length) return false;
  let target: HTMLElement | undefined;
  if (key === 'Home') target = items[0];
  else if (key === 'End') target = items.at(-1);
  else {
    const direction = ({ ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right' } as const)[key as 'ArrowUp'];
    if (!direction) return false;
    target = nearest(from.getBoundingClientRect(), items.filter((item) => item !== from).map((item) => ({ item, box: item.getBoundingClientRect() })), direction);
  }
  if (target) focusOn(target);
  return true;
}

export function onKeyDown(event: KeyboardEvent): void {
  if (event.defaultPrevented || event.isComposing || event.metaKey || event.ctrlKey || event.altKey) return;
  const target = event.target instanceof HTMLElement ? event.target : null;
  if (event.key === 'F6') {
    event.preventDefault();
    cycle(event.shiftKey ? -1 : 1);
    return;
  }
  if (event.shiftKey) return;
  if (event.key === 'Escape') {
    // меню «Добавить» открыто в главном виде, а клавиатура в поле: Esc закрывает его, как щелчок мимо
    if (role === 'composer' && fieldHost.get().menu) {
      event.preventDefault();
      void fieldActions.menu(false);
      return;
    }
    if (blocked()) return;
    event.preventDefault();
    if (role === 'sidebar' || target?.closest('.sidebar-panel')) void goComposer();
    else goSidebar();
    return;
  }
  const region = target?.closest<HTMLElement>(REGIONS);
  if (!region || !target || target.closest(OWN_ARROWS)) return;
  if (event.key === 'Enter' && target.matches('.chat-item')) {
    // чат открывается щелчком кнопки (действие Enter по умолчанию), затем клавиатура уходит в поле
    window.setTimeout(() => void goComposer(), 0);
    return;
  }
  if (move(region, target, event.key)) event.preventDefault();
}

let started = false;
/** Подключается в каждом виде приложения (главный и плитки). */
export function initKeyNav(): void {
  if (started) return;
  started = true;
  document.addEventListener('keydown', onKeyDown);
  // кольцо фокуса только при работе с клавиатуры
  document.addEventListener('keydown', (event) => {
    if (['Tab', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'F6'].includes(event.key)) keyboardMode();
  }, true);
  document.addEventListener('pointerdown', () => { delete document.documentElement.dataset.input; }, true);
  if (!isMirror) window.addEventListener(SITE_ESCAPE, () => { if (!blocked()) goSidebar(); });
}

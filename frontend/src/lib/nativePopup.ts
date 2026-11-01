/**
 * Меню и списки отдельным окном поверх приложения: они не ограничены окном и не прячутся за плитками стекла.
 * Меню по-прежнему рисует тот вид, где оно открыто (скрытая панель Popover), а окно показывает копию его разметки
 * и отдаёт обратно щелчки и ввод - поэтому у всех меню один и тот же код и обработчики. В браузере и без окна
 * всё остаётся обычной панелью на странице.
 */
import { call, exposeActions, role as viewRole, type Role } from './mirror';

export const POPUP_MARGIN = 24;
const FIRST_REPLY_MS = 500;
/** Щелчок по кнопке меню, снявший фокус с его окна, не должен открыть меню снова. */
const REOPEN_GUARD_MS = 400;

export interface PopupContent {
  id: number;
  owner: Role;
  html: string;
  className: string;
  role: string;
  label?: string;
  side: string;
  minWidth: number;
  root: { className: string; style: string; data: Record<string, string> };
}
export interface PopupAction { id: number; type: 'click' | 'input' | 'key'; path: number[]; value?: string; key?: string }

/** Адрес узла от корня панели: разметка копии совпадает с исходной. */
export function pathOf(root: Element, node: Element): number[] {
  const path: number[] = [];
  for (let item = node; item !== root; item = item.parentElement as Element) {
    if (!item.parentElement) return [];
    path.unshift([...item.parentElement.children].indexOf(item));
  }
  return path;
}

export const nodeAt = (root: Element, path: number[]): Element | null =>
  path.reduce<Element | null>((node, index) => node?.children[index] ?? null, root);

/** Действие из окна меню над настоящей разметкой: React видит обычные события. */
export function applyAction(panel: Element, action: PopupAction): void {
  const node = nodeAt(panel, action.path);
  if (!node) return;
  if (action.type === 'click') {
    node.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  } else if (action.type === 'input' && node instanceof HTMLInputElement) {
    // React следит за value через свой дескриптор: значение ставится мимо него, иначе onChange не сработает
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(node, action.value ?? '');
    node.dispatchEvent(new Event('input', { bubbles: true }));
  } else if (action.type === 'key') {
    node.dispatchEvent(new KeyboardEvent('keydown', { key: action.key ?? '', bubbles: true, cancelable: true }));
  }
}

// окно не ответило: пока не пробуем снова, иначе каждое меню ждёт ответа перед показом на странице
const RETRY_AFTER_MS = 30_000;
let unavailableUntil = 0;
export const nativePopupPossible = (): boolean => viewRole !== 'popup' && Boolean(window.koddiWindowBridge?.popupOpen) && Date.now() >= unavailableUntil;

interface Session { panel: Element; onClose: () => void; trigger: HTMLElement | null; closeFirst: boolean; hide: () => void; keepFocus: () => void }
const sessions = new Map<number, Session>();
let seq = 0;
let blurred: { trigger: HTMLElement | null; at: number } | null = null;

exposeActions({
  popupAction: (action: PopupAction) => {
    const session = sessions.get(action.id);
    if (!session) return;
    if (session.closeFirst && action.type === 'click') {
      // пункт работает с полем под меню (вставка, копирование): сначала клавиатура возвращается к нему
      session.hide();
      window.setTimeout(() => applyAction(session.panel, action), 40);
      return;
    }
    applyAction(session.panel, action);
  },
  popupDismissed: (id: number, reason: string) => {
    const session = sessions.get(id);
    if (!session) return;
    if (reason === 'blur') {
      blurred = { trigger: session.trigger, at: Date.now() };
      // щелчок мимо меню уже отдал клавиатуру туда, куда нажали: не забирать её обратно
      session.keepFocus();
    }
    session.onClose();
    if (reason === 'escape') session.trigger?.focus({ preventScroll: true });
  },
});

document.addEventListener('click', (event) => {
  if (!blurred) return;
  const { trigger, at } = blurred;
  blurred = null;
  if (trigger?.contains(event.target as Node) && Date.now() - at < REOPEN_GUARD_MS) {
    event.preventDefault();
    event.stopPropagation();
  }
}, true);

export interface PopupOptions {
  panel: HTMLElement;
  rect: { x: number; y: number; w: number; h: number };
  side: string;
  align: string;
  minWidth: number;
  className: string;
  role: string;
  label?: string;
  trigger: HTMLElement | null;
  closeFirst: boolean;
  onClose: () => void;
  onUnavailable: () => void;
}

/** Открывает окно меню; возвращает закрытие. Окно не ответило (ещё не загрузилось) - меню остаётся на странице. */
export function openNativePopup(options: PopupOptions): () => void {
  const bridge = window.koddiWindowBridge!;
  const { panel } = options;
  const id = ++seq;
  const message = JSON.stringify({ id });
  let replied = false;
  let frame = 0;
  let sent = '';
  let hidden = false;
  let refocus = true;
  const hide = () => {
    if (hidden) return;
    hidden = true;
    bridge.popupClose?.(message);
    if (refocus && (viewRole === 'main' || viewRole === 'sidebar' || viewRole === 'inspector' || viewRole === 'composer')) bridge.focusView?.(viewRole);
  };
  const sync = () => {
    frame = 0;
    const html = panel.innerHTML;
    if (html === sent) return;
    sent = html;
    const root = document.documentElement;
    const content: PopupContent = {
      id, owner: viewRole, html, className: options.className, role: options.role, label: options.label, side: options.side,
      minWidth: options.minWidth,
      root: { className: root.className, style: root.getAttribute('style') ?? '', data: { ...root.dataset } as Record<string, string> },
    };
    void call('popup', 'popupShow', [content]).then(() => { replied = true; }, () => undefined);
  };
  const observer = new MutationObserver(() => { frame ||= requestAnimationFrame(sync); });
  observer.observe(panel, { subtree: true, childList: true, attributes: true, characterData: true });
  sessions.set(id, { panel, onClose: options.onClose, trigger: options.trigger, closeFirst: options.closeFirst, hide, keepFocus: () => { refocus = false; } });
  bridge.popupOpen?.(JSON.stringify({ id, ...options.rect, vw: innerWidth, vh: innerHeight, side: options.side, align: options.align }));
  sync();
  const fallback = window.setTimeout(() => {
    if (replied || !sessions.has(id)) return;
    unavailableUntil = Date.now() + RETRY_AFTER_MS;
    options.onUnavailable();
  }, FIRST_REPLY_MS);
  return () => {
    window.clearTimeout(fallback);
    cancelAnimationFrame(frame);
    observer.disconnect();
    sessions.delete(id);
    hide();
    void call('popup', 'popupHide', [id]).catch(() => undefined);
  };
}

/** Страница окна меню: показывает копию разметки меню из вида-владельца и отдаёт ему щелчки и ввод. */
import { moveMenuFocus } from './lib/menuKeys';
import { call, exposeActions, type Role } from './lib/mirror';
import { nodeAt, pathOf, type PopupAction, type PopupContent } from './lib/nativePopup';

const root = document.documentElement;
root.classList.add('tile', 'popup-view');
document.body.classList.remove('booting');
const panel = document.createElement('div');
panel.hidden = true;
document.body.append(panel);

let current = 0;
let owner: Role = 'main';
let html = '';
let reported = '';

function act(action: Omit<PopupAction, 'id'>): void {
  void call(owner, 'popupAction', [{ ...action, id: current }]).catch(() => undefined);
}

function dismiss(reason: string): void {
  if (current) void call(owner, 'popupDismissed', [current, reason]).catch(() => undefined);
}

function report(): void {
  const text = JSON.stringify({ id: current, w: panel.offsetWidth, h: panel.offsetHeight });
  if (text === reported) return;
  reported = text;
  window.koddiWindowBridge?.popupResize?.(text);
}

/** Владелец перерисовал меню: прокрутка, фокус и набранный текст копии остаются на месте. */
function update(next: string): void {
  if (next === html) return;
  html = next;
  const focused = document.activeElement && panel.contains(document.activeElement) ? pathOf(panel, document.activeElement) : null;
  const values = [...panel.querySelectorAll('input')].map((input) => [pathOf(panel, input), input.value] as const);
  const scrolls = [panel, ...panel.querySelectorAll('*')].filter((item) => item.scrollTop > 0).map((item) => [pathOf(panel, item), item.scrollTop] as const);
  panel.innerHTML = next;
  for (const [path, value] of values) {
    const input = nodeAt(panel, path);
    if (input instanceof HTMLInputElement && input.type !== 'checkbox' && input.type !== 'radio') input.value = value;
  }
  for (const [path, top] of scrolls) {
    const item = nodeAt(panel, path);
    if (item) item.scrollTop = top;
  }
  if (focused) (nodeAt(panel, focused) as HTMLElement | null)?.focus({ preventScroll: true });
}

exposeActions({
  popupShow: (content: PopupContent) => {
    if (content.id !== current) {
      current = content.id;
      owner = content.owner;
      html = '';
      reported = '';
    }
    root.className = `${content.root.className} tile popup-view`;
    root.setAttribute('style', content.root.style);
    for (const [key, value] of Object.entries(content.root.data)) root.dataset[key] = value;
    panel.className = `popover ${content.className}`;
    panel.setAttribute('role', content.role);
    if (content.label) panel.setAttribute('aria-label', content.label);
    panel.dataset.side = content.side;
    panel.style.minWidth = content.minWidth ? `${content.minWidth}px` : '';
    panel.hidden = false;
    update(content.html);
    report();
  },
  popupHide: (id: number) => {
    if (id !== current) return;
    current = 0;
    html = '';
    panel.hidden = true;
    panel.innerHTML = '';
  },
});

// окно показано и получило клавиатуру: фокус на текущий пункт или поле поиска
window.koddiPopupShown = () => {
  panel.querySelector<HTMLElement>('input:not([type=checkbox]):not([type=radio]), [role="option"][aria-selected="true"], [role="menuitem"]:not(:disabled), [role="option"]:not(:disabled)')
    ?.focus({ preventScroll: true });
};
window.koddiPopupBlur = () => dismiss('blur');

new ResizeObserver(report).observe(panel);
document.addEventListener('contextmenu', (event) => event.preventDefault());
document.addEventListener('pointerdown', (event) => {
  // прозрачное поле для тени вокруг меню: щелчок по нему - мимо меню
  if (!panel.contains(event.target as Node)) dismiss('outside');
});
panel.addEventListener('click', (event) => {
  const target = event.target as Element;
  event.preventDefault();
  // щелчок по подписи браузер повторит на её поле: уйдёт только этот второй
  if (target.closest('label') && !(target instanceof HTMLInputElement)) return;
  if (target instanceof HTMLInputElement && target.type !== 'checkbox' && target.type !== 'radio') return;
  act({ type: 'click', path: pathOf(panel, target) });
});
panel.addEventListener('input', (event) => {
  const target = event.target;
  if (target instanceof HTMLInputElement) act({ type: 'input', path: pathOf(panel, target), value: target.value });
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    event.preventDefault();
    dismiss('escape');
    return;
  }
  const typing = event.target instanceof HTMLInputElement;
  if (typing && event.key === 'Enter') {
    event.preventDefault();
    act({ type: 'key', path: pathOf(panel, event.target as Element), key: 'Enter' });
    return;
  }
  if (typing && !['ArrowDown', 'ArrowUp', 'Tab'].includes(event.key)) return;
  if (event.key === 'Tab') {
    event.preventDefault();
    dismiss('tab');
    return;
  }
  moveMenuFocus(event, panel);
});

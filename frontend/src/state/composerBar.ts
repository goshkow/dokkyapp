/**
 * Переключатели над полем под Liquid Glass. Кнопки остаются на странице главного вида: от них рамка стекла, меню
 * и клавиатура. Рисует их нативная плитка над лентой, как поле ввода, поэтому её вид получает снимок кнопки, а нажатие
 * возвращает сюда.
 */
import { exposeActions, exposeStore, isMirror, mirrorStore, remote } from '../lib/mirror';
import { createStore, useStore } from '../lib/store';

export const BAR_CONTROLS = ['pill', 'product', 'mode'] as const;
export type BarControlId = typeof BAR_CONTROLS[number];
export interface BarControl { label: string; title: string; disabled: boolean; open: boolean; focus: boolean }
export type ComposerBar = Record<BarControlId, BarControl | null>;

const EMPTY: ComposerBar = { pill: null, product: null, mode: null };
export const composerBar = createStore<ComposerBar>(EMPTY);
export const useComposerBar = <S,>(selector: (state: ComposerBar) => S): S => useStore(composerBar, selector);
if (isMirror) mirrorStore('composerBar', composerBar);
else exposeStore('composerBar', composerBar);

export const isBarControl = (value: string): value is BarControlId => (BAR_CONTROLS as readonly string[]).includes(value);

function read(root: HTMLElement): ComposerBar {
  const next: ComposerBar = { ...EMPTY };
  for (const id of BAR_CONTROLS) {
    const button = root.querySelector<HTMLButtonElement>(`[data-glass="${id}"]`);
    if (!button) continue;
    next[id] = {
      label: button.querySelector('span')?.textContent ?? '',
      title: button.getAttribute('aria-label') ?? '',
      disabled: button.disabled,
      open: button.getAttribute('aria-expanded') === 'true',
      focus: button.matches(':focus-visible'),
    };
  }
  return next;
}

/** Плитки повторяют кнопки ряда: подпись и состояния берутся из разметки, второго источника у них нет. */
export function watchComposerBar(root: HTMLElement): () => void {
  let sent = '';
  const publish = () => {
    const next = read(root);
    const text = JSON.stringify(next);
    if (text === sent) return;
    sent = text;
    composerBar.set(next);
  };
  const changes = new MutationObserver(publish);
  changes.observe(root, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['aria-expanded', 'aria-label', 'disabled', 'data-glass'] });
  root.addEventListener('focusin', publish);
  root.addEventListener('focusout', publish);
  publish();
  return () => {
    changes.disconnect();
    root.removeEventListener('focusin', publish);
    root.removeEventListener('focusout', publish);
    composerBar.set(EMPTY);
  };
}

/** Обычный щелчок по настоящей кнопке: так работают и её меню, и защита от повторного открытия. */
function realPress(id: BarControlId): void {
  if (isBarControl(id)) document.querySelector<HTMLElement>(`#composer [data-glass="${id}"]`)?.click();
}
if (!isMirror) exposeActions({ pressBarControl: realPress });
export const pressBarControl = remote('pressBarControl', realPress);

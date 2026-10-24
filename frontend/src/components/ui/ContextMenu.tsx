import { useCallback, useEffect, useRef, useState } from 'react';
import { copyText } from '../../lib/clipboard';
import { Popover } from './Popover';
import { mod } from '../../lib/platform';

/* Своё контекстное меню вместо родного меню браузера (назад, перезагрузить, исходный код страницы).
   Показывается там, где есть что делать: выделенный текст, поле ввода, ссылка. Свои меню (чаты) не трогаются. */

declare global {
  interface Window {
    koddiPageAction?: (name: string, event: Event) => Promise<boolean>;
  }
}

type Action = 'cut' | 'copy' | 'paste' | 'selectAll';

interface Entry {
  label: string;
  hint?: string;
  disabled?: boolean;
  run?: (event: Event) => void | Promise<void>;
  separator?: boolean;
}

interface State {
  x: number;
  y: number;
  entries: Entry[];
  /** элемент, который держал фокус до меню: действие выполняется над ним */
  restore: HTMLElement | null;
}

const EDITABLE = 'textarea, input:not([type="checkbox"]):not([type="radio"]):not([type="button"]):not([type="submit"]):not([type="range"]), [contenteditable=""], [contenteditable="true"]';

const selectedText = (editable: HTMLElement | null): string => {
  if (editable instanceof HTMLInputElement || editable instanceof HTMLTextAreaElement) {
    return editable.value.slice(editable.selectionStart ?? 0, editable.selectionEnd ?? 0);
  }
  return window.getSelection()?.toString() ?? '';
};

const openLink = (href: string): void => {
  if (window.koddiDesktop) window.location.href = `koddi://open-url?url=${encodeURIComponent(href)}`;
  else window.open(href, '_blank', 'noopener,noreferrer');
};

async function perform(action: Action, event: Event): Promise<void> {
  if (window.koddiDesktop && typeof window.koddiPageAction === 'function') {
    await window.koddiPageAction(action, event);
    return;
  }
  // в обычном браузере: вставка через буфер, остальное родными командами
  if (action === 'paste') {
    try {
      const text = await navigator.clipboard.readText();
      const target = document.activeElement;
      if (target?.matches('[data-auto-attach-paste="true"]')) {
        const clipboardData = new DataTransfer();
        clipboardData.setData('text/plain', text);
        if (!target.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData }))) return;
      }
      document.execCommand('insertText', false, text);
    } catch {
      /* буфер недоступен */
    }
    return;
  }
  document.execCommand(action);
}

function buildEntries(target: Element): Entry[] {
  const editable = target.closest<HTMLElement>(EDITABLE);
  const link = target.closest<HTMLAnchorElement>('a[href]');
  const href = link && /^https?:/i.test(link.href) ? link.href : '';
  const text = selectedText(editable);
  const entries: Entry[] = [];
  if (href) {
    entries.push({ label: 'Открыть ссылку', run: () => openLink(href) });
    entries.push({ label: 'Копировать ссылку', run: async (event) => { await copyText(href, event as MouseEvent, 'Ссылка скопирована'); } });
  }
  if (editable) {
    const readOnly = (editable as HTMLInputElement).readOnly || (editable as HTMLInputElement).disabled;
    if (entries.length) entries.push({ label: '', separator: true });
    entries.push({ label: 'Вырезать', hint: `${mod()}X`, disabled: !text || readOnly, run: (event) => perform('cut', event) });
    entries.push({ label: 'Копировать', hint: `${mod()}C`, disabled: !text, run: (event) => perform('copy', event) });
    entries.push({ label: 'Вставить', hint: `${mod()}V`, disabled: readOnly, run: (event) => perform('paste', event) });
    entries.push({ label: '', separator: true });
    entries.push({ label: 'Выделить всё', hint: `${mod()}A`, run: (event) => perform('selectAll', event) });
  } else if (text) {
    if (entries.length) entries.push({ label: '', separator: true });
    entries.push({ label: 'Копировать', hint: `${mod()}C`, run: (event) => perform('copy', event) });
  }
  return entries;
}

export function ContextMenu() {
  const [state, setState] = useState<State | null>(null);
  const [open, setOpen] = useState(false);
  const last = useRef<State | null>(null);
  if (state) last.current = state;

  useEffect(() => {
    const onContext = (event: MouseEvent) => {
      // у чатов и других мест своё меню: оно уже вызвало preventDefault
      if (event.defaultPrevented) return;
      event.preventDefault();
      const target = event.target as Element | null;
      if (!target?.closest) return;
      const entries = buildEntries(target);
      if (!entries.length) {
        setOpen(false);
        return;
      }
      const active = document.activeElement as HTMLElement | null;
      setState({ x: event.clientX, y: event.clientY, entries, restore: active && active !== document.body ? active : null });
      setOpen(true);
    };
    document.addEventListener('contextmenu', onContext);
    return () => document.removeEventListener('contextmenu', onContext);
  }, []);

  const close = useCallback(() => setOpen(false), []);
  const shown = state ?? last.current;
  return (
    <Popover open={open && Boolean(state)} anchor={shown ? { x: shown.x, y: shown.y } : null} onClose={close} label="Действия" minWidth={210} className="context-menu">
      {shown?.entries.map((entry, index) =>
        entry.separator ? (
          <div key={index} className="menu-separator" role="separator" />
        ) : (
          <button
            key={index}
            type="button"
            role="menuitem"
            className="menu-item"
            disabled={entry.disabled}
            // не отнимаем фокус и выделение у поля, над которым вызвано меню
            onMouseDown={(event) => event.preventDefault()}
            onClick={(event) => {
              const restore = shown.restore;
              setOpen(false);
              restore?.focus({ preventScroll: true });
              void entry.run?.(event.nativeEvent);
            }}
          >
            <span>{entry.label}</span>
            {entry.hint && <kbd className="menu-hint">{entry.hint}</kbd>}
          </button>
        ),
      )}
    </Popover>
  );
}

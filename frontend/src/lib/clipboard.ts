import { notify } from '../state/overlay';

declare global {
  interface Window {
    koddiClipboardWrite?: (text: string, event: Event) => Promise<boolean>;
  }
}

/** Копирование по настоящему щелчку: в окне приложения запись идёт через мост хоста. */
export async function copyText(text: string, event: { isTrusted: boolean; preventDefault(): void; stopPropagation(): void; nativeEvent?: Event }, success: string): Promise<void> {
  event.preventDefault();
  event.stopPropagation();
  if (!event.isTrusted) return;
  try {
    if (window.koddiDesktop) {
      if (typeof window.koddiClipboardWrite !== 'function' || !(await window.koddiClipboardWrite(text, event.nativeEvent ?? (event as unknown as Event)))) throw new Error('clipboard');
    } else await navigator.clipboard.writeText(text);
    notify(success);
  } catch {
    notify('Не удалось скопировать в буфер обмена', true);
  }
}

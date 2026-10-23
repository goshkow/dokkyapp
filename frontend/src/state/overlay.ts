import { createStore, useStore } from '../lib/store';

/* уведомления */
export interface Toast {
  id: number;
  text: string;
  error: boolean;
  closing: boolean;
}

export const toasts = createStore<{ items: Toast[] }>({ items: [] });
export const useToasts = () => useStore(toasts, (state) => state.items);
let toastId = 0;

export function notify(text: string, error = false): void {
  const id = ++toastId;
  toasts.set({ items: [...toasts.get().items.filter((item) => !item.closing), { id, text, error, closing: false }].slice(-3) });
  window.setTimeout(() => toasts.set({ items: toasts.get().items.map((item) => (item.id === id ? { ...item, closing: true } : item)) }), 4300);
  window.setTimeout(() => toasts.set({ items: toasts.get().items.filter((item) => item.id !== id) }), 4500);
}

/** Обработчик ошибок для асинхронных действий интерфейса. */
export function guarded<A extends unknown[]>(action: (...args: A) => unknown | Promise<unknown>) {
  return (...args: A): void => {
    try {
      Promise.resolve(action(...args)).catch((error: unknown) => {
        if ((error as Error)?.name !== 'AbortError') notify((error as Error)?.message || 'Не удалось выполнить действие', true);
      });
    } catch (error) {
      notify((error as Error)?.message || 'Не удалось выполнить действие', true);
    }
  };
}

/* диалоги ввода и подтверждения */
export interface DialogField {
  name: string;
  label: string;
  type?: 'text' | 'textarea' | 'select';
  value?: string | number;
  placeholder?: string;
  required?: boolean;
  options?: { value: string | number; label: string }[];
}

export interface DialogRequest {
  title: string;
  text?: string;
  fields: DialogField[];
  accept: string;
  /** вторая кнопка выбора; если она рекомендована, основной становится она */
  alternative?: string;
  recommended?: 'accept' | 'alternative';
  resolve: (value: Record<string, string> | null) => void;
}

export const dialogs = createStore<{ current: DialogRequest | null }>({ current: null });
export const useDialogRequest = () => useStore(dialogs, (state) => state.current);

export function inputDialog(title: string, fields: DialogField[], accept = 'Сохранить', text?: string): Promise<Record<string, string> | null> {
  return new Promise((resolve) => {
    dialogs.get().current?.resolve(null);
    dialogs.set({ current: { title, text, fields, accept, resolve } });
  });
}

export async function confirmDialog(title: string, text: string, accept = 'Подтвердить'): Promise<boolean> {
  return (await inputDialog(title, [], accept, text)) !== null;
}

/** Выбор из двух действий: 'accept', 'alternative' или null, если окно закрыли. */
export function choiceDialog(title: string, text: string, accept: string, alternative: string, recommended: 'accept' | 'alternative' = 'accept'): Promise<'accept' | 'alternative' | null> {
  return new Promise((resolve) => {
    dialogs.get().current?.resolve(null);
    dialogs.set({ current: {
      title, text, fields: [], accept, alternative, recommended,
      resolve: (value) => resolve(value === null ? null : value.__choice === 'alternative' ? 'alternative' : 'accept'),
    } });
  });
}

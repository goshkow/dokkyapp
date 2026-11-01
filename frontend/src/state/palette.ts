/* Быстрый переход (Cmd+K): чаты, проекты, команды и разделы настроек в одном списке. */
import { createStore, useStore } from '../lib/store';
import type { Chat, Project } from '../lib/types';

export const palette = createStore<{ open: boolean }>({ open: false });
export const usePalette = <S,>(selector: (state: { open: boolean }) => S): S => useStore(palette, selector);
export const togglePalette = (): void => palette.set((state) => ({ open: !state.open }));
export const closePalette = (): void => palette.set({ open: false });

export type PaletteKind = 'Команда' | 'Чат' | 'Проект' | 'Настройки';

export interface PaletteItem {
  key: string;
  kind: PaletteKind;
  title: string;
  /** Дополнительные слова для поиска, на экран не выводятся. */
  keywords?: string;
  hint?: string;
  chat?: Chat;
  project?: Project;
  /** Действие меню приложения или раздел настроек. */
  action?: string;
  section?: string;
}

const MAX_ROWS = 50;
const IDLE_COMMANDS = 8;
const IDLE_CHATS = 6;

/** Подпоследовательность букв: «нвч» находит «Новый чат». */
const subsequence = (needle: string, text: string): boolean => {
  let at = 0;
  for (const letter of text) {
    if (letter === needle[at]) at += 1;
    if (at === needle.length) return true;
  }
  return false;
};

export function paletteScore(item: PaletteItem, query: string): number {
  const title = item.title.toLocaleLowerCase();
  const words = (item.keywords ?? '').toLocaleLowerCase();
  const tokens = query.toLocaleLowerCase().split(/\s+/).filter(Boolean);
  let score = 0;
  for (const token of tokens) {
    if (title.startsWith(token)) score += 4;
    else if (title.includes(` ${token}`)) score += 3;
    else if (title.includes(token)) score += 2;
    else if (words.includes(token)) score += 1;
    else if (subsequence(token, title)) score += 0.5;
    else return 0;
  }
  return score;
}

export function paletteResults(items: PaletteItem[], query: string): PaletteItem[] {
  const needle = query.trim();
  if (!needle) {
    return [...items.filter((item) => item.kind === 'Команда').slice(0, IDLE_COMMANDS), ...items.filter((item) => item.kind === 'Чат').slice(0, IDLE_CHATS)];
  }
  return items
    .map((item, order) => ({ item, order, score: paletteScore(item, needle) }))
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score || a.order - b.order)
    .slice(0, MAX_ROWS)
    .map((entry) => entry.item);
}

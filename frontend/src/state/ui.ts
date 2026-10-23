import { createStore, useStore } from '../lib/store';

export type Theme = 'dark' | 'light';
export type Surface = 'classic' | 'chat-dark' | 'side-dark';
export type Scope = 'chat' | 'search' | 'development';

export interface UiState {
  leftOpen: boolean;
  rightOpen: boolean;
  theme: Theme;
  surface: Surface;
  baseSize: number;
  uiFont: 'inter' | 'system';
  codeFont: string;
  motion: 'full' | 'reduced';
  chatWidth: 'compact' | 'standard' | 'wide';
  scope: Scope;
}

export const ui = createStore<UiState>({
  leftOpen: true,
  rightOpen: false,
  theme: 'dark',
  surface: 'classic',
  baseSize: 14,
  uiFont: 'inter',
  codeFont: 'Consolas',
  motion: 'full',
  chatWidth: 'standard',
  scope: 'development',
});

export const useUi = <S,>(selector: (state: UiState) => S): S => useStore(ui, selector);

const CHAT_WIDTH = { compact: 680, standard: 760, wide: 1080 } as const;

/** Переносит настройки в атрибуты и переменные корня: дальше всё считает CSS. */
export function applyAppearance(state: UiState = ui.get()): void {
  const root = document.documentElement;
  root.dataset.theme = state.theme;
  root.dataset.surface = state.surface;
  root.dataset.motion = state.motion;
  root.dataset.uiFont = state.uiFont;
  root.style.setProperty('--base', `${Math.min(20, Math.max(8, state.baseSize))}px`);
  // название шрифта идёт в CSS в кавычках: лишние символы вырезаются, запасные шрифты остаются
  const name = state.codeFont.replace(/[^A-Za-z0-9 ._,-]/g, '').trim();
  root.style.setProperty('--code-font', name && name !== 'monospace' ? `"${name}", Consolas, Menlo, monospace` : 'Consolas, Menlo, monospace');
  root.style.setProperty('--chat-width', `${CHAT_WIDTH[state.chatWidth]}px`);
}

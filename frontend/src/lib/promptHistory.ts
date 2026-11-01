/* История отправленных запросов для стрелок в поле ввода, как в Claude Code и Codex. Хранится у поля, а не у чата:
   поле под стеклом живёт в своём виде, где сообщений чата нет. У каждого режима своя история: задача для проекта
   в поиске не нужна. */
import { readJson, writeJson } from './storage';

export type PromptScope = 'development' | 'chat' | 'search';

const SCOPES: PromptScope[] = ['development', 'chat', 'search'];
const PREFIX = 'dokky.prompt-history';
const LIMIT = 50;
const ITEM_LIMIT = 20_000;

const key = (scope: PromptScope) => `${PREFIX}.${scope}`;

export function promptHistory(scope: PromptScope): string[] {
  const list = readJson<unknown>(key(scope), []);
  return Array.isArray(list) ? list.filter((item): item is string => typeof item === 'string' && item.length > 0).slice(-LIMIT) : [];
}

/** Повтор уходит в конец, а не копится: стрелка вверх сразу даёт последний запрос. */
export function rememberPrompt(scope: PromptScope, text: string): void {
  const value = text.trim();
  if (!value || value.length > ITEM_LIMIT) return;
  writeJson(key(scope), [...promptHistory(scope).filter((item) => item !== value), value].slice(-LIMIT));
}

/** При выходе из аккаунта: запросы прошлого человека следующему не показываются. */
export function forgetPrompts(): void {
  try {
    for (const scope of SCOPES) localStorage.removeItem(key(scope));
  } catch {
    /* без хранилища и забывать нечего */
  }
}

/** Следующий шаг по истории: null - листать некуда, '' - вернулись к пустому полю. */
export function stepHistory(list: string[], index: number | null, direction: -1 | 1): { index: number | null; text: string } | null {
  if (!list.length) return null;
  const from = index ?? list.length;
  const next = from + direction;
  if (next < 0) return null;
  if (next >= list.length) return index === null ? null : { index: null, text: '' };
  return { index: next, text: list[next] as string };
}

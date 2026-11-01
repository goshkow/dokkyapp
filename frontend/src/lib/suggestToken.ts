/* Токен подсказки под курсором: без зависимостей, его считает поле в любом виде. */
import type { SuggestToken } from '../state/field';

/** «/» только в самом начале поля и без пробелов; «@» - в начале слова. */
export function findToken(text: string, caret: number): SuggestToken | null {
  const before = text.slice(0, caret);
  const slash = /^\/([^\s/]*)$/.exec(before);
  if (slash) return { kind: '/', query: slash[1] ?? '', start: 0, end: caret };
  const at = /(?:^|\s)@([^\s@]*)$/.exec(before);
  if (at) {
    const query = at[1] ?? '';
    return { kind: '@', query, start: caret - query.length - 1, end: caret };
  }
  return null;
}

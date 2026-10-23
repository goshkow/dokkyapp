const LARGE_CHARS = 6000;
const LARGE_LINES = 80;

/** содержимое сохраняется дословно, эвристика определяет только расширение */
export function pastedTextFile(text: string): File | null {
  const lines = text.split(/\r\n|\r|\n/);
  if (!text.trim() || (text.length < LARGE_CHARS && lines.length < LARGE_LINES)) return null;
  const markdown = lines.some((line) => /^[ \t]{0,3}(?:#{1,6}[ \t]+\S|`{3,}|~{3,}|>[ \t]+\S|[-*+][ \t]+(?:\[[ xX]\][ \t]+)?\S|\d+[.)][ \t]+\S)/.test(line))
    || /\[[^[\]\r\n]{1,512}\]\([^\s()]{1,2048}\)|\*\*[^*\r\n]+\*\*|__[^_\r\n]+__|`[^`\r\n]+`|~~[^~\r\n]+~~/.test(text)
    || /(?:^|[\s(])(?:\*[^*\r\n]+\*|_[^_\r\n]+_)(?=[\s.,!?:;)]|$)/.test(text)
    || lines.some((line, index) => index > 0 && lines[index - 1]!.includes('|') && /^\|?[ \t]*:?-{3,}:?[ \t]*\|/.test(line.trim()));
  return new File([text], `Вставка.${markdown ? 'md' : 'txt'}`, { type: markdown ? 'text/markdown;charset=utf-8' : 'text/plain;charset=utf-8' });
}

export function appendPastedText(draft: string, text: string): string {
  return draft ? draft + (draft.endsWith('\n') ? '' : '\n\n') + text : text;
}

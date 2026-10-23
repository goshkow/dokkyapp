import { describe, expect, it } from 'vitest';
import { appendPastedText, pastedTextFile } from './pastedText';

describe('большая вставка', () => {
  it('оставляет короткую вставку и пустые строки обычным текстом', () => {
    expect(pastedTextFile('# Короткий заголовок\nТекст')).toBeNull();
    expect(pastedTextFile(' '.repeat(7000))).toBeNull();
  });
  it('использует txt для длинного текста и многострочной вставки', () => {
    expect(pastedTextFile('Текст. '.repeat(1000))?.name).toBe('Вставка.txt');
    expect(pastedTextFile('Строка\r\n'.repeat(80))?.name).toBe('Вставка.txt');
  });
  it.each(['# Заголовок\n', '```python\nprint(1)\n```\n', '- Первый пункт\n', '**Важно**\n', '*Курсив*\n', '`код`\n', '[Ссылка](https://example.com)\n', '| A | B |\n| --- | --- |\n'])('распознаёт Markdown %s', (markup) => {
    const file = pastedTextFile(markup + 'Дополнение. '.repeat(600));
    expect(file?.name).toBe('Вставка.md');
    expect(file?.type).toBe('text/markdown;charset=utf-8');
  });
  it('не принимает незакрытые скобки и разделители за разметку', () => {
    expect(pastedTextFile('['.repeat(100000))?.name).toBe('Вставка.txt');
    expect(pastedTextFile('|'.repeat(100000))?.name).toBe('Вставка.txt');
  });
  it('возвращает исходник без обрезки и не стирает существующий запрос', () => {
    expect(appendPastedText('', '\n Исходник\r\n')).toBe('\n Исходник\r\n');
    expect(appendPastedText('Проверь это', 'Исходник')).toBe('Проверь это\n\nИсходник');
  });
});

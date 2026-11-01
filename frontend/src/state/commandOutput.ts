/* Живой вывод команд агента за текущий ход: хвост для карточки в ленте, пока у неё нет результата с сервера.
   После хода не нужен: вывод есть в самом результате. */
import { createStore, useStore } from '../lib/store';

const LIMIT = 12_000;
const CALLS = 20;

export const commandOutput = createStore<{ output: Record<string, string> }>({ output: {} });

export function appendCommandOutput(id: string, text: string): void {
  if (!id || !text) return;
  const { output } = commandOutput.get();
  const next = { ...output, [id]: ((output[id] ?? '') + text).slice(-LIMIT) };
  const keys = Object.keys(next);
  for (const key of keys.slice(0, Math.max(0, keys.length - CALLS))) delete next[key];
  commandOutput.set({ output: next });
}

export function clearCommandOutput(): void {
  if (Object.keys(commandOutput.get().output).length) commandOutput.set({ output: {} });
}

export const useCommandOutput = (id: string): string => useStore(commandOutput, (state) => state.output[id] ?? '');

/** Последняя непустая строка: подпись свёрнутого журнала показывает, что команда делает сейчас. */
export function lastOutputLine(text: string): string {
  const lines = text.replace(/\r(?!\n)/g, '\n').split('\n');
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const line = (lines[index] ?? '').trim();
    if (line) return line.length > 80 ? `${line.slice(0, 79)}…` : line;
  }
  return '';
}

/** Хвост как в терминале: возврат каретки перерисовывает строку (полосы прогресса), видны последние строки. */
export function terminalTail(text: string, lines = 40): string {
  return text
    .replace(/\r\n/g, '\n')
    .split('\n')
    .map((line) => line.slice(line.lastIndexOf('\r') + 1))
    .slice(-lines)
    .join('\n');
}

import { beforeEach, describe, expect, it } from 'vitest';
import { appendCommandOutput, clearCommandOutput, commandOutput, lastOutputLine, terminalTail } from './commandOutput';

describe('живой вывод команд', () => {
  beforeEach(() => clearCommandOutput());

  it('копит хвост по вызовам и держит только последние', () => {
    appendCommandOutput('a', 'раз\n');
    appendCommandOutput('a', 'два\n');
    expect(commandOutput.get().output.a).toBe('раз\nдва\n');
    appendCommandOutput('b', 'x'.repeat(20_000));
    expect(commandOutput.get().output.b).toHaveLength(12_000);
    for (let index = 0; index < 30; index += 1) appendCommandOutput(`call-${index}`, '.');
    expect(Object.keys(commandOutput.get().output)).toHaveLength(20);
    expect(commandOutput.get().output.a).toBeUndefined();
  });

  it('показывает хвост как терминал: прогресс перерисовывает строку', () => {
    expect(terminalTail('сборка\n10%\r50%\r100%\nготово')).toBe('сборка\n100%\nготово');
    expect(terminalTail(Array.from({ length: 50 }, (_, index) => String(index)).join('\n'), 3)).toBe('47\n48\n49');
    expect(lastOutputLine('первая\n  вторая  \n\n')).toBe('вторая');
    expect(lastOutputLine('загрузка 10%\rзагрузка 90%')).toBe('загрузка 90%');
    expect(lastOutputLine('я'.repeat(100))).toHaveLength(80);
  });
});

import { describe, expect, it } from 'vitest';
import { parseCommands } from './QualitySettings';

describe('parseCommands', () => {
  it('разбирает вид, папку и команду', () => {
    expect(parseCommands('test: npm run test:unit\n\ntypecheck @frontend: npm run typecheck')).toEqual([
      { kind: 'test', cwd: '', command: 'npm run test:unit' },
      { kind: 'typecheck', cwd: 'frontend', command: 'npm run typecheck' },
    ]);
  });

  it('отказывает на неизвестном виде с номером строки', () => {
    expect(() => parseCommands('test: ok\ndeploy: rm -rf /')).toThrow('Строка 2');
  });
});

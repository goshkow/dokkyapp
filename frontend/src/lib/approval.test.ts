import { describe, expect, it } from 'vitest';
import { summarizeApproval } from './approval';

describe('подтверждение действий', () => {
  it('показывает путь и объём без содержимого файла и JSON', () => {
    const result = summarizeApproval('file__write_patch', { path: 'src/app.ts', content: 'private code\n'.repeat(10000) });
    expect(result.title).toBe('Изменить файл');
    expect(result.scope).toEqual(['src/app.ts']);
    expect(JSON.stringify(result)).not.toContain('private code');
    expect(JSON.stringify(result).length).toBeLessThan(300);
  });
  it('перечисляет файлы патча без строк изменений', () => {
    const result = summarizeApproval('patch.apply', { patch: '*** Begin Patch\n*** Update File: app.ts\n+secret code\n*** Add File: test.ts\n*** End Patch' });
    expect(result.scope).toEqual(['app.ts', 'test.ts']);
    expect(JSON.stringify(result)).not.toContain('secret code');
  });
  it('показывает объём сценария и программу без тела команды', () => {
    const result = summarizeApproval('shell.run', { command: 'API_KEY=private npm run build\necho private\n'.repeat(15) });
    expect(result.details).toEqual(['Сценарий: 30 строк', 'Запуск: npm run']);
    expect(JSON.stringify(result)).not.toContain('private');
  });
  it('ссылки не раскрывают credentials и query, неизвестные аргументы не выводятся', () => {
    const result = summarizeApproval('browser.navigate', { url: 'https://user:password@example.com/a?token=private#secret', credentials: { key: 'private' } });
    expect(result.scope).toEqual(['https://example.com/a']);
    expect(JSON.stringify(result)).not.toMatch(/password|private|secret/);
  });
  it('соблюдает границы размера и допускает неверные аргументы', () => {
    expect(summarizeApproval('unknown', null).title).toBe('Выполнить действие инструмента');
    expect(summarizeApproval('file.create', { paths: Array.from({ length: 1000 }, (_, n) => `file-${n}`) }).scope).toHaveLength(100);
  });
});

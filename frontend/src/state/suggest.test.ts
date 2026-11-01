import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./surface', () => ({ startChat: vi.fn() }));

import { composer } from './composer';
import { fieldHost } from './field';
import { skillsStore } from './skills';
import { findToken } from '../lib/suggestToken';
import { liveToken, pick, projectCommands, rankFiles, suggestions } from './suggest';
import { view } from './view';
import { workspace } from './workspace';

describe('подсказки «/» и «@»', () => {
  beforeEach(() => {
    composer.set({ text: '', skills: [], agentMode: 'development' });
    fieldHost.set({ suggest: null, suggestIndex: 0, suggestCount: 0, place: 0, placeAt: 0 });
    skillsStore.set({ items: [], loaded: true, error: '' });
    projectCommands.set({ projectId: null, items: [] });
  });

  it('команда проекта вставляется в текст, аргументы - после неё', () => {
    view.set({ project: { id: 5, name: 'p', path: '/p' } as never });
    projectCommands.set({ projectId: 5, items: [{ name: 'fix-issue', description: 'Чинит задачу', argument_hint: '<номер>' }] });
    composer.set({ text: '/fix' });
    const token = { kind: '/' as const, query: 'fix', start: 0, end: 4 };
    const [item] = suggestions(token);
    expect(item).toMatchObject({ label: '/fix-issue', hint: '<номер> · Чинит задачу' });
    fieldHost.set({ suggest: token });
    pick(0);
    expect(composer.get().text).toBe('/fix-issue ');
    // команды другого проекта не предлагаются
    view.set({ project: { id: 6, name: 'q', path: '/q' } as never });
    expect(suggestions(token)).toEqual([]);
  });

  it('находит токен под курсором', () => {
    expect(findToken('/пл', 3)).toEqual({ kind: '/', query: 'пл', start: 0, end: 3 });
    expect(findToken('сделай /plan', 12)).toBeNull();
    expect(findToken('/plan сейчас', 12)).toBeNull();
    expect(findToken('почини @src/ap', 14)).toEqual({ kind: '@', query: 'src/ap', start: 7, end: 14 });
    expect(findToken('@', 1)).toEqual({ kind: '@', query: '', start: 0, end: 1 });
    expect(findToken('почта user@mail', 15)).toBeNull();
    expect(findToken('@a.py готово', 12)).toBeNull();
  });

  it('файлы: сначала имя с начала, потом внутри имени, потом путь', () => {
    const files = ['docs/app-notes.md', 'src/main.ts', 'src/app.ts', 'app/index.ts', 'lib/webapp.ts'];
    expect(rankFiles(files, 'app')).toEqual(['src/app.ts', 'docs/app-notes.md', 'lib/webapp.ts', 'app/index.ts']);
    expect(rankFiles(files, 'нет')).toEqual([]);
  });

  it('выбор файла вставляет путь и ставит курсор за ним', () => {
    workspace.set({ tree: { state: 'ready', files: ['src/app.ts', 'src/main.ts'], error: '', projectId: 1 } });
    composer.set({ text: 'почини @ma и проверь' });
    fieldHost.set({ suggest: { kind: '@', query: 'ma', start: 7, end: 10 } });
    pick(0);
    expect(composer.get().text).toBe('почини @src/main.ts и проверь');
    expect(fieldHost.get()).toMatchObject({ suggest: null, placeAt: 20, place: 1 });
    // в конце текста путь получает пробел, чтобы сразу писать дальше
    composer.set({ text: 'смотри @ap' });
    fieldHost.set({ suggest: { kind: '@', query: 'ap', start: 7, end: 10 } });
    pick(0);
    expect(composer.get().text).toBe('смотри @src/app.ts ');
    expect(fieldHost.get().placeAt).toBe(19);
  });

  it('навык подключается чипом, а токен убирается из текста', () => {
    skillsStore.set({ items: [
      { id: 3, name: 'Код-ревью', description: 'Проверить изменения', builtin: true, category: 'Разработка', workflow: {} },
      { id: 4, name: 'Выключенный', description: '', builtin: true, category: 'Разработка', enabled: false, workflow: {} },
    ], loaded: true, error: '' });
    composer.set({ text: '/код посмотри' });
    const token = { kind: '/' as const, query: 'код', start: 0, end: 4 };
    expect(suggestions(token).map((item) => item.label)).toEqual(['/Код-ревью']);
    fieldHost.set({ suggest: token });
    pick(0);
    expect(composer.get().skills).toEqual([{ id: 3, name: 'Код-ревью' }]);
    expect(composer.get().text).toBe('посмотри');
  });

  it('встроенные команды и устаревший токен', () => {
    composer.set({ text: '/pl' });
    fieldHost.set({ suggest: { kind: '/', query: 'pl', start: 0, end: 3 } });
    expect(suggestions(fieldHost.get().suggest).map((item) => item.label)).toEqual(['/plan']);
    pick(0);
    expect(composer.get().agentMode).toBe('plan');
    expect(composer.get().text).toBe('');
    // после отправки или смены чата старый токен уже не в тексте
    expect(liveToken({ kind: '@', query: 'a', start: 0, end: 2 }, 'другое')).toBeNull();
  });
});

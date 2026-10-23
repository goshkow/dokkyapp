import { describe, expect, it } from 'vitest';
import {
  actionText, duration, journalDuration, journalSegments, journalSummary, orderedJournalEntries, projectLocalJournal, type Message,
} from './journal';

const turn = { id: 7, status: 'completed', created_at: '2026-10-17T10:00:00+00:00', completed_at: '2026-10-17T10:02:13+00:00', timeline: [] };
const call = (id: string, name: string, path: string, sequence: number, time: string) => ({
  event: 'tool.call', sequence, created_at: time, data: { id, name, arguments: { path } },
});
const result = (id: string, name: string, path: string, sequence: number, time: string, extra: object = {}) => ({
  event: 'tool.result', sequence, created_at: time, data: { id, name, arguments: { path }, result: { ok: true, ...extra } },
});

const messages: Message[] = [
  { id: 1, role: 'user', content: 'вопрос', metadata: { turn_id: 7 } },
  {
    id: 2, role: 'assistant', content: 'Смотрю код.',
    metadata: {
      kind: 'activity', channel: 'commentary', status: 'completed', sequence: 2, turn, turn_id: 7,
      events: [
        call('a1', 'file.read', 'app.py', 3, '2026-10-17T10:00:10+00:00'),
        result('a1', 'file.read', 'app.py', 4, '2026-10-17T10:00:11+00:00'),
        call('a2', 'file.write_patch', 'app.py', 5, '2026-10-17T10:01:10+00:00'),
        result('a2', 'file.write_patch', 'app.py', 6, '2026-10-17T10:01:11+00:00', { added_lines: 4, removed_lines: 1 }),
      ],
    },
  },
  { id: 3, role: 'assistant', content: 'Готово.', metadata: { channel: 'final', status: 'completed', turn_id: 7, sequence: 9, turn } },
];

describe('journal', () => {
  it('форматирует длительность по-русски', () => {
    expect(duration(500)).toBe('1 сек.');
    expect(duration(133_000)).toBe('2 мин. 13 сек.');
  });

  it('собирает ход в журнал и отдельный ответ', () => {
    const feed = projectLocalJournal(messages);
    expect(feed.map((item) => item.metadata?.kind ?? item.role)).toEqual(['user', 'journal', 'assistant']);
    expect(feed[2]?.content).toBe('Готово.');
  });

  it('объединяет вызов и результат одного действия и считает правки', () => {
    const journal = projectLocalJournal(messages)[1] as Message;
    const entries = orderedJournalEntries(journal.metadata ?? {});
    expect(entries.filter((entry) => entry.type === 'action')).toHaveLength(2);
    const summary = journalSummary(entries);
    expect(summary).toMatchObject({ added: 4, removed: 1, edits: 1 });
    expect(summary.text).toBe('Прочитал 1 файл, изменил 1 файл');
  });

  it('делит работу на этапы вокруг комментариев модели', () => {
    const journal = projectLocalJournal(messages)[1] as Message;
    const blocks = journalSegments(orderedJournalEntries(journal.metadata ?? {}), journal.metadata ?? {});
    expect(blocks.map((block) => block.type)).toEqual(['commentary', 'segment']);
  });

  it('считает время работы без пауз на сжатие контекста', () => {
    expect(journalDuration(turn)).toBe('2 мин. 13 сек.');
    const paused = {
      ...turn,
      timeline: [
        { kind: 'context.compacting', created_at: '2026-10-17T10:00:30+00:00' },
        { kind: 'context.compacted', created_at: '2026-10-17T10:01:00+00:00' },
      ],
    };
    expect(journalDuration(paused)).toBe('1 мин. 43 сек.');
  });

  it('пишет действия понятным текстом и показывает ошибки', () => {
    expect(actionText({ event: 'tool.call', data: { name: 'file.read', arguments: { path: 'a.py' } } })).toBe('Читаю: a.py');
    expect(actionText({ event: 'tool.result', data: { name: 'shell.run', arguments: { command: 'ls' }, result: { ok: false } } })).toBe('Не выполнено: Выполнена команда: ls');
  });
});

describe('короткие подписи состояния', () => {
  it('длинные запросы и команды не попадают в строку состояния', async () => {
    const { conciseStatus, liveLabel } = await import('./journal');
    expect(conciseStatus('Ищу в проекте: QTimer|singleShot|setInterval|timeout.connect|threading.Thread')).toBe('Ищу в файлах проекта');
    expect(conciseStatus('Выполняю: python -m pytest tests/test_very_long_name.py -k something')).toBe('Выполняю команду');
    expect(conciseStatus('Редактирую: backend/koddi/desktop/web_host.py')).toBe('Редактирую desktop/web_host.py');
    expect(conciseStatus('Думаю…')).toBe('Думаю…');
    expect(liveLabel({ event: 'tool.call', data: { name: 'file.read', arguments: { path: ['C:', 'proj', 'src', 'app.py'].join(String.fromCharCode(92)) } } })).toBe('Читаю src/app.py');
    expect(liveLabel({ event: 'tool.call', data: { name: 'search.text', arguments: { query: 'a|b|c' } } })).toBe('Ищу в файлах проекта');
  });
});

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { actionText, liveLabel, type JournalEntry } from '../../lib/journal';
import { ComputerLive, computerState } from './Journal';

afterEach(cleanup);

const call = (position: number, args: Record<string, unknown>, result?: Record<string, unknown>): JournalEntry => ({
  key: `a${position}`, type: 'action', position,
  event: { event: result ? 'tool.result' : 'tool.call', data: { name: 'computer.use', arguments: args, ...(result ? { result } : {}) } },
} as unknown as JournalEntry);

describe('работа с приложением в ленте', () => {
  it('подписи команд по-русски и с названием приложения', () => {
    const data = { name: 'computer.use', arguments: { command: 'click', app: 'Telegram' } };
    expect(liveLabel({ event: 'tool.call', data })).toBe('Нажимаю в Telegram');
    expect(actionText({ event: 'tool.result', data: { ...data, result: { ok: true } } })).toBe('Нажато в Telegram');
    expect(actionText({ event: 'tool.call', data: { name: 'computer.use', arguments: { command: 'press', combo: 'cmd+c' } } })).toBe('Нажимаю cmd+c');
    expect(actionText({ event: 'tool.call', data: { name: 'computer.use', arguments: { command: 'launch', app: 'Notes' } } })).toBe('Открываю Notes');
  });

  it('у инструментов браузера, git и GitHub нет внутренних имён', () => {
    for (const name of ['browser.network', 'browser.eval', 'git.push', 'github.pr_list']) {
      expect(actionText({ event: 'tool.result', data: { name, arguments: {}, result: { ok: true } } })).not.toContain(name);
      expect(liveLabel({ event: 'tool.call', data: { name, arguments: {} } })).not.toContain(name);
    }
  });

  it('сводка берёт приложение, число шагов и свой снимок окна', () => {
    const state = computerState([
      call(1, { command: 'snapshot', app: 'Telegram' }, { ok: true }),
      call(2, { command: 'screenshot', app: 'Telegram' }, { ok: true, data: { image_url: '/api/attachments/7/content' } }),
      call(3, { command: 'type', app: 'Telegram', text: 'Салам' }),
    ]);
    expect(state).toEqual({ app: 'Telegram', steps: 3, current: 'Ввожу текст в Telegram', image: '/api/attachments/7/content' });
    // чужой адрес картинки не показывается
    expect(computerState([call(1, { command: 'screenshot' }, { ok: true, data: { image_url: 'https://evil.example/x.png' } })])?.image).toBe('');
    expect(computerState([{ key: 'x', type: 'status', text: 'Думаю', position: 1 } as unknown as JournalEntry])).toBeNull();
  });

  it('карточка показывает приложение, действие и остановку', () => {
    render(<ComputerLive state={{ app: 'Telegram', steps: 2, current: 'Нажимаю в Telegram', image: '/api/attachments/7/content' }} />);
    expect(screen.getByText('Докки работает в Telegram')).toBeTruthy();
    expect(screen.getByText('Нажимаю в Telegram · шагов: 2')).toBeTruthy();
    expect(screen.getByRole('img', { name: 'Окно Telegram' })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Остановить/ })).toBeTruthy();
  });
});

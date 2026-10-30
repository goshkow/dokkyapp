import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemorySettings } from './MemorySettings';

vi.mock('../../state/overlay', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }));

const reply = (value: unknown) => new Response(JSON.stringify(value));
const item = (id: number, content: string, source = 'auto') => ({ id, content, source, created_at: '' });

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('память о пользователе и проекте', () => {
  it('показывает оба раздела и добавляет пункт в память проекта', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(reply({ enabled: true, user: [item(1, 'Пишет на TypeScript')], project: [] }))
      .mockResolvedValueOnce(reply({ enabled: true, user: [item(1, 'Пишет на TypeScript')], project: [item(2, 'Лицензия GPL-3.0', 'manual')] }));
    vi.stubGlobal('fetch', fetch);
    const say = vi.fn();
    render(<MemorySettings project={{ id: 7, name: 'Zorya' }} say={say} />);
    expect(await screen.findByText('Пишет на TypeScript')).toBeTruthy();
    expect(fetch.mock.calls[0]?.[0]).toBe('/api/memory?project_id=7');
    const field = screen.getByLabelText('Добавить в память: О проекте «Zorya»');
    fireEvent.change(field, { target: { value: 'Лицензия GPL-3.0' } });
    fireEvent.keyDown(field, { key: 'Enter' });
    expect(await screen.findByText('Лицензия GPL-3.0')).toBeTruthy();
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body))).toEqual({ scope: 'project', project_id: 7, content: 'Лицензия GPL-3.0' });
    await waitFor(() => expect(say).toHaveBeenCalledWith('Добавлено'));
  });

  it('без проекта есть только память о пользователе, пункт удаляется', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(reply({ enabled: true, user: [item(1, 'Комментарии по-русски')], project: [] }))
      .mockResolvedValueOnce(reply({ enabled: true, user: [], project: [] }));
    vi.stubGlobal('fetch', fetch);
    render(<MemorySettings project={null} say={vi.fn()} />);
    fireEvent.click(await screen.findByLabelText('Удалить: Комментарии по-русски'));
    await waitFor(() => expect(screen.queryByText('Комментарии по-русски')).toBeNull());
    expect(fetch.mock.calls[1]?.[0]).toBe('/api/memory/1');
    expect(fetch.mock.calls[1]?.[1]?.method).toBe('DELETE');
    expect(screen.queryByText(/О проекте/)).toBeNull();
  });
});

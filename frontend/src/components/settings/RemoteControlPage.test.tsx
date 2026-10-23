import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { remote, setChatShared, sharedOnPhone, type RemoteStatus } from '../../state/remoteControl';
import { RemoteControlPage } from './RemoteControlPage';

vi.mock('../../state/data', () => ({ useData: (select: (state: { projects: unknown[] }) => unknown) => select({ projects: [{ id: 3, name: 'Сайт', path: '/p' }] }) }));
vi.mock('../../state/overlay', () => ({ guarded: (fn: () => unknown) => fn }));
vi.mock('../../lib/external', () => ({ openExternal: vi.fn() }));
vi.mock('../ui/Select', () => ({ Select: ({ label }: { label: string }) => <span>{label}</span> }));

const status = (patch: Partial<RemoteStatus> = {}): RemoteStatus => ({
  enabled: true, state: 'online', message: '', name: 'MacBook', prevent_sleep: 'working', prevent_sleep_active: false,
  power: 'ac', pending: 0, new_session_project_id: null, shared: { '5': true, '6': false }, site: 'https://example.com', ...patch,
});
const reply = (value: unknown) => new Response(JSON.stringify(value));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); remote.set({ status: null }); });

describe('доступ с телефона', () => {
  it('видимым считается только переданный чат при включённом доступе', () => {
    expect(sharedOnPhone(status(), 5)).toBe(true);
    expect(sharedOnPhone(status(), 6)).toBe(false);
    expect(sharedOnPhone(status(), 7)).toBe(false);
    expect(sharedOnPhone(status({ enabled: false }), 5)).toBe(false);
    expect(sharedOnPhone(null, 5)).toBe(false);
  });

  it('переключение чата берёт итог из ответа сервера', async () => {
    remote.set({ status: status() });
    const fetch = vi.fn().mockResolvedValue(reply({ chat_id: 6, shared: true }));
    vi.stubGlobal('fetch', fetch);
    await setChatShared(6, true);
    expect(fetch.mock.calls[0]?.[0]).toBe('/api/chats/6/remote');
    expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body))).toEqual({ shared: true });
    expect(remote.get().status?.shared['6']).toBe(true);
  });

  it('после отзыва в вебе предлагает подключить снова', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(reply(status({ state: 'revoked' })))
      .mockResolvedValue(reply(status()));
    vi.stubGlobal('fetch', fetch);
    render(<RemoteControlPage say={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Подключить снова' }));
    await waitFor(() => expect(fetch.mock.calls.some(([path, init]) => path === '/api/remote-control/reconnect' && init?.method === 'POST')).toBe(true));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Подключить снова' })).toBeNull());
    expect(screen.getByText('MacBook')).toBeTruthy();
  });

  it('показывает ошибку загрузки и повторяет запрос', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(reply(status({ enabled: false, state: 'off' }))));
    render(<RemoteControlPage say={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Повторить' }));
    expect((await screen.findByRole('switch', { name: 'Доступ с телефона' })).getAttribute('aria-checked')).toBe('false');
    expect(screen.queryByText('MacBook')).toBeNull();
  });
});

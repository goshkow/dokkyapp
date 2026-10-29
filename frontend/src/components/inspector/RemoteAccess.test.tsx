import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { remote, type RemoteStatus } from '../../state/remoteControl';
import { RemoteAccess } from './RemoteAccess';

globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;

const put = vi.fn();
vi.mock('../../lib/api', () => ({ api: vi.fn(() => new Promise(() => undefined)), post: vi.fn(), put: (...args: unknown[]) => put(...args) }));
vi.mock('../../state/overlay', () => ({ guarded: (fn: () => unknown) => fn }));
vi.mock('../../state/view', () => ({ useView: (select: (state: { chat: { id: number } }) => unknown) => select({ chat: { id: 5 } }) }));

const status = (patch: Partial<RemoteStatus> = {}): RemoteStatus => ({
  enabled: true, state: 'online', message: '', name: 'MacBook', prevent_sleep: 'working', prevent_sleep_active: false,
  power: 'ac', pending: 0, new_session_project_id: null, share_new: true, shared: { '5': true }, site: 'https://example.com', ...patch,
});

describe('Remote access в «Среде»', () => {
  beforeEach(() => { vi.useFakeTimers(); put.mockReset(); });
  afterEach(() => { cleanup(); vi.useRealTimers(); remote.set({ status: null }); });

  it('переключатель скрывает чат с других устройств и показывает обратно', async () => {
    remote.set({ status: status() });
    put.mockResolvedValue({ shared: false });
    render(<RemoteAccess />);
    const toggle = screen.getByRole('switch', { name: 'Remote access' });
    expect(toggle.getAttribute('aria-checked')).toBe('true');
    await act(async () => { fireEvent.click(toggle); });
    expect(put).toHaveBeenCalledWith('/api/chats/5/remote', { shared: false });
    expect(toggle.getAttribute('aria-checked')).toBe('false');
  });

  it('по наведению объясняет, что это; без доступа переключатель недоступен', () => {
    remote.set({ status: status({ enabled: false }) });
    const { container } = render(<RemoteAccess />);
    expect(screen.getByRole('switch', { name: 'Remote access' })).toHaveProperty('disabled', true);
    fireEvent.mouseEnter(container.querySelector('.environment-remote') as Element);
    act(() => { vi.advanceTimersByTime(400); });
    expect(screen.getByRole('tooltip', { hidden: true }).textContent).toContain('Доступ с телефона');
  });
});

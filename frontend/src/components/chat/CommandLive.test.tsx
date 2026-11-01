import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const network = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock('../../lib/api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../lib/api')>()), post: network.post }));

import { ApiError } from '../../lib/api';
import { chat } from '../../state/chatCore';
import { appendCommandOutput, clearCommandOutput } from '../../state/commandOutput';
import { view } from '../../state/view';
import { CommandLive } from './Journal';

afterEach(() => { cleanup(); clearCommandOutput(); network.post.mockReset(); chat.set({ running: false, turnEnded: false }); });

describe('идущая команда в ленте', () => {
  it('показывает живой хвост и останавливает только эту команду', async () => {
    view.set({ chat: { id: 12, title: 'Чат' } as never });
    chat.set({ running: true, turnEnded: false });
    network.post.mockResolvedValue({ ok: true });
    render(<CommandLive id="call-7" />);
    act(() => appendCommandOutput('call-7', 'npm test\n✓ 12 тестов\n'));
    expect(screen.getByText(/✓ 12 тестов/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Остановить/ })); });
    expect(network.post).toHaveBeenCalledWith('/api/chats/12/tools/call-7/stop');
    expect(screen.getByRole('button', { name: /Останавливаю/ }).hasAttribute('disabled')).toBe(true);
  });

  it('команда, закончившаяся раньше нажатия, не даёт ошибки', async () => {
    view.set({ chat: { id: 12, title: 'Чат' } as never });
    chat.set({ running: true, turnEnded: false });
    network.post.mockRejectedValue(new ApiError('Команда уже завершилась', 404));
    render(<CommandLive id="call-8" />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Остановить/ })); });
    expect(document.querySelector('.toast, [role="alert"]')).toBeNull();
  });

  it('в законченном ходе кнопки нет', () => {
    chat.set({ running: false, turnEnded: true });
    const { container } = render(<CommandLive id="call-9" />);
    expect(container.innerHTML).toBe('');
  });
});

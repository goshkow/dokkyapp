import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { recoveryKind, turnErrorCode, untilText } from '../../lib/recovery';
import { retryStatus } from '../../state/chatStream';

const state = vi.hoisted(() => ({
  chat: { messages: [] as unknown[], running: false, load: 'ready' },
  limits: { value: null as Record<string, unknown> | null },
}));
const resumeChat = vi.hoisted(() => vi.fn(async () => false));
const announce = vi.hoisted(() => vi.fn());

vi.mock('../../state/chatCore', async (importOriginal) => ({ ...(await importOriginal<object>()), useChat: (select: (value: unknown) => unknown) => select(state.chat) }));
vi.mock('../../state/view', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useView: (select: (value: unknown) => unknown) => select({ chat: { id: 7, title: 'Задача' } }),
}));
vi.mock('../../state/limits', async (importOriginal) => ({ ...(await importOriginal<object>()), useLimits: (select: (value: unknown) => unknown) => select(state.limits), limitsStore: { get: () => state.limits }, loadLimits: vi.fn(async () => undefined) }));
vi.mock('../../state/chat', async (importOriginal) => ({ ...(await importOriginal<object>()), resumeChat }));
vi.mock('../../state/overlay', async (importOriginal) => ({ ...(await importOriginal<object>()), guarded: (run: () => unknown) => run }));
vi.mock('../../lib/bridge', async (importOriginal) => ({ ...(await importOriginal<object>()), announceAgentNotice: announce }));

const { RecoveryBanner, lastFailure, resetAutoResume } = await import('./RecoveryBanner');

const limits = (patch: Record<string, unknown>) => ({
  windowRemaining: 0, windowResetsAt: null, windowHours: 5, remaining: 80, expiresAt: null, plan: 'super', trial: false, paidActive: true,
  usageExhausted: false, catalogVersion: null, usageReset: null, developer: { allowed: true, reason: '', message: '' }, ...patch,
});

const failed = (code: string, id = 'turn-1') => ({
  role: 'assistant', content: '', metadata: { turn: { id, status: 'failed', error_json: JSON.stringify({ code }) } },
});

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
  vi.setSystemTime(new Date('2026-11-05T10:00:00Z'));
  localStorage.clear();
  resumeChat.mockClear();
  announce.mockClear();
  resetAutoResume();
  state.chat = { messages: [], running: false, load: 'ready' };
  state.limits = { value: null };
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('повторы и плашки после оборванного хода', () => {
  it('строка состояния считает повторы и секунды', () => {
    const now = Date.now();
    expect(retryStatus({ attempt: 2, total: 6, until: now + 7_400 } as never, now)).toBe('Докки не отвечает · повтор 2 из 6 через 8 с');
    expect(retryStatus({ attempt: 6, total: 6, until: now - 1 } as never, now)).toBe('Докки не отвечает · повтор 6 из 6…');
  });

  it('коды ошибок делятся на сбой и лимит, остальное без плашки', () => {
    expect(recoveryKind('ai_unavailable')).toBe('stalled');
    expect(recoveryKind('window_exhausted')).toBe('limit');
    expect(recoveryKind('forbidden')).toBeNull();
    expect(turnErrorCode('{"code":"quota_exceeded"}')).toBe('quota_exceeded');
    expect(turnErrorCode('не json')).toBe('');
    expect(turnErrorCode(null)).toBe('');
    const now = Date.now();
    expect(untilText(now + 30_000, now)).toBe('через минуту');
    expect(untilText(now + 40 * 60_000, now)).toBe('через 40 мин');
    expect(untilText(now + 135 * 60_000, now)).toBe('через 2 ч 15 мин');
    expect(untilText(now + 50 * 3_600_000, now)).toBe('через 2 дн');
  });

  it('плашка только у последнего хода, и только если он упал с известной причиной', () => {
    expect(lastFailure([failed('ai_unavailable')] as never)).toEqual({ kind: 'stalled', turnId: 'turn-1' });
    const done = { role: 'assistant', content: 'ok', metadata: { turn: { id: 'turn-2', status: 'completed' } } };
    expect(lastFailure([failed('ai_unavailable'), done] as never)).toBeNull();
    expect(lastFailure([failed('forbidden')] as never)).toBeNull();
    expect(lastFailure([] as never)).toBeNull();
  });

  it('«Повторить попытку» прячет плашку и продолжает ход', async () => {
    state.chat = { messages: [failed('ai_unavailable')], running: false, load: 'ready' };
    render(<RecoveryBanner />);
    expect(screen.getByText('Докки временно не отвечает')).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Повторить попытку' })); });
    expect(resumeChat).toHaveBeenCalledWith(7);
    // ход не начался (чат сменился): плашка остаётся, можно нажать снова
    expect(screen.getByRole('button', { name: 'Повторить попытку' })).toBeTruthy();
  });

  it('пока идёт ход, плашки нет', () => {
    state.chat = { messages: [failed('ai_unavailable')], running: true, load: 'ready' };
    render(<RecoveryBanner />);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('«Продолжить работу» включается только когда лимит восстановился', async () => {
    state.chat = { messages: [failed('window_exhausted')], running: false, load: 'ready' };
    state.limits = { value: limits({ windowResetsAt: new Date(Date.now() + 20 * 60_000).toISOString() }) };
    render(<RecoveryBanner />);
    const button = screen.getByRole('button', { name: 'Продолжить работу' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByText(/через 20 мин/)).toBeTruthy();
    await act(async () => { vi.advanceTimersByTime(21 * 60_000); });
    expect(button.disabled).toBe(false);
    expect(resumeChat).not.toHaveBeenCalled();
    await act(async () => { fireEvent.click(button); });
    expect(resumeChat).toHaveBeenCalledTimes(1);
  });

  it('с галочкой работа продолжается сама и приходит уведомление', async () => {
    state.chat = { messages: [failed('quota_exceeded')], running: false, load: 'ready' };
    state.limits = { value: limits({ windowResetsAt: new Date(Date.now() + 60_000).toISOString() }) };
    render(<RecoveryBanner />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Продолжить автоматически' }));
    expect(localStorage.getItem('dokky.autoResume')).toBe('1');
    expect(resumeChat).not.toHaveBeenCalled();
    await act(async () => { vi.advanceTimersByTime(2 * 60_000); });
    expect(resumeChat).toHaveBeenCalledTimes(1);
    expect(announce).toHaveBeenCalledWith(7, 'limit', 'Задача');
  });

  it('после сброса окна с новым окном впереди кнопка всё равно включена', () => {
    state.chat = { messages: [failed('quota_exceeded')], running: false, load: 'ready' };
    state.limits = { value: limits({ windowRemaining: 100, windowResetsAt: new Date(Date.now() + 5 * 3_600_000).toISOString() }) };
    render(<RecoveryBanner />);
    expect((screen.getByRole('button', { name: 'Продолжить работу' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('кончилась вся подписка: время не поможет, автопродолжения нет', () => {
    state.chat = { messages: [failed('quota_exceeded')], running: false, load: 'ready' };
    state.limits = { value: limits({ windowRemaining: 100, remaining: 0, usageExhausted: true }) };
    render(<RecoveryBanner />);
    expect((screen.getByRole('button', { name: 'Продолжить работу' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.getByText(/Использование по подписке закончилось/)).toBeTruthy();
  });

  it('автопродолжение не идёт по кругу, если сервер снова отказал', async () => {
    localStorage.setItem('dokky.autoResume', '1');
    state.chat = { messages: [failed('quota_exceeded', 'turn-1')], running: false, load: 'ready' };
    state.limits = { value: limits({ windowRemaining: 50 }) };
    const view = render(<RecoveryBanner />);
    await act(async () => { await Promise.resolve(); });
    expect(resumeChat).toHaveBeenCalledTimes(1);
    state.chat = { messages: [failed('quota_exceeded', 'turn-2')], running: false, load: 'ready' };
    view.rerender(<RecoveryBanner />);
    await act(async () => { await Promise.resolve(); });
    expect(resumeChat).toHaveBeenCalledTimes(1);
  });

  it('пока грузится другой чат, плашки нет', () => {
    state.chat = { messages: [failed('ai_unavailable')], running: false, load: 'loading' };
    render(<RecoveryBanner />);
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

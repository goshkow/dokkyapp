import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Limits } from '../lib/types';
import { limitsStore } from './limits';
import { startTray, trayPayload } from './tray';

const limits = (patch: Partial<Limits> = {}): Limits => ({
  windowRemaining: 80, windowResetsAt: '2026-10-26T15:00:00Z', windowHours: 5, remaining: 72, expiresAt: '2026-11-08T00:00:00Z',
  plan: 'super', trial: false, paidActive: true, usageExhausted: false, catalogVersion: null, usageReset: null,
  developer: { allowed: true, reason: '', message: '' }, ...patch,
});

describe('лимиты для меню трея', () => {
  afterEach(() => {
    delete window.koddiWindowBridge;
    limitsStore.set({ value: null });
  });

  it('берёт проценты и время сброса из подписки', () => {
    expect(trayPayload(limits(), null)).toEqual({
      plan: 'Super',
      window: { remaining: 80, hours: 5, resets_at: Date.parse('2026-10-26T15:00:00Z') / 1000 },
      total: { remaining: 72, resets_at: Date.parse('2026-11-08T00:00:00Z') / 1000 },
    });
  });

  it('скрывает неизвестные значения, проценты Free и всё без входа', () => {
    expect(trayPayload(limits({ windowRemaining: null, expiresAt: 'не дата' }), null)).toMatchObject({ window: null, total: { remaining: 72, resets_at: null } });
    expect(trayPayload(limits({ plan: 'free' }), null)).toEqual({ plan: 'Free', window: null, total: null });
    expect(trayPayload(null, null)).toEqual({ plan: null, window: null, total: null });
  });

  it('шлёт хосту только изменения', () => {
    const tray = vi.fn();
    window.koddiWindowBridge = { tray, command: vi.fn(), adjustWidth: vi.fn() };
    startTray();
    limitsStore.set({ value: limits() });
    limitsStore.set({ loading: true });
    limitsStore.set({ value: limits({ windowRemaining: 40 }) });
    const sent = tray.mock.calls.map(([payload]) => JSON.parse(payload).window?.remaining ?? null);
    expect(sent).toEqual([null, 80, 40]);
  });
});

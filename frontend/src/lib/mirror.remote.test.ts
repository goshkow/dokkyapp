import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => {
  vi.resetModules();
  window.history.replaceState(null, '', '/');
  delete window.koddiWindowBridge;
});

describe('действие из плитки', () => {
  it('событие щелчка не уходит в другой вид: оно циклическое, а JSON.stringify на нём падал', async () => {
    window.history.replaceState(null, '', '/?role=sidebar');
    const sent: string[] = [];
    window.koddiWindowBridge = { adjustWidth: vi.fn(), command: vi.fn(), relay: (payload: string) => { sent.push(payload); } };
    const { remote } = await import('./mirror');
    const action = remote('restartForUpdate', (force?: boolean) => force);
    const event = new MouseEvent('click') as MouseEvent & { self?: unknown };
    event.self = event;
    const synthetic: Record<string, unknown> = { nativeEvent: event };
    synthetic.self = synthetic;
    void action(event as never);
    void action(synthetic as never);
    void action(true);
    const calls = sent.map((text) => JSON.parse(text) as { k: string; f?: string; a?: unknown[] }).filter((item) => item.k === 'call');
    expect(calls.map((item) => item.a)).toEqual([[], [], [true]]);
  });
});

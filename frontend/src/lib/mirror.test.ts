import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => { window.history.replaceState({}, '', '/'); delete window.koddiWindowBridge; vi.useRealTimers(); vi.resetModules(); });

describe('готовность нативной плитки', () => {
  it('показывает плитку, когда снимок пришёл раньше подписки React', async () => {
    vi.resetModules(); vi.useFakeTimers();
    window.history.replaceState({}, '', '/?role=sidebar');
    const relay = vi.fn();
    window.koddiWindowBridge = { relay } as unknown as Window['koddiWindowBridge'];
    const { mirrorStore, startMirror } = await import('./mirror');
    const { createStore } = await import('./store');
    const state = createStore({ name: '' });
    mirrorStore('data', state);
    window.koddiRelayIn!(JSON.stringify({ k: 'state', n: 'data', s: { name: 'Докки' } }));
    const ready = vi.fn();
    const cleanup = startMirror(ready);
    expect(ready).toHaveBeenCalledOnce();
    expect(state.get().name).toBe('Докки');
    expect(relay).toHaveBeenCalled();
    cleanup?.();
    const count = relay.mock.calls.length;
    vi.advanceTimersByTime(1200);
    expect(relay.mock.calls).toHaveLength(count);
  });
  it('повторный effect после cleanup не теряет ожидание первого снимка', async () => {
    vi.resetModules(); vi.useFakeTimers();
    window.history.replaceState({}, '', '/?role=inspector');
    window.koddiWindowBridge = { relay: vi.fn() } as unknown as Window['koddiWindowBridge'];
    const { startMirror } = await import('./mirror');
    const abandoned = vi.fn();
    startMirror(abandoned)?.();
    const ready = vi.fn();
    const cleanup = startMirror(ready);
    window.koddiRelayIn!(JSON.stringify({ k: 'state', n: 'data', s: {} }));
    expect(ready).toHaveBeenCalledOnce();
    expect(abandoned).not.toHaveBeenCalled();
    cleanup?.();
  });
});

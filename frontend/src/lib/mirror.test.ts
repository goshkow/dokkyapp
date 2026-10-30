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

describe('щелчок мимо меню в другом виде', () => {
  it('нажатие в плитке рассылается остальным видам, а пришедшее закрывает меню этого вида', async () => {
    vi.resetModules();
    window.history.replaceState({}, '', '/?role=sidebar');
    const relay = vi.fn();
    window.koddiWindowBridge = { relay } as unknown as Window['koddiWindowBridge'];
    const { OUTSIDE_POINTER } = await import('./mirror');
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(relay.mock.calls.map(([payload]) => JSON.parse(payload as string))).toContainEqual({ k: 'touch', from: 'sidebar' });
    const outside = vi.fn();
    window.addEventListener(OUTSIDE_POINTER, outside);
    // своё же нажатие не возвращается, чужое - закрывает меню
    window.koddiRelayIn!(JSON.stringify({ k: 'touch', from: 'sidebar' }));
    expect(outside).not.toHaveBeenCalled();
    window.koddiRelayIn!(JSON.stringify({ k: 'touch', from: 'main' }));
    expect(outside).toHaveBeenCalledOnce();
    window.removeEventListener(OUTSIDE_POINTER, outside);
  });

  it('в обычном окне без плиток нажатия никуда не уходят', async () => {
    vi.resetModules();
    const relay = vi.fn();
    window.koddiWindowBridge = { relay } as unknown as Window['koddiWindowBridge'];
    await import('./mirror');
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    // слушатели модулей прошлых тестов (роли плиток) тоже слышат нажатие: главный вид сам не шлёт ничего
    expect(relay.mock.calls.map(([payload]) => JSON.parse(payload as string))).not.toContainEqual({ k: 'touch', from: 'main' });
  });
});

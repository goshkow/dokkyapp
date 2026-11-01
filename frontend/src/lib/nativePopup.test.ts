import { afterEach, describe, expect, it, vi } from 'vitest';
import { applyAction, nodeAt, pathOf } from './nativePopup';

function panel(): HTMLDivElement {
  const root = document.createElement('div');
  root.innerHTML = '<button id="a"></button><div><button id="b"><span id="c"></span></button></div><input id="d" />';
  return root;
}

describe('окно меню', () => {
  it('адрес узла одинаков в разметке и её копии', () => {
    const source = panel();
    const copy = panel();
    const path = pathOf(copy, copy.querySelector('#c')!);
    expect(path).toEqual([1, 0, 0]);
    expect(nodeAt(source, path)).toBe(source.querySelector('#c'));
    expect(nodeAt(source, [9])).toBeNull();
  });

  it('щелчок из окна доходит до настоящего пункта и всплывает', () => {
    const source = panel();
    const clicked = vi.fn();
    source.addEventListener('click', (event) => clicked((event.target as Element).id));
    applyAction(source, { id: 1, type: 'click', path: [1, 0, 0] });
    expect(clicked).toHaveBeenCalledWith('c');
  });

  it('ввод ставит значение и шлёт событие, Enter приходит клавишей', () => {
    const source = panel();
    const seen: string[] = [];
    source.addEventListener('input', (event) => seen.push((event.target as HTMLInputElement).value));
    source.addEventListener('keydown', (event) => seen.push(event.key));
    applyAction(source, { id: 1, type: 'input', path: [2], value: 'rel' });
    applyAction(source, { id: 1, type: 'key', path: [2], key: 'Enter' });
    expect(seen).toEqual(['rel', 'Enter']);
  });

  it('устаревший адрес ничего не ломает', () => {
    expect(() => applyAction(panel(), { id: 1, type: 'click', path: [7, 7] })).not.toThrow();
  });
});

describe('сессия окна меню', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.resetModules();
    delete (window as unknown as Record<string, unknown>).koddiWindowBridge;
  });

  async function start() {
    vi.useFakeTimers();
    vi.resetModules();
    const bridge = { relay: vi.fn(), popupOpen: vi.fn(), popupClose: vi.fn(), focusView: vi.fn() };
    (window as unknown as Record<string, unknown>).koddiWindowBridge = bridge;
    const module = await import('./nativePopup');
    const options = {
      panel: document.createElement('div'), rect: { x: 0, y: 0, w: 10, h: 10 }, side: 'top', align: 'start', minWidth: 0,
      className: '', role: 'menu', trigger: null, closeFirst: false, onClose: vi.fn(), onUnavailable: vi.fn(),
    };
    return { bridge, module, options };
  }

  const dismissed = (id: number, reason: string) => window.koddiRelayIn?.(JSON.stringify({ k: 'call', id: 1, to: 'main', from: 'popup', f: 'popupDismissed', a: [id, reason] }));

  it('щелчок мимо меню не забирает клавиатуру обратно', async () => {
    const { bridge, module, options } = await start();
    const close = module.openNativePopup(options);
    dismissed(1, 'blur');
    expect(options.onClose).toHaveBeenCalled();
    close();
    expect(bridge.popupClose).toHaveBeenCalled();
    expect(bridge.focusView).not.toHaveBeenCalled();
  });

  it('обычное закрытие возвращает клавиатуру виду', async () => {
    const { bridge, module, options } = await start();
    module.openNativePopup(options)();
    expect(bridge.focusView).toHaveBeenCalledWith('main');
  });

  it('окно не ответило: меню на странице, новая попытка позже', async () => {
    const { module, options } = await start();
    module.openNativePopup(options);
    expect(module.nativePopupPossible()).toBe(true);
    vi.advanceTimersByTime(500);
    expect(options.onUnavailable).toHaveBeenCalled();
    expect(module.nativePopupPossible()).toBe(false);
    vi.advanceTimersByTime(30_000);
    expect(module.nativePopupPossible()).toBe(true);
  });
});

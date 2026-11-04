import source from '../public/desktop-window.js?raw';
import { expect, test } from 'vitest';

function boot(search: string) {
  const calls: string[] = [];
  const bridge = { command: (action: string) => calls.push(action), frameChanged: { connect() {} }, requestState() {} };
  (window as any).qt = { webChannelTransport: {} };
  (window as any).QWebChannel = function (_transport: unknown, ready: (channel: any) => void) { ready({ objects: { koddiWindow: bridge } }); };
  window.history.replaceState({}, '', `/ui/${search}`);
  document.documentElement.className = 'desktop-window';
  document.body.innerHTML = '<p id="t">текст</p>';
  new Function(source)();
  return { bridge, calls, target: document.getElementById('t')! };
}

const press = (target: Element, type: string) => target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, button: 0, clientY: 10 }));

// слушатели висят на document и копятся между запусками, поэтому у каждого запуска свой мост и свой журнал
test('в окне меню нет кнопок окна и верхняя полоса его не двигает', () => {
  let bridgeReady = false;
  window.addEventListener('koddi-bridge', () => { bridgeReady = true; }, { once: true });
  const { bridge, calls, target } = boot('?role=popup');
  expect(document.querySelector('.desktop-window-controls')).toBeNull();
  expect(document.documentElement.dataset.captionMode).toBeUndefined();
  for (const type of ['mousedown', 'pointerdown', 'dblclick']) press(target, type);
  expect(calls).toEqual([]);
  expect((window as any).koddiWindowBridge).toBe(bridge);
  expect(bridgeReady).toBe(true);
});

test('в главном окне Windows кнопки и перетаскивание на месте', () => {
  for (const search of ['', '?role=main']) {
    const { calls, target } = boot(search);
    const actions = [...document.querySelectorAll<HTMLElement>('.desktop-window-controls button')].map((button) => button.dataset.windowAction);
    expect(actions).toEqual(['minimize', 'maximize', 'close']);
    press(target, 'pointerdown');
    press(target, 'dblclick');
    expect(calls).toEqual(['move', 'maximize']);
    document.querySelector('.desktop-window-controls')?.remove();
    delete document.documentElement.dataset.captionMode;
  }
});

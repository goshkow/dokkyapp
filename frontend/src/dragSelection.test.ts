import source from '../public/desktop-window.js?raw';
import { expect, test } from 'vitest';
test('mousedown в верхней полосе не начинает выделение', () => {
  const calls: string[] = [];
  const bridge = { command: (a: string) => calls.push(a), frameChanged: { connect() {} }, requestState() {} };
  (window as any).qt = { webChannelTransport: {} };
  (window as any).QWebChannel = function (_t: unknown, cb: (c: any) => void) { cb({ objects: { koddiWindow: bridge } }); };
  document.body.innerHTML = '<p id="t">текст</p><button id="b">x</button>';
  new Function(source)();
  const down = (target: Element, y: number) => { const e = new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0, clientY: y }); target.dispatchEvent(e); return e.defaultPrevented; };
  expect(down(document.getElementById('t')!, 10)).toBe(true);
  expect(down(document.getElementById('t')!, 200)).toBe(false);
  expect(down(document.getElementById('b')!, 10)).toBe(false);
});

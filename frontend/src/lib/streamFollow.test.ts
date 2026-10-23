import { describe, expect, it } from 'vitest';
import { createFollower } from './streamFollow';

globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
globalThis.matchMedia ??= (() => ({ matches: false })) as unknown as typeof matchMedia;

function feed(top: number) {
  const el = document.createElement('div');
  let scrollTop = top;
  Object.defineProperty(el, 'scrollHeight', { value: 3000 });
  Object.defineProperty(el, 'clientHeight', { value: 500 });
  Object.defineProperty(el, 'scrollTop', { get: () => scrollTop, set: (v: number) => { scrollTop = v; }, configurable: true });
  return el;
}
const frames = () => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));

describe('лента у конца', () => {
  it('жест вверх не возвращает ленту к концу, пока прокрутка не успела сдвинуться', async () => {
    const el = feed(2495);
    createFollower({ scroller: el, isRunning: () => false, target: () => null, topInset: () => 0 });
    el.dispatchEvent(new WheelEvent('wheel', { deltaY: -4 }));
    await frames();
    expect(el.scrollTop).toBe(2495);
  });

  it('жест вниз у конца включает слежение снова', async () => {
    const el = feed(2400);
    const follower = createFollower({ scroller: el, isRunning: () => false, target: () => null, topInset: () => 0 });
    el.dispatchEvent(new WheelEvent('wheel', { deltaY: -4 }));
    el.scrollTop = 2300;
    el.dispatchEvent(new Event('scroll'));
    await frames();
    expect(follower.enabled()).toBe(false);
    el.dispatchEvent(new WheelEvent('wheel', { deltaY: 40 }));
    el.scrollTop = 2500;
    el.dispatchEvent(new Event('scroll'));
    await frames();
    expect(follower.enabled()).toBe(true);
  });
});

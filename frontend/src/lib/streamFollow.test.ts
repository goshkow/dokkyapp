import { describe, expect, it } from 'vitest';
import { createFollower } from './streamFollow';

globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
globalThis.matchMedia ??= (() => ({ matches: false })) as unknown as typeof matchMedia;

function feed(top: number, writes: number[] = []) {
  const el = document.createElement('div');
  let scrollTop = top;
  Object.defineProperty(el, 'scrollHeight', { value: 3000 });
  Object.defineProperty(el, 'clientHeight', { value: 500 });
  Object.defineProperty(el, 'scrollTop', { get: () => scrollTop, set: (v: number) => { writes.push(v); scrollTop = v; }, configurable: true });
  return el;
}
// начало ответа на 1000 px от верха содержимого
function answer(el: HTMLElement) {
  const node = document.createElement('div');
  node.getBoundingClientRect = () => ({ top: 1000 - el.scrollTop }) as DOMRect;
  return node;
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

  it('лишняя прокрутка вниз у конца не двигает ленту', async () => {
    const writes: number[] = [];
    const el = feed(2500, writes);
    const follower = createFollower({ scroller: el, isRunning: () => false, target: () => null, topInset: () => 0 });
    for (let i = 0; i < 5; i += 1) {
      el.dispatchEvent(new WheelEvent('wheel', { deltaY: 30 }));
      el.dispatchEvent(new Event('scroll'));
      await frames();
    }
    expect(writes).toEqual([]);
    expect(follower.enabled()).toBe(true);
  });

  it('возврат к концу посреди ответа идёт за концом, а не к началу ответа', async () => {
    const el = feed(2500);
    const target = answer(el);
    const follower = createFollower({ scroller: el, isRunning: () => true, target: () => target, topInset: () => 0 });
    el.dispatchEvent(new WheelEvent('wheel', { deltaY: -40 }));
    el.scrollTop = 1500;
    el.dispatchEvent(new Event('scroll'));
    await frames();
    expect(follower.enabled()).toBe(false);
    el.dispatchEvent(new WheelEvent('wheel', { deltaY: 40 }));
    el.scrollTop = 2500;
    el.dispatchEvent(new Event('scroll'));
    for (let i = 0; i < 4; i += 1) {
      el.dispatchEvent(new WheelEvent('wheel', { deltaY: 30 }));
      await frames();
    }
    expect(follower.enabled()).toBe(true);
    expect(el.scrollTop).toBe(2500);
  });

  it('начало нового ответа по-прежнему встаёт у верхнего края', async () => {
    const el = feed(2500);
    const target = answer(el);
    const follower = createFollower({ scroller: el, isRunning: () => true, target: () => target, topInset: () => 0 });
    follower.update();
    for (let i = 0; i < 40 && el.scrollTop > 1000; i += 1) await frames();
    expect(el.scrollTop).toBeLessThan(1010);
  });
});

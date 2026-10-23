import { afterEach, describe, expect, it, vi } from 'vitest';
import { glassPreferred, setGlassEnabled, tailCurve } from './glass';

const cubic = (a: number, b: number, t: number) => 3 * a * t * (1 - t) ** 2 + 3 * b * t ** 2 * (1 - t) + t ** 3;
function ease(curve: number[], t: number): number {
  let lo = 0; let hi = 1;
  for (let i = 0; i < 45; i += 1) {
    const u = (lo + hi) / 2;
    if (cubic(curve[0]!, curve[2]!, u) < t) lo = u; else hi = u;
  }
  return cubic(curve[1]!, curve[3]!, (lo + hi) / 2);
}

afterEach(() => {
  setGlassEnabled(false, false);
  document.body.replaceChildren();
  document.documentElement.classList.remove('glass-capable');
  delete window.koddiWindowBridge;
  delete window.koddiGlassPreferred;
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('синхронизация стекла', () => {
  it('остаток bezier совпадает с исходным движением на всём интервале', () => {
    const curve = [0.22, 1, 0.36, 1];
    for (const start of [0, 0.1, 0.3, 0.7]) {
      const tail = tailCurve(curve, start);
      const from = ease(curve, start);
      for (const t of [0, 0.1, 0.5, 0.9, 1]) {
        expect(from + (1 - from) * ease(tail, t)).toBeCloseTo(ease(curve, start + (1 - start) * t), 5);
      }
    }
  });

  it('повторный кадр сохраняет начало, новое движение заменяет старое, отмена ставит стекло сразу', () => {
    vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1));
    let now = 1000;
    vi.spyOn(Date, 'now').mockImplementation(() => now);
    document.documentElement.classList.add('glass-capable');
    const node = document.createElement('div');
    node.dataset.glass = 'sidebar';
    document.body.append(node);
    node.checkVisibility = () => true;
    let x = 100;
    node.getBoundingClientRect = () => ({ left: x, top: 8, width: 200, height: 500 }) as DOMRect;
    const movement = () => ({ effect: {
      getKeyframes: () => [{ transform: 'translateX(100px)' }, { transform: 'translateX(0)' }],
      getComputedTiming: () => ({ duration: 400, localTime: 100, progress: 0.5 }),
      getTiming: () => ({ easing: 'cubic-bezier(0.22, 1, 0.36, 1)' }),
    } }) as unknown as Animation;
    let motions = [movement()];
    node.getAnimations = () => motions;
    const packets: Array<{ regions: Array<Record<string, unknown>> }> = [];
    window.koddiWindowBridge = { setGlass: (payload: string) => packets.push(JSON.parse(payload)) } as unknown as NonNullable<typeof window.koddiWindowBridge>;
    const push = () => { setGlassEnabled(true, false); return packets.at(-1)!.regions[0]!; };
    const first = push();
    now += 20;
    expect(push()).toEqual(first);
    motions = [movement()];
    x = 130;
    expect(push()).toMatchObject({ x: 130, at: 1020, to: { x: 80 } });
    motions = [];
    x = 80;
    expect(push()).toEqual({ id: 'sidebar', x: 80, y: 8, w: 200, h: 500, r: 0 });
  });
});

describe('выбор стекла', () => {
  it('выбор из startup.json хоста важнее устаревшего localStorage', () => {
    localStorage.setItem('koddi.liquid-glass', 'on');
    window.koddiGlassPreferred = false;
    expect(glassPreferred()).toBe(false);
  });

  it('переключение сразу меняет выбор для этой страницы', () => {
    window.koddiGlassPreferred = true;
    setGlassEnabled(false);
    expect(glassPreferred()).toBe(false);
    expect(localStorage.getItem('koddi.liquid-glass')).toBe('off');
  });

  it('без хоста работает по localStorage', () => {
    expect(glassPreferred()).toBe(true);
    localStorage.setItem('koddi.liquid-glass', 'off');
    expect(glassPreferred()).toBe(false);
  });
});

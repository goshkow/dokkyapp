import { afterEach, describe, expect, it, vi } from 'vitest';
import { glassPreferred, nextFade, setGlassEnabled, tailCurve } from './glass';

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

describe('угасание блока', () => {
  it('новое угасание начинается с полной или нулевой прозрачности', () => {
    expect(nextFade(null, true, 1000, 240, [0, 0, 1, 1])).toEqual({ from: 0, to: 1, ms: 240, c: [0, 0, 1, 1], at: 1000 });
    expect(nextFade(null, false, 1000, 240, [0, 0, 1, 1])).toMatchObject({ from: 1, to: 0 });
  });

  it('разворот посреди угасания продолжается с текущей прозрачности', () => {
    const curve = [0.25, 0.1, 0.25, 1];
    const hiding = nextFade(null, false, 1000, 200, curve);
    const turned = nextFade(hiding, true, 1050, 200, curve);
    // та же кривая, что у хоста: линейная четверть времени дала бы 0.75
    expect(turned.from).toBeCloseTo(1 - ease(curve, 0.25), 3);
    expect(turned).toMatchObject({ to: 1, at: 1050 });
    expect(nextFade(hiding, true, 5000, 200, curve).from).toBeCloseTo(0, 3);
  });

  it('угасание подложки уходит хосту вместе с итоговой прозрачностью', () => {
    document.documentElement.classList.add('glass-capable');
    const node = document.createElement('div');
    node.dataset.glass = 'product';
    node.dataset.glassFade = JSON.stringify({ from: 1, to: 0, ms: 240, c: [0, 0, 1, 1], at: 7 });
    document.body.append(node);
    node.checkVisibility = () => true;
    node.getAnimations = () => [];
    node.getBoundingClientRect = () => ({ left: 10, top: 8, width: 200, height: 30 }) as DOMRect;
    const packets: Array<{ regions: Array<Record<string, unknown>> }> = [];
    window.koddiWindowBridge = { setGlass: (payload: string) => packets.push(JSON.parse(payload)) } as unknown as NonNullable<typeof window.koddiWindowBridge>;
    setGlassEnabled(true, false);
    expect(packets.at(-1)!.regions[0]).toMatchObject({ id: 'product', o: 0, fade: { from: 1, to: 0, ms: 240, at: 7 } });
    delete node.dataset.glassFade;
    setGlassEnabled(true, false);
    expect(packets.at(-1)!.regions[0]).not.toHaveProperty('fade');
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

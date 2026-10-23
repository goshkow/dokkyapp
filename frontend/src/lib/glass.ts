/** Liquid Glass (macOS 26+): боковые панели - нативные плитки со своими видами, а блоки главной страницы (поле ввода) получают нативное стекло под прозрачной страницей: страница сообщает, где они. */
import { useSyncExternalStore } from 'react';
import { isMirror } from './mirror';


const KEY = 'koddi.liquid-glass';
const root = document.documentElement;
const IDLE_FRAMES = 24;

interface Box { x: number; y: number; w: number; h: number }
interface Region extends Box { id: string; r: number; to?: Box; ms?: number; c?: number[]; at?: number }

/** Хост умеет стекло: macOS 26 и новее, окно создано прозрачным. */
export const glassCapable = (): boolean => root.classList.contains('glass-capable');

/** Выбор человека; по умолчанию стекло включено там, где оно доступно.
 * Главный источник - startup.json хоста (пишется сразу при переключении): localStorage QtWebEngine сбрасывается
 * на диск с задержкой и при быстром выходе терялся, и стекло включалось снова. */
export function glassPreferred(): boolean {
  if (typeof window.koddiGlassPreferred === 'boolean') return window.koddiGlassPreferred;
  try { return localStorage.getItem(KEY) !== 'off'; } catch { return true; }
}

let active = false;
let frame = 0;
let idle = 0;
let sent = '';
const listeners = new Set<() => void>();

/** Движение панели: хост проигрывает его сам тем же кубическим безье, а не повторяет каждый кадр страницы. */
interface Flight { to: Box; from: Box; ms: number; c: number[]; at: number; motion: Animation }
const flights = new Map<string, Flight>();

const rectOf = (node: Element): Box => {
  const box = node.getBoundingClientRect();
  return { x: box.left, y: box.top, w: box.width, h: box.height };
};

function bezier(easing: string): number[] | null {
  const match = /cubic-bezier\(([^)]+)\)/.exec(easing);
  const parts = match?.[1]?.split(',').map(Number);
  return parts && parts.length === 4 && parts.every(Number.isFinite) ? parts : null;
}

const cubic = (a: number, b: number, u: number): number => 3 * a * u * (1 - u) ** 2 + 3 * b * u * u * (1 - u) + u ** 3;

/** Оставшаяся часть кривой с момента t0: хост проигрывает только «хвост» движения, а не начинает его заново. */
export function tailCurve(c: number[], t0: number): number[] {
  const [x1, y1, x2, y2] = c as [number, number, number, number];
  let low = 0;
  let high = 1;
  for (let step = 0; step < 24; step += 1) {
    const mid = (low + high) / 2;
    if (cubic(x1, x2, mid) < t0) low = mid; else high = mid;
  }
  const u = (low + high) / 2;
  const lerp = (a: number, b: number) => a + (b - a) * u;
  // де Кастельжо: правая часть кривой от параметра u
  const ax = lerp(0, x1); const ay = lerp(0, y1);
  const bx = lerp(x1, x2); const by = lerp(y1, y2);
  const cx = lerp(x2, 1); const cy = lerp(y2, 1);
  const dx = lerp(ax, bx); const dy = lerp(ay, by);
  const ex = lerp(bx, cx); const ey = lerp(by, cy);
  const fx = lerp(dx, ex); const fy = lerp(dy, ey);
  const width = 1 - fx || 1;
  const height = 1 - fy || 1;
  return [(ex - fx) / width, (ey - fy) / height, (cx - fx) / width, (cy - fy) / height].map((v, index) => (index % 2 ? v : Math.min(1, Math.max(0, v))));
}

/** Сдвиг, который ещё осталось проехать элементу и его предкам по идущим анимациям, и сколько миллисекунд до конца. */
function pendingShift(node: Element): { dx: number; dy: number; ms: number; c: number[]; t: number; motion: Animation } | null {
  let dx = 0;
  let dy = 0;
  let ms = 0;
  let curve: number[] | null = null;
  let elapsed = 0;
  let total = 0;
  let motion: Animation | null = null;
  for (let item: Element | null = node; item && item !== document.body; item = item.parentElement) {
    for (const animation of item.getAnimations()) {
      const effect = animation.effect as KeyframeEffect | null;
      if (!effect || typeof effect.getKeyframes !== 'function') continue;
      const timing = effect.getComputedTiming();
      const duration = Number(timing.duration);
      const left = duration - Number(timing.localTime ?? 0);
      const from = String(effect.getKeyframes()[0]?.transform ?? '');
      const x = /translateX\((-?[\d.]+)px\)/.exec(from);
      const y = /translateY\((-?[\d.]+)px\)/.exec(from);
      if (!(x || y) || !(left > 16) || timing.progress == null) continue;
      const rest = 1 - Number(timing.progress);
      dx += (x ? Number(x[1]) : 0) * rest;
      dy += (y ? Number(y[1]) : 0) * rest;
      if (left > ms) { ms = left; curve = bezier(String(effect.getTiming().easing)); elapsed = duration - left; total = duration; motion = animation; }
    }
  }
  return curve && motion && total > 0 && (Math.abs(dx) > 1 || Math.abs(dy) > 1) ? { dx, dy, ms, c: curve, t: elapsed / total, motion } : null;
}

const near = (a: Box, b: Box): boolean => Math.abs(a.x - b.x) < 1.5 && Math.abs(a.y - b.y) < 1.5 && Math.abs(a.w - b.w) < 1.5 && Math.abs(a.h - b.h) < 1.5;

function collect(): Region[] {
  const regions: Region[] = [];
  const seen = new Set<string>();
  document.querySelectorAll<HTMLElement>('[data-glass]').forEach((node, index) => {
    if (!node.checkVisibility({ visibilityProperty: true, opacityProperty: true })) return;
    const id = node.dataset.glass || String(index);
    const box = rectOf(node);
    if (box.w < 2 || box.h < 2) return;
    const radius = Math.min(parseFloat(getComputedStyle(node).borderTopLeftRadius) || 0, box.h / 2, box.w / 2);
    const shift = pendingShift(node);
    const flight = flights.get(id);
    if (shift) {
      const to = { x: box.x - shift.dx, y: box.y - shift.dy, w: box.w, h: box.h };
      const ms = shift.ms;
      const curve = tailCurve(shift.c, shift.t);
      // то же движение, что уже передано хосту: повторять его не нужно, иначе оно начнётся заново
      if (flight && flight.motion === shift.motion && near(flight.to, to)) {
        seen.add(id);
        regions.push({ id, ...flight.from, r: radius, to: flight.to, ms: flight.ms, c: flight.c, at: flight.at });
        return;
      }
      const at = Date.now();
      flights.set(id, { to, from: box, ms, c: curve, at, motion: shift.motion });
      seen.add(id);
      regions.push({ id, ...box, r: radius, to, ms, c: curve, at });
      return;
    }
    flights.delete(id);
    if (box.x + box.w <= 0 || box.y + box.h <= 0 || box.x >= innerWidth || box.y >= innerHeight) return;
    regions.push({ id, ...box, r: radius });
  });
  for (const id of [...flights.keys()]) if (!seen.has(id)) flights.delete(id);
  return regions;
}

/** Обычный цвет фона приложения: стекло подкладывается под блоки, а палитра остаётся прежней. */
export function appColor(): number[] | null {
  const probe = document.createElement('div');
  probe.style.cssText = 'position:fixed;width:0;height:0;visibility:hidden;background:var(--bg)';
  document.body.append(probe);
  const value = getComputedStyle(probe).backgroundColor;
  probe.remove();
  const numbers = (value.match(/-?[\d.]+/g) ?? []).map(Number);
  if (numbers.length < 3) return null;
  const scale = value.startsWith('color(') ? 1 : 255;
  return numbers.slice(0, 3).map((part) => Math.min(1, Math.max(0, part / scale)));
}

let cachedColor: number[] | null = null;

function push(force = false): void {
  // вид плитки сообщает хосту только рамки своих блоков, главный вид - ещё включённость, тему и цвет
  const send = isMirror ? window.koddiWindowBridge?.regions : window.koddiWindowBridge?.setGlass;
  if (!send) return;
  const theme = root.dataset.theme === 'light' ? 'light' : 'dark';
  if (!isMirror && active && (force || !cachedColor)) cachedColor = appColor();
  // размер страницы при замере: хост по нему предсказывает, куда блоки уедут вместе с окном, пока страница не прислала новый замер
  const size = { vw: innerWidth, vh: innerHeight };
  const payload = JSON.stringify(isMirror
    ? { regions: active ? collect() : [], ...size }
    : { enabled: active, theme, color: active ? cachedColor : null, regions: active ? collect() : [], ...size });
  if (!force && payload === sent) { idle += 1; return; }
  idle = 0;
  sent = payload;
  send.call(window.koddiWindowBridge, payload);
}

function loop(): void {
  push();
  frame = idle < IDLE_FRAMES && active ? requestAnimationFrame(loop) : 0;
}

/** Любое событие, после которого блоки могли сдвинуться: следим кадр за кадром, пока всё не успокоится. */
export function kickGlass(): void {
  if (!active) return;
  idle = 0;
  if (!frame) frame = requestAnimationFrame(loop);
}

export function glassActive(): boolean { return active; }
/** Боковые панели - нативные плитки стекла со своим веб-видом: хост создал их, и стекло включено. */
export const nativeTiles = (): boolean => active && window.koddiTiles === true;
export const onGlassChange = (fn: () => void): (() => void) => { listeners.add(fn); return () => listeners.delete(fn); };
export const useNativeTiles = (): boolean => useSyncExternalStore(onGlassChange, nativeTiles);

export function setGlassEnabled(enabled: boolean, remember = true): void {
  const next = enabled && glassCapable();
  if (remember) {
    window.koddiGlassPreferred = enabled;
    try { localStorage.setItem(KEY, enabled ? 'on' : 'off'); } catch { /* без сохранения */ }
  }
  active = next;
  root.classList.toggle('glass', next);
  root.classList.toggle('native-tiles', next && window.koddiTiles === true);
  push(true);
  kickGlass();
  listeners.forEach((fn) => fn());
}

/** Слежение за блоками: любое событие, после которого они могли сдвинуться, запускает покадровую проверку. */
function watch(): void {
  addEventListener('resize', kickGlass);
  for (const type of ['transitionstart', 'transitionrun', 'animationstart', 'pointerdown', 'keydown', 'scroll']) document.addEventListener(type, kickGlass, true);
  new MutationObserver(kickGlass).observe(document.getElementById('root') ?? document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['class', 'style', 'hidden', 'open', 'data-open', 'aria-expanded', 'data-focus'] });
  new MutationObserver(() => { push(true); kickGlass(); }).observe(root, { attributes: true, attributeFilter: ['data-theme', 'data-surface'] });
  // после ресайза мышью блоки сообщаются заново
  let resizing = root.classList.contains('resizing');
  new MutationObserver(() => {
    const now = root.classList.contains('resizing');
    if (now === resizing) return;
    resizing = now;
    if (!now) { push(true); kickGlass(); }
  }).observe(root, { attributes: true, attributeFilter: ['class'] });
}

let started = false;
export function initGlass(): void {
  if (started) return;
  started = true;
  const start = () => {
    if (!glassCapable()) return;
    setGlassEnabled(glassPreferred(), false);
    watch();
  };
  if (window.koddiWindowBridge) start();
  else addEventListener('koddi-bridge', start, { once: true });
}

/** Вид плитки (правая панель): стекло под карточками включено всегда, пока плитка существует. */
export function startTileGlass(): void {
  if (started) return;
  started = true;
  const start = () => { active = true; watch(); push(true); kickGlass(); };
  if (window.koddiWindowBridge) start();
  else addEventListener('koddi-bridge', start, { once: true });
}

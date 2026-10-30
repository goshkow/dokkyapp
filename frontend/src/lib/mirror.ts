/**
 * Несколько веб-видов одного приложения (macOS, нативные плитки Liquid Glass): главный вид владеет состоянием и действиями,
 * вид плитки (роль sidebar, inspector, composer или product) показывает зеркало состояния и вызывает действия у главного вида.
 * Связь идёт через хост (WindowBridge.relay -> window.koddiRelayIn); в обычном окне и в браузере зеркал нет.
 */
import type { Store } from './store';

export type Role = 'main' | 'sidebar' | 'inspector' | 'composer' | 'product';

export const role: Role = (() => {
  const value = new URLSearchParams(location.search).get('role');
  return value === 'sidebar' || value === 'inspector' || value === 'composer' || value === 'product' ? value : 'main';
})();
/** Вид плитки: состояния не владеет, действия отправляет главному виду. */
export const isMirror = role !== 'main';

type Wire =
  | { k: 'hello'; r: Role }
  | { k: 'state'; n: string; s: Record<string, unknown> }
  | { k: 'call'; id: number; to: Role; from: Role; f: string; a: unknown[] }
  | { k: 'ret'; id: number; to: Role; ok: boolean; v?: unknown; e?: string }
  | { k: 'touch'; from: Role };

/** Событие окна: нажатие пришлось на другой вид или фрейм сайта - меню и списки этого вида закрываются (ui/Popover). */
export const OUTSIDE_POINTER = 'koddi:outside-pointer';

const stores = new Map<string, Store<object>>();
const handlers = new Map<string, (...args: never[]) => unknown>();
const pending = new Map<number, { resolve: (value: unknown) => void; reject: (error: Error) => void; timer: number }>();
const queue: string[] = [];
let nextId = 1;
let helloTimer = 0;
let synced = false;
const syncWaiters = new Set<() => void>();

function transport(): ((payload: string) => void) | null {
  const relay = window.koddiWindowBridge?.relay;
  return relay ? (payload) => relay.call(window.koddiWindowBridge, payload) : null;
}

function send(message: Wire): void {
  const payload = JSON.stringify(message);
  const out = transport();
  if (out) out(payload);
  else queue.push(payload);
}

function flush(): void {
  const out = transport();
  if (!out) return;
  for (const payload of queue.splice(0)) out(payload);
}

/** Главный вид отдаёт состояние хранилища зеркалам: полное по запросу, дальше только изменившиеся поля. */
export function exposeStore<T extends object>(name: string, store: Store<T>, pick?: readonly (keyof T)[]): void {
  if (isMirror) return;
  stores.set(name, store as unknown as Store<object>);
  let last: Partial<T> = {};
  let scheduled = false;
  const snapshot = (): Partial<T> => {
    const state = store.get();
    if (!pick) return { ...state };
    const part: Partial<T> = {};
    for (const key of pick) part[key] = state[key];
    return part;
  };
  const publish = (full: boolean) => {
    const now = snapshot();
    const changed: Record<string, unknown> = {};
    for (const key of Object.keys(now) as (keyof T)[]) {
      if (full || !Object.is(now[key], last[key])) changed[key as string] = now[key];
    }
    last = now;
    if (Object.keys(changed).length) send({ k: 'state', n: name, s: changed });
  };
  publishers.set(name, () => publish(true));
  store.subscribe(() => {
    if (scheduled) return;
    scheduled = true;
    queueMicrotask(() => { scheduled = false; publish(false); });
  });
}
const publishers = new Map<string, () => void>();

/** Вид плитки принимает состояние хранилища от главного вида. */
export function mirrorStore<T extends object>(name: string, store: Store<T>): void {
  if (!isMirror) return;
  stores.set(name, store as unknown as Store<object>);
}

/** Вид регистрирует действия, которые могут вызывать другие виды (главный вид - действия приложения, плитка - свои). */
export function exposeActions(table: Record<string, (...args: never[]) => unknown>): void {
  for (const [name, fn] of Object.entries(table)) handlers.set(name, fn);
}

/** Действие плитки: в главном виде выполняется на месте, в плитке - у главного вида. */
export function remote<A extends unknown[], R>(name: string, local: (...args: A) => R): (...args: A) => Promise<Awaited<R>> | R {
  if (!isMirror) return local;
  return ((...args: A) => call('main', name, args)) as (...args: A) => Promise<Awaited<R>>;
}

/** Главный вид отправляет действие плитке своей роли (когда боковая панель - плитка), иначе выполняет на месте. */
export function toTile<A extends unknown[], R>(to: Role, name: string, local: (...args: A) => R, active: () => boolean): (...args: A) => Promise<Awaited<R>> | R {
  return ((...args: A) => (!isMirror && active() ? call(to, name, args) : local(...args))) as (...args: A) => Promise<Awaited<R>> | R;
}

/** Вызов действия у другого вида; ответ приходит обратно через хост. */
export function call<R>(to: Role, name: string, args: unknown[]): Promise<R> {
  return new Promise<R>((resolve, reject) => {
    const id = nextId++;
    const timer = window.setTimeout(() => { pending.delete(id); reject(new Error(`нет ответа на ${name}`)); }, 120_000);
    pending.set(id, { resolve: resolve as (value: unknown) => void, reject, timer });
    send({ k: 'call', id, to, from: role, f: name, a: JSON.parse(JSON.stringify(args)) as unknown[] });
  });
}

function receive(raw: string): void {
  let message: Wire;
  try { message = JSON.parse(raw) as Wire; } catch { return; }
  if (message.k === 'hello' && !isMirror) {
    for (const publish of publishers.values()) publish();
  } else if (message.k === 'state' && isMirror) {
    const store = stores.get(message.n);
    if (store) (store as Store<Record<string, unknown>>).set(message.s);
    if (!synced) {
      synced = true;
      window.clearInterval(helloTimer);
      syncWaiters.forEach((fn) => fn());
    }
  } else if (message.k === 'call' && message.to === role) {
    const fn = handlers.get(message.f);
    const done = (ok: boolean, value?: unknown, error?: unknown) =>
      send({ k: 'ret', id: message.id, to: message.from, ok, v: ok ? value : undefined, e: ok ? undefined : String((error as Error)?.message ?? error) });
    if (!fn) { done(false, undefined, `нет действия ${message.f}`); return; }
    try {
      Promise.resolve((fn as (...args: unknown[]) => unknown)(...message.a)).then((value) => done(true, value ?? null), (error) => done(false, undefined, error));
    } catch (error) { done(false, undefined, error); }
  } else if (message.k === 'touch' && message.from !== role) {
    window.dispatchEvent(new Event(OUTSIDE_POINTER));
  } else if (message.k === 'ret' && message.to === role) {
    const wait = pending.get(message.id);
    if (!wait) return;
    pending.delete(message.id);
    window.clearTimeout(wait.timer);
    if (message.ok) wait.resolve(message.v);
    else wait.reject(new Error(message.e || 'действие не выполнено'));
  }
}

window.koddiRelayIn = receive;
window.addEventListener('koddi-bridge', flush);

/* нажатие в одном виде - щелчок мимо меню всех остальных: у каждого вида свой документ, и мимо-щелчков чужих видов он не видит */
const multiView = (): boolean => isMirror || window.koddiTiles === true;
function announceTouch(): void {
  const out = transport();
  // не в очередь: старое нажатие, дошедшее после подключения моста, закрыло бы только что открытое меню
  if (out && multiView()) out(JSON.stringify({ k: 'touch', from: role } satisfies Wire));
}
document.addEventListener('pointerdown', announceTouch, true);
// фрейм страницы сайта (режим чата) забирает нажатия себе: окно узнаёт о них только по уходу фокуса во фрейм
window.addEventListener('blur', () => {
  queueMicrotask(() => {
    if (document.activeElement?.tagName !== 'IFRAME') return;
    window.dispatchEvent(new Event(OUTSIDE_POINTER));
    announceTouch();
  });
});

/** Вид плитки просит у главного вида всё состояние и повторяет просьбу, пока тот не готов. */
export function startMirror(onSynced: () => void): (() => void) | undefined {
  if (!isMirror) return;
  syncWaiters.add(onSynced);
  // снимок мог прийти до useEffect: иначе плитка навсегда останется пустой
  if (synced) onSynced();
  const hello = () => { send({ k: 'hello', r: role }); };
  const begin = () => {
    hello();
    helloTimer = window.setInterval(() => { if (synced) window.clearInterval(helloTimer); else hello(); }, 400);
  };
  if (window.koddiWindowBridge) begin();
  else window.addEventListener('koddi-bridge', begin, { once: true });
  return () => {
    syncWaiters.delete(onSynced);
    window.removeEventListener('koddi-bridge', begin);
    if (!syncWaiters.size) window.clearInterval(helloTimer);
  };
}

declare global {
  interface Window {
    koddiRelayIn?: (payload: string) => void;
    koddiTiles?: boolean;
    koddiGlassPreferred?: boolean;
  }
}

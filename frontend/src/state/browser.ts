/* Живое окно браузера агента: список открытых страниц, кадры и ввод пользователя. Кадры идут цепочкой, пока карточка открыта. */
import { api, ApiError, post } from '../lib/api';
import { createStore, useStore } from '../lib/store';
import { notify } from './overlay';
import { selectInspector } from './workspaceRemote';

export interface BrowserSession {
  session_id: number;
  project_id: number | null;
  url: string;
  title: string;
  local: boolean;
}

export interface BrowserFrame {
  sessionId: number;
  src: string;
  width: number;
  height: number;
  url: string;
  title: string;
}

interface BrowserState {
  sessions: BrowserSession[];
  active: number | null;
  frame: BrowserFrame | null;
  error: string;
}

export const browser = createStore<BrowserState>({ sessions: [], active: null, frame: null, error: '' });
export const useBrowser = <S,>(selector: (state: BrowserState) => S): S => useStore(browser, selector);

// после действия пользователя кадры идут часто, в покое реже: страница может меняться и сама (анимации, загрузка)
const FAST_MS = 60;
const IDLE_MS = 450;
const FAST_WINDOW_MS = 2500;

let watching = 0;
let timer = 0;
let busy = false;
let fastUntil = 0;

function setFrame(frame: BrowserFrame | null): void {
  const previous = browser.get().frame;
  if (previous && previous.src !== frame?.src) URL.revokeObjectURL(previous.src);
  browser.set({ frame });
}

export async function loadBrowser(): Promise<void> {
  try {
    const sessions = await api<BrowserSession[]>('/api/browser/live');
    const state = browser.get();
    const active = sessions.some((item) => item.session_id === state.active) ? state.active : (sessions.at(-1)?.session_id ?? null);
    if (active !== state.active) setFrame(null);
    browser.set({ sessions, active, error: '' });
  } catch {
    // сервер недоступен: карточка покажет прежнее состояние
  }
}

async function pullFrame(): Promise<void> {
  const id = browser.get().active;
  if (id === null || busy) return;
  busy = true;
  try {
    const response = await fetch(`/api/browser/${id}/frame.jpg`, { cache: 'no-store' });
    if (response.status === 404) {
      void loadBrowser();
      return;
    }
    if (!response.ok) throw new Error(`Кадр не получен: ${response.status}`);
    const blob = await response.blob();
    if (browser.get().active !== id) return;
    const [width, height] = (response.headers.get('x-browser-size') ?? '1280x800').split('x').map(Number);
    const decode = (name: string) => {
      try {
        return decodeURIComponent(response.headers.get(name) ?? '');
      } catch {
        return '';
      }
    };
    setFrame({ sessionId: id, src: URL.createObjectURL(blob), width: width || 1280, height: height || 800, url: decode('x-browser-url'), title: decode('x-browser-title') });
    if (browser.get().error) browser.set({ error: '' });
  } catch (error) {
    browser.set({ error: (error as Error).message });
  } finally {
    busy = false;
  }
}

function schedule(): void {
  window.clearTimeout(timer);
  if (!watching) return;
  const delay = Date.now() < fastUntil ? FAST_MS : IDLE_MS;
  timer = window.setTimeout(async () => {
    if (document.visibilityState === 'visible') await pullFrame();
    schedule();
  }, delay);
}

/** Следующие кадры быстрее: пользователь или агент что-то делает на странице. */
export function hurryFrames(): void {
  fastUntil = Date.now() + FAST_WINDOW_MS;
  if (watching) schedule();
}

/** Пока карточка открыта и окно видимо, кадры идут цепочкой: следующий запрашивается, когда пришёл предыдущий. */
export function watchBrowser(): () => void {
  watching += 1;
  hurryFrames();
  void loadBrowser().then(pullFrame);
  const sessions = window.setInterval(() => { if (document.visibilityState === 'visible') void loadBrowser(); }, 4000);
  return () => {
    window.clearInterval(sessions);
    watching = Math.max(0, watching - 1);
    if (!watching) window.clearTimeout(timer);
  };
}

export function pickBrowser(id: number): void {
  setFrame(null);
  browser.set({ active: id });
  hurryFrames();
  void pullFrame();
}

/** Ввод пользователя в живое окно; координаты - доли размера кадра. */
// запросы ввода идут строго по очереди: иначе отпускание кнопки могло обогнать нажатие
let inputQueue: Promise<void> = Promise.resolve();
let pendingInputs = 0;

export function sendBrowserInput(payload: Record<string, unknown>, { quiet = false } = {}): Promise<void> {
  const id = browser.get().active;
  if (id === null) return Promise.resolve();
  // движение мыши устаревает мгновенно: если очередь занята, его пропускаем
  if (payload.kind === 'move' && pendingInputs > 0) return Promise.resolve();
  hurryFrames();
  pendingInputs += 1;
  inputQueue = inputQueue.then(async () => {
    try {
      const result = await post<{ ok: boolean; error?: string }>(`/api/browser/${id}/input`, payload);
      if (!result.ok && result.error && !quiet) notify(result.error);
    } catch (error) {
      if (!quiet) notify((error as Error).message);
    } finally {
      pendingInputs -= 1;
    }
  });
  return inputQueue;
}

/** Размер страницы повторяет размер панели: без полос по краям и без мелкого шрифта. */
export function fitBrowser(width: number, height: number): void {
  void sendBrowserInput({ kind: 'resize', width: Math.round(width), height: Math.round(height) }, { quiet: true });
}

export async function closeBrowser(id: number): Promise<void> {
  try {
    await post(`/api/browser/${id}/close`);
  } finally {
    await loadBrowser();
  }
}

/** Ссылка из чата во вкладке браузерной панели рабочей области. */
export async function openInBrowserPanel(url: string, projectId: number | null): Promise<boolean> {
  try {
    const result = await post<{ ok: boolean; session_id: number }>('/api/browser/open', { url, project_id: projectId });
    await loadBrowser();
    if (result.ok) browser.set({ active: result.session_id });
    void selectInspector('browser');
    hurryFrames();
    return true;
  } catch (error) {
    notify(error instanceof ApiError ? error.message : 'Страница не открылась');
    return false;
  }
}

/** Событие сервера: агент открыл или закрыл страницу. */
export function onBrowserSession(data: { action?: string }): void {
  void loadBrowser();
  hurryFrames();
  if (data.action === 'opened') void selectInspector('browser');
}

/* Живое окно браузера агента: список открытых страниц, кадры и ввод пользователя. Кадры запрашиваются, пока карточка открыта. */
import { api, post } from '../lib/api';
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

let polling = 0;
let busy = false;

export async function loadBrowser(): Promise<void> {
  try {
    const sessions = await api<BrowserSession[]>('/api/browser/live');
    const state = browser.get();
    const active = sessions.some((item) => item.session_id === state.active) ? state.active : (sessions.at(-1)?.session_id ?? null);
    browser.set({ sessions, active, frame: active === state.active ? state.frame : null, error: '' });
  } catch {
    // сервер недоступен: карточка покажет прежнее состояние
  }
}

async function pullFrame(): Promise<void> {
  const id = browser.get().active;
  if (id === null || busy) return;
  busy = true;
  try {
    const value = await api<{ image: string; width: number; height: number; url: string; title: string }>(`/api/browser/${id}/frame`);
    if (browser.get().active !== id) return;
    browser.set({
      frame: { sessionId: id, src: `data:image/jpeg;base64,${value.image}`, width: value.width, height: value.height, url: value.url, title: value.title },
      error: '',
    });
  } catch (error) {
    if ((error as { status?: number }).status === 404) void loadBrowser();
    else browser.set({ error: (error as Error).message });
  } finally {
    busy = false;
  }
}

/** Пока карточка открыта и окно видимо, кадры обновляются несколько раз в секунду. */
export function watchBrowser(): () => void {
  void loadBrowser().then(pullFrame);
  let tick = 0;
  polling = window.setInterval(() => {
    if (document.visibilityState !== 'visible') return;
    if (++tick % 6 === 0) void loadBrowser();
    void pullFrame();
  }, 700);
  return () => { window.clearInterval(polling); polling = 0; };
}

export function pickBrowser(id: number): void {
  browser.set({ active: id, frame: null });
  void pullFrame();
}

/** Ввод пользователя в живое окно; координаты - доли размера кадра. */
export async function sendBrowserInput(payload: Record<string, unknown>): Promise<void> {
  const id = browser.get().active;
  if (id === null) return;
  try {
    const result = await post<{ ok: boolean; error?: string }>(`/api/browser/${id}/input`, payload);
    if (!result.ok && result.error) notify(result.error);
  } catch (error) {
    notify((error as Error).message);
  }
  void pullFrame();
}

export async function closeBrowser(id: number): Promise<void> {
  try {
    await post(`/api/browser/${id}/close`);
  } finally {
    await loadBrowser();
  }
}

/** Событие сервера: агент открыл или закрыл страницу. */
export function onBrowserSession(data: { action?: string }): void {
  void loadBrowser();
  if (data.action === 'opened') void selectInspector('browser');
}

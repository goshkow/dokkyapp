import { api, cloudPath, post, put, setUnauthorizedHandler } from '../lib/api';
import { cacheClear, cacheGet, cacheSet, setCacheOwner } from '../lib/cache';
import { readJson, readText, writeJson, writeText } from '../lib/storage';
import { createStore, useStore } from '../lib/store';
import {
  defaultSettings, type Account, type AppSettings, type Chat, type ModeOption, type ModelOption, type Project,
} from '../lib/types';
import { loadLimits, resetLimits } from './limits';
import { setPanel, setPanelPersistence } from './panels';
import { notify } from './overlay';
import { applyAppearance, ui, type Scope } from './ui';

export interface DataState {
  booted: boolean;
  account: Account | null;
  settings: AppSettings;
  projects: Project[];
  localChats: Chat[];
  cloudChats: Chat[];
  cloudNext: string | null;
  models: ModelOption[];
  modes: ModeOption[];
  loginBusy: boolean;
  /** Раскрытые проекты и последний проект помнятся между запусками. */
  openProjects: string[];
  lastProjectId: number | null;
}

const OPEN_KEY = 'koddi-open-projects';
const LAST_KEY = 'koddi-last-project';
const SCOPE_KEY = 'koddi-product-scope';

export const data = createStore<DataState>({
  booted: false,
  account: null,
  settings: defaultSettings,
  projects: [],
  localChats: [],
  cloudChats: [],
  cloudNext: null,
  models: [],
  modes: [],
  loginBusy: false,
  openProjects: readJson<string[]>(OPEN_KEY, []),
  lastProjectId: Number(readText(LAST_KEY)) || null,
});

export const useData = <S,>(selector: (state: DataState) => S): S => useStore(data, selector);

export const isReady = (account: Account | null): boolean => Boolean(account?.authenticated || account?.internal_test);

/* запоминание проектов */
export function setProjectOpen(id: number | string, open: boolean): void {
  const items = new Set(data.get().openProjects);
  if (open) items.add(String(id));
  else items.delete(String(id));
  const next = [...items];
  data.set({ openProjects: next });
  writeJson(OPEN_KEY, next);
}

export function rememberProject(project: Project | null): void {
  if (!project) return;
  data.set({ lastProjectId: project.id });
  writeText(LAST_KEY, String(project.id));
}

export const lastProject = (): Project | null => {
  const { projects, lastProjectId } = data.get();
  return projects.find((project) => project.id === lastProjectId) ?? null;
};

/* настройки */
/** «Как в системе» следует настройке Windows и меняется на лету. */
export function resolveTheme(value: AppSettings['theme'] | undefined): 'dark' | 'light' {
  if (value === 'system') return matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  return value === 'light' ? 'light' : 'dark';
}

export function applySettings(settings: AppSettings, first = false): void {
  data.set({ settings });
  const reduced = settings.ui_motion === 'reduced' || (settings.ui_motion !== 'full' && matchMedia('(prefers-reduced-motion: reduce)').matches);
  ui.set({
    theme: resolveTheme(settings.theme),
    surface: settings.ui_surface === 'side-dark' || settings.ui_surface === 'chat-dark' ? settings.ui_surface : 'classic',
    baseSize: settings.text_size || 14,
    uiFont: settings.ui_font === 'system' ? 'system' : 'inter',
    codeFont: settings.code_font || 'system',
    motion: reduced ? 'reduced' : 'full',
    chatWidth: settings.chat_width ?? 'standard',
  });
  applyAppearance();
  if (first) {
    setPanel('left', !settings.left_panel_collapsed && window.innerWidth >= 720, false);
    setPanel('right', !(settings.right_panel_collapsed ?? false), false);
  }
}

export async function saveSettings(patch: Partial<AppSettings> & { project_id?: number | null; confirm_full_access?: boolean }): Promise<AppSettings> {
  const owner = data.get().account?.user?.id;
  const saved = await put<AppSettings>('/api/settings', patch);
  if (data.get().account?.user?.id === owner) applySettings({ ...data.get().settings, ...saved });
  return saved;
}

setPanelPersistence((name, open) => {
  void put('/api/settings', { [`${name}_panel_collapsed`]: !open }).catch(() => undefined);
});

/* локальные данные */
export async function loadLocal(includeModels = true): Promise<void> {
  const [projects, chats, settings, models, modes] = await Promise.all([
    api<Project[]>('/api/projects'),
    api<Chat[]>('/api/chats'),
    api<AppSettings>('/api/settings'),
    includeModels ? api<ModelOption[]>('/api/models/options') : Promise.resolve(null),
    api<ModeOption[]>('/api/modes'),
  ]);
  data.set({ projects, localChats: chats, modes, ...(models ? { models } : {}) });
  applySettings({ ...defaultSettings, ...settings });
}

/* облачные чаты */
export async function loadCloud(cursor = ''): Promise<void> {
  const account = await api<Account>('/api/dokky/session');
  data.set({ account });
  if (!account.authenticated) {
    data.set({ cloudChats: [], cloudNext: null });
    return;
  }
  setCacheOwner(String(account.user?.id ?? account.user?.display_name ?? 'user'));
  const listMode = (scope: Scope) => (scope === 'search' ? 'chat' : scope);
  const requested = listMode(ui.get().scope);
  const name = `list:${requested}`;
  const request = api<{ items: Chat[]; next_cursor: string | null }>(
    cloudPath(`chats?mode=${requested}&limit=50&cursor=${encodeURIComponent(cursor)}`),
  );
  // список из кэша виден сразу, пока свежий ещё в пути
  let arrived = false;
  if (!cursor && !data.get().cloudChats.length) {
    void cacheGet<{ items: Chat[]; next_cursor: string | null }>(name).then((cached) => {
      if (cached && !arrived && !data.get().cloudChats.length && requested === listMode(ui.get().scope)) {
        data.set({ cloudChats: cached.items, cloudNext: cached.next_cursor });
      }
    });
  }
  const page = await request;
  arrived = true;
  if (requested !== listMode(ui.get().scope)) return;
  if (!cursor) cacheSet(name, page);
  data.set({ cloudChats: cursor ? [...data.get().cloudChats, ...page.items] : page.items, cloudNext: page.next_cursor });
}

/* режим приложения */
export function setScope(scope: Scope): void {
  ui.set({ scope });
  writeText(SCOPE_KEY, scope);
}

export const storedScope = (): Scope | null => {
  const value = readText(SCOPE_KEY);
  return value === 'chat' || value === 'search' || value === 'development' ? value : null;
};

/* вход и выход */
export function revoke(): void {
  resetLimits();
  cacheClear();
  setCacheOwner('');
  data.set({
    account: { authenticated: false },
    settings: { ...defaultSettings },
    cloudChats: [],
    cloudNext: null,
    loginBusy: false,
  });
}
setUnauthorizedHandler(() => revoke());

type LoginStatus = { status: 'idle' | 'pending' | 'done' | 'expired' | 'denied' | 'invalid' | 'error'; message?: string; pending?: boolean };

const LOGIN_SECONDS = 600;
let loginTimer = 0;
let loginAttempt = 0;

/**
 * Одна попытка входа живёт 10 минут: сервер выдаёт короткую ссылку, приложение ждёт ответа браузера.
 * Под кнопкой статуса нет: истёкшая попытка просто возвращает «Войти», настоящая ошибка приходит уведомлением.
 */
export async function login(): Promise<void> {
  const attempt = ++loginAttempt;
  clearTimeout(loginTimer);
  data.set({ loginBusy: true });
  let flow: { authorize_url: string; expires_in?: number };
  try {
    flow = await post<{ authorize_url: string; expires_in?: number }>('/api/dokky/auth/start');
  } catch (error) {
    // ошибка выдачи ссылки: браузер не открывается
    if (attempt === loginAttempt) {
      data.set({ loginBusy: false });
      notify((error as Error).message || 'Не удалось начать вход', true);
    }
    return;
  }
  if (attempt !== loginAttempt) return;
  if (window.koddiDesktop) window.location.href = `koddi://open-url?url=${encodeURIComponent(flow.authorize_url)}`;
  else window.open(flow.authorize_url, '_blank', 'noopener,noreferrer');
  // запас в несколько секунд: решает приложение по ответу сервера, таймер - только страховка
  const deadline = Date.now() + (flow.expires_in || LOGIN_SECONDS) * 1000 + 5000;
  const finish = (error = ''): void => {
    if (attempt !== loginAttempt) return;
    data.set({ loginBusy: false });
    if (error) notify(error, true);
  };
  const poll = async (): Promise<void> => {
    if (attempt !== loginAttempt) return;
    try {
      const value = await api<LoginStatus>('/api/dokky/auth/status');
      if (attempt !== loginAttempt) return;
      if (value.status === 'done') {
        // после входа загружается всё то же, что при запуске: списки, модели, лимиты
        resetLimits();
        await bootApp();
        finish();
        return;
      }
      if (value.status === 'expired' || value.status === 'invalid' || value.status === 'idle') return finish();
      if (value.status === 'denied' || value.status === 'error') return finish(value.message || 'Не удалось войти');
      if (Date.now() > deadline) {
        await post('/api/dokky/auth/cancel').catch(() => undefined);
        return finish();
      }
      loginTimer = window.setTimeout(() => void poll(), 1000);
    } catch (error) {
      finish((error as Error).message || 'Не удалось войти');
    }
  };
  loginTimer = window.setTimeout(() => void poll(), 1000);
}

/** «Отмена»: приложение перестаёт ждать, а локальный слушатель закрывается. */
export async function cancelLogin(): Promise<void> {
  loginAttempt += 1;
  clearTimeout(loginTimer);
  data.set({ loginBusy: false });
  await post('/api/dokky/auth/cancel').catch(() => undefined);
}

export async function logout(): Promise<void> {
  await post('/api/dokky/logout');
  revoke();
}

/** Перечитывает bootstrap и сессию: когда план сменился, вместе с ним меняются модели и возможности аккаунта. */
export async function refreshAccount(): Promise<void> {
  if (!data.get().account?.authenticated) return;
  await post('/api/dokky/bootstrap');
  data.set({ account: await api<Account>('/api/dokky/session') });
}

/** Первичная загрузка: сессия, режим, настройки и списки. */
export async function bootApp(): Promise<void> {
  const account = await api<Account>('/api/dokky/session');
  data.set({ account });
  const scope: Scope = storedScope() ?? (account.internal_test ? 'development' : 'chat');
  setScope(scope);
  let current = account;
  if (account.authenticated) {
    await post('/api/dokky/bootstrap');
    current = await api<Account>('/api/dokky/session');
    data.set({ account: current });
  }
  if (isReady(current)) {
    await loadLocal();
    applySettings(data.get().settings, !data.get().booted);
    if (scope !== 'development' && current.authenticated) await loadCloud();
  }
  data.set({ booted: true });
  // лимиты ждут в меню аккаунта заранее, а не грузятся после открытия
  if (current.authenticated) void loadLimits();
}

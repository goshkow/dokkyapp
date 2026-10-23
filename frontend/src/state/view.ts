import { createStore, useStore } from '../lib/store';
import type { Chat, Project } from '../lib/types';
import { rememberProject, data } from './data';
import { ui, type Scope } from './ui';

export type PageKey = 'chat' | 'library' | 'projects' | 'mission' | 'automations' | 'review' | 'skills' | 'sources' | 'logs';

export const pageNames: Record<Exclude<PageKey, 'chat'>, string> = {
  library: 'Библиотека',
  projects: 'Проекты',
  mission: 'Задачи',
  automations: 'Автоматизации',
  review: 'Изменения',
  skills: 'Навыки',
  sources: 'Источники',
  logs: 'Журнал',
};

export interface ViewState {
  page: PageKey;
  /** Открытый чат; у нового чата его нет. */
  chat: Chat | null;
  project: Project | null;
  /** Растёт при каждом переходе: устаревшие ответы сервера не должны попадать в новый экран. */
  version: number;
}

export const view = createStore<ViewState>({ page: 'chat', chat: null, project: null, version: 0 });
export const useView = <S,>(selector: (state: ViewState) => S): S => useStore(view, selector);

/* история переходов: назад и вперёд */
interface Entry {
  scope: Scope;
  page: PageKey;
  chatId: Chat['id'] | null;
  projectId: number | null;
}

export const nav = createStore({ index: -1, length: 0 });
export const useNav = <S,>(selector: (state: { index: number; length: number }) => S): S => useStore(nav, selector);
const entries: Entry[] = [];
let restoring = false;

const sameEntry = (a?: Entry, b?: Entry) =>
  Boolean(a && b && a.scope === b.scope && a.page === b.page && a.chatId === b.chatId && a.projectId === b.projectId);

function current(): Entry {
  const state = view.get();
  const chatPage = state.page === 'chat';
  return {
    scope: ui.get().scope,
    page: state.page,
    chatId: chatPage ? state.chat?.id ?? null : null,
    projectId: chatPage ? state.project?.id ?? null : null,
  };
}

function record(): void {
  if (restoring) return;
  const entry = current();
  const { index } = nav.get();
  if (sameEntry(entries[index], entry)) {
    entries[index] = entry;
    return;
  }
  entries.splice(index + 1);
  entries.push(entry);
  if (entries.length > 80) entries.shift();
  nav.set({ index: entries.length - 1, length: entries.length });
}

/* переходы */
export function newChat(project: Project | null = null): void {
  rememberProject(project);
  view.set((state) => ({ page: 'chat', chat: null, project, version: state.version + 1 }));
  record();
}

export function openChat(chat: Chat): void {
  const project = data.get().projects.find((item) => item.id === chat.project_id) ?? null;
  rememberProject(project);
  view.set((state) => ({ page: 'chat', chat, project, version: state.version + 1 }));
  record();
}

export function openPage(page: Exclude<PageKey, 'chat'>): void {
  view.set((state) => ({ page, chat: null, version: state.version + 1 }));
  record();
}

/** Чат создан отправкой первого сообщения: экран остаётся тем же, меняется только запись в истории. */
export function adoptChat(chat: Chat): void {
  view.set({ chat });
  const { index } = nav.get();
  if (!restoring && index >= 0) entries[index] = current();
}

export function renameCurrent(chat: Chat): void {
  const state = view.get();
  if (state.chat && state.chat.id === chat.id) view.set({ chat });
}

type Restorer = (scope: Scope) => Promise<void>;
let switchScope: Restorer = async () => undefined;
export const setScopeRestorer = (fn: Restorer) => { switchScope = fn; };

async function open(entry: Entry): Promise<boolean> {
  restoring = true;
  try {
    if (entry.scope !== ui.get().scope) await switchScope(entry.scope);
    if (entry.page !== 'chat') {
      openPage(entry.page);
      return true;
    }
    if (entry.chatId == null) {
      newChat(data.get().projects.find((item) => item.id === entry.projectId) ?? null);
      return true;
    }
    const list = entry.scope === 'development' ? data.get().localChats : data.get().cloudChats;
    const chat = list.find((item) => item.id === entry.chatId);
    if (!chat) return false;
    openChat(chat);
    return true;
  } finally {
    restoring = false;
  }
}

async function step(direction: 1 | -1): Promise<void> {
  for (;;) {
    const target = nav.get().index + direction;
    if (target < 0 || target >= entries.length) break;
    nav.set({ index: target });
    const entry = entries[target];
    if (entry && (await open(entry))) return;
    // чат удалили: убираем запись и идём дальше
    entries.splice(target, 1);
    nav.set({ index: direction > 0 ? target - 1 : target, length: entries.length });
  }
}

export const goBack = () => step(-1);
export const goForward = () => step(1);
export const canGoBack = (state: { index: number }) => state.index > 0;
export const canGoForward = (state: { index: number; length: number }) => state.index < state.length - 1;

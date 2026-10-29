import { cloudPath, del, patch, post } from '../lib/api';
import type { Account, Chat, Project } from '../lib/types';
import { data, isReady, lastProject, loadCloud, loadLocal, setScope } from './data';
import { confirmDialog, inputDialog, notify } from './overlay';
import { setSurfaceProduct, surface } from './surface';
import { ui, type Scope } from './ui';
import { newChat, openChat, setScopeRestorer, view } from './view';

const cloudScope = () => ui.get().scope !== 'development';

export const chatApiPath = (id: Chat['id']) => (cloudScope() ? cloudPath(`chats/${encodeURIComponent(String(id))}`) : `/api/chats/${id}`);

/** Список чатов текущего режима. */
export const currentChats = (): Chat[] => (cloudScope() ? data.get().cloudChats : data.get().localChats);

export async function refreshLists(): Promise<void> {
  if (cloudScope()) await loadCloud();
  else await loadLocal(false);
}

/** Закрепление чата Докки (в локальном режиме закрепления нет). */
export async function togglePin(chat: Chat): Promise<void> {
  const updated = await patch<Chat>(chatApiPath(chat.id), { pinned: !chat.pinned, expected_version: chat.version });
  if (view.get().chat?.id === chat.id) view.set({ chat: updated });
  await refreshLists();
}

export async function renameChat(chat: Chat): Promise<void> {
  const value = await inputDialog('Переименовать чат', [{ name: 'title', label: 'Название', value: chat.title, required: true }]);
  if (!value) return;
  const updated = await patch<Chat>(chatApiPath(chat.id), {
    title: value.title,
    ...(cloudScope() && chat.version ? { expected_version: chat.version } : {}),
  });
  if (view.get().chat?.id === chat.id) view.set({ chat: updated });
  await refreshLists();
}

export async function deleteChat(chat: Chat): Promise<void> {
  if (!(await confirmDialog('Удалить чат?', chat.title, 'Удалить'))) return;
  await del(chatApiPath(chat.id) + (cloudScope() ? '?confirm=true' : ''));
  if (view.get().chat?.id === chat.id) newChat(cloudScope() ? null : lastProject());
  await refreshLists();
}

/** Переключение «Чат / Разработка» и «Чат / Поиск»: открытый чат переживает заход в поиск и возвращается. */
let parked: Chat | null = null;
export const forgetParked = (): void => { parked = null; };
let switching = 0;
export async function switchScope(scope: Scope): Promise<void> {
  const account = data.get().account;
  if (!isReady(account)) return;
  // чат и поиск в режиме чата - вкладки страницы сайта, у приложения это один режим
  if (scope !== 'development' && surface.get().status !== 'off') {
    void setSurfaceProduct(scope === 'search' ? 'search' : 'chat');
    scope = 'chat';
  }
  if (scope === ui.get().scope) return;
  const version = ++switching;
  const previous = ui.get().scope;
  if (previous === 'chat' && scope === 'search') parked = view.get().chat;
  const returning = scope === 'chat' ? parked : null;
  if (scope !== 'search') parked = null;
  setScope(scope);
  try {
    if (scope === 'development') {
      await loadLocal(false);
      if (version !== switching) return;
      newChat(lastProject());
    } else {
      newChat(null);
      const keep = previous !== 'development' || surface.get().status !== 'off';
      if (!keep) await loadCloud();
      if (version !== switching) return;
      if (returning && scope === 'chat') openChat(returning);
    }
  } catch (error) {
    notify((error as Error).message, true);
  }
}
setScopeRestorer(switchScope);

export async function createProject(): Promise<Project | null> {
  const value = await inputDialog('Новый проект', [{ name: 'name', label: 'Название', required: true }], 'Создать');
  if (!value) return null;
  const project = await post<Project>('/api/projects/create-empty', value);
  await loadLocal(false);
  newChat(project);
  return project;
}

export async function openFolder(): Promise<void> {
  if (window.koddiDesktop || navigator.userAgent.includes('QtWebEngine')) {
    window.location.href = 'koddi://open-folder';
    return;
  }
  const value = await inputDialog('Открыть проект', [{ name: 'path', label: 'Путь к папке', placeholder: 'C:\\Projects\\MyProject', required: true }]);
  if (!value) return;
  const project = await post<Project>('/api/projects', value);
  await loadLocal(false);
  newChat(project);
}

/** Хост вызывает это после выбора папки в системном окне. */
export function bindHostCallbacks(): void {
  (window as unknown as Record<string, unknown>).koddiProjectOpened = (project: Project) => {
    void loadLocal(false).then(() => newChat(project));
  };
  (window as unknown as Record<string, unknown>).koddiProjectOpenFailed = (error: string) => notify(`Не удалось открыть папку: ${error}`, true);
}

export const accountName = (account: Account | null): string => account?.user?.display_name?.trim() ?? '';


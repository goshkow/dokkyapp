/* Доступ с телефона: чаты Докки Code видны и управляются из Докки на телефоне и на сайте. */
import { api, post, put } from '../lib/api';
import { createStore, useStore } from '../lib/store';

export type RemoteState = 'off' | 'connecting' | 'online' | 'offline' | 'revoked' | 'login' | 'error';
export type PreventSleep = 'off' | 'working' | 'on_ac';

export interface RemoteStatus {
  enabled: boolean;
  state: RemoteState;
  message: string;
  name: string;
  prevent_sleep: PreventSleep;
  prevent_sleep_active: boolean;
  power: 'ac' | 'battery' | 'unknown';
  pending: number;
  new_session_project_id: number | null;
  shared: Record<string, boolean>;
  site: string;
}

interface RemoteStore { status: RemoteStatus | null }

export const remote = createStore<RemoteStore>({ status: null });
export const useRemote = <S,>(selector: (state: RemoteStore) => S): S => useStore(remote, selector);

export async function loadRemote(): Promise<RemoteStatus> {
  const status = await api<RemoteStatus>('/api/remote-control');
  remote.set({ status });
  return status;
}

export async function saveRemote(patch: Partial<Pick<RemoteStatus, 'enabled' | 'prevent_sleep' | 'new_session_project_id'>>): Promise<RemoteStatus> {
  const status = await put<RemoteStatus>('/api/remote-control', patch);
  remote.set({ status });
  return status;
}

export async function reconnectRemote(): Promise<RemoteStatus> {
  const status = await post<RemoteStatus>('/api/remote-control/reconnect');
  remote.set({ status });
  return status;
}

/** Чат виден на телефоне: доступ включён и чат передан компьютером (старые чаты за пределами последних 150 не передаются). */
export const sharedOnPhone = (status: RemoteStatus | null, chatId: string | number | undefined): boolean =>
  Boolean(status?.enabled && chatId !== undefined && status.shared[String(chatId)] === true);

export async function setChatShared(chatId: string | number, shared: boolean): Promise<void> {
  const result = await put<{ shared: boolean }>(`/api/chats/${chatId}/remote`, { shared });
  const status = remote.get().status;
  if (status) remote.set({ status: { ...status, shared: { ...status.shared, [String(chatId)]: result.shared } } });
}

/** Тихое обновление: ошибка не мешает остальному интерфейсу, состояние просто остаётся прежним. */
export const refreshRemote = () => { loadRemote().catch(() => undefined); };

export const stateText: Record<RemoteState, string> = {
  off: 'Выключено',
  connecting: 'Подключаюсь…',
  online: 'Подключено',
  offline: 'Нет сети, повторю',
  revoked: 'Доступ отключён в вебе',
  login: 'Войдите в Докки',
  error: 'Не удалось подключить',
};

/* Автообновление: состояние приходит с локального сервера (`/api/updates/*`) и событиями `update.state`. */
import { ApiError, api, post } from '../lib/api';
import { createStore, useStore } from '../lib/store';
import { confirmDialog, notify } from './overlay';

export type UpdateStatus = 'disabled' | 'idle' | 'checking' | 'up_to_date' | 'available' | 'skipped' | 'downloading' | 'preparing' | 'ready' | 'installing' | 'error';
export type UpdateMethod = 'bundle' | 'portable' | 'setup' | 'setup_elevated' | 'manual' | 'move_to_applications' | 'dev' | 'unsupported';

export interface UpdateState {
  status: UpdateStatus;
  current: string;
  platform: string | null;
  kind: string | null;
  method: UpdateMethod;
  reason?: string;
  auto_download: boolean;
  checked_at?: number | null;
  skipped?: string | null;
  error: string;
  available: { version: string; name: string; size: number } | null;
  progress: { done: number; total: number } | null;
}

export const updates = createStore<{ value: UpdateState | null }>({ value: null });
export const useUpdates = <S,>(selector: (state: UpdateState | null) => S): S => useStore(updates, (store) => selector(store.value));

/** Точка «есть обновление» у аккаунта и в настройках. */
export const hasUpdate = (state: UpdateState | null): boolean =>
  Boolean(state && ['available', 'downloading', 'preparing', 'ready'].includes(state.status));

export function applyUpdateState(value: unknown): void {
  if (value && typeof value === 'object' && 'status' in value) updates.set({ value: value as UpdateState });
}

export async function loadUpdates(): Promise<void> {
  applyUpdateState(await api<UpdateState>('/api/updates/status'));
}

export async function checkUpdates(): Promise<void> {
  applyUpdateState(await post<UpdateState>('/api/updates/check'));
}

export async function downloadUpdate(): Promise<void> {
  applyUpdateState(await post<UpdateState>('/api/updates/download'));
}

export async function cancelUpdate(): Promise<void> {
  applyUpdateState(await post<UpdateState>('/api/updates/cancel'));
}

export async function skipUpdate(): Promise<void> {
  applyUpdateState(await post<UpdateState>('/api/updates/skip'));
}

export async function setAutoDownload(value: boolean): Promise<void> {
  applyUpdateState(await api<UpdateState>('/api/updates/settings', { method: 'PUT', body: JSON.stringify({ auto_download: value }) }));
}

/** Перезапуск: приложение закрывается само, помощник ставит новую версию и открывает её. */
export async function installUpdate(force = false): Promise<void> {
  await post('/api/updates/install', { force });
  const current = updates.get().value;
  if (current) updates.set({ value: { ...current, status: 'installing' } });
}

export const megabytes = (bytes: number): string => `${(bytes / 1024 / 1024).toLocaleString('ru-RU', { maximumFractionDigits: bytes < 100 * 1024 * 1024 ? 1 : 0 })} МБ`;

let announced = '';
/** Новая версия готова: один раз за версию говорим об этом, даже если настройки закрыты. */
export function announceReady(version: string): void {
  if (!version || announced === version) return;
  announced = version;
  notify(`Обновление ${version} готово. Перезапустите Докки в настройках, раздел «Обновления»`);
}

export async function restartForUpdate(): Promise<void> {
  try {
    await installUpdate();
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 409 || !/Агент/.test(error.message)) throw error;
    if (!(await confirmDialog('Агент ещё работает', 'Перезапуск остановит текущую задачу. Сделанное сохранится, задачу можно будет продолжить после обновления.', 'Перезапустить'))) return;
    await installUpdate(true);
  }
  notify('Докки перезапустится через пару секунд');
}

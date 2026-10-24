/* Очередь запросов во время работы агента и права текущего проекта. */
import { api, post } from '../lib/api';
import type { AppSettings } from '../lib/types';
import { chat, currentChatId, ctl, hooks, isDevelopment, selectedLocalModel, stale } from './chatCore';
import type { QueueSnapshot } from './chatTypes';
import { requireDeveloper } from './access';
import { attachmentsReady, composer, draftContent } from './composer';
import { data } from './data';
import { inputDialog } from './overlay';
import { view } from './view';

const queue = { timer: 0, signature: null as string | null, wasRunning: false, pending: null as { signature: string; key: string } | null };
let permissionsProject: number | null | undefined;
let permissionsVersion = 0;
let enqueueing: object | null = null;

export function resetQueue(): void {
  enqueueing = null;
  clearTimeout(queue.timer);
  queue.signature = null;
  queue.wasRunning = false;
  queue.pending = null;
}

export function resetPermissions(): void {
  permissionsProject = undefined;
  permissionsVersion += 1;
}

/** Права и доступ к сети задаются для проекта: при смене проекта показываем безопасные значения, пока не пришли настоящие. */
export async function refreshProjectPermissions(): Promise<void> {
  const project = view.get().project?.id ?? null;
  if (permissionsProject === project) return;
  permissionsProject = project;
  const mine = ++permissionsVersion;
  data.set({ settings: { ...data.get().settings, permission_profile: 'ask' } });
  const settings = await api<AppSettings>(`/api/settings${project ? `?project_id=${project}` : ''}`);
  if (mine !== permissionsVersion || (view.get().project?.id ?? null) !== project) return;
  data.set({ settings: { ...data.get().settings, permission_profile: settings.permission_profile, private_network: settings.private_network } });
}

export async function refreshQueue(): Promise<void> {
  clearTimeout(queue.timer);
  const chatId = currentChatId();
  const version = view.get().version;
  if (chatId == null || !isDevelopment()) {
    if (chat.get().queue) chat.set({ queue: null });
    return;
  }
  const snapshot = await api<QueueSnapshot>(`/api/chats/${chatId}/queue`);
  if (stale(version, chatId) || !isDevelopment()) return;
  const signature = JSON.stringify([chatId, snapshot.paused, snapshot.items.map((item) => [item.id, item.version, item.status])]);
  const changed = queue.signature !== signature;
  const previous = queue.signature;
  queue.signature = signature;
  if (changed) chat.set({ queue: snapshot.items.length ? snapshot : null });
  if (snapshot.items.length) {
    queue.timer = window.setTimeout(() => {
      refreshQueue().catch(() => undefined);
    }, 1200);
  }
  const running = snapshot.items.some((item) => item.status === 'running');
  if (changed && previous && (running || queue.wasRunning)) {
    queue.wasRunning = running;
    await hooks.sync();
  }
  // ход из очереди запускает сервер: подключаемся к нему, даже если момент смены статуса пропущен
  if (running && !ctl.stream && !stale(version, chatId)) {
    if (!chat.get().running) await hooks.sync();
    if (!stale(version, chatId) && chat.get().running && !ctl.stream) void hooks.attach(chatId);
  }
}

export async function enqueueDraft(): Promise<{ id: string; version: number } | null> {
  if (enqueueing) return null;
  const token = {};
  enqueueing = token;
  try { return await enqueueCurrentDraft(); } finally { if (enqueueing === token) enqueueing = null; }
}

async function enqueueCurrentDraft(): Promise<{ id: string; version: number } | null> {
  if (!requireDeveloper()) return null;
  const state = composer.get();
  const content = draftContent();
  const chatId = currentChatId();
  const version = view.get().version;
  if (!content || chatId == null || !attachmentsReady()) return null;
  const payload = {
    content, mode: state.agentMode, model: selectedLocalModel(), options: {}, attachment_ids: state.attachments.map((item) => item.id),
    skill_ids: state.skills.map((item) => item.id),
  };
  const signature = JSON.stringify([chatId, payload]);
  if (queue.pending?.signature !== signature) queue.pending = { signature, key: crypto.randomUUID() };
  const key = queue.pending.key;
  const snapshot = await post<QueueSnapshot>(`/api/chats/${chatId}/queue`, { ...payload, request_id: key }, { signal: ctl.view.signal });
  if (queue.pending?.key === key) queue.pending = null;
  if (stale(version, chatId)) return null;
  const sentFiles = new Set(state.attachments.map((item) => item.id));
  const sentSkills = new Set(state.skills.map((item) => item.id));
  composer.set({
    attachments: composer.get().attachments.filter((item) => !sentFiles.has(item.id)),
    skills: composer.get().skills.filter((item) => !sentSkills.has(item.id)),
    ...(composer.get().text === state.text ? { text: '' } : {}),
  });
  await refreshQueue();
  const added = snapshot?.items?.find((item) => item.id === key);
  return added ? { id: String(added.id), version: added.version } : null;
}

/** Запрос из очереди выполняется сейчас: текущая задача останавливается, он встаёт первым и запускается. */
export async function runQueueItemNow(item: { id: number | string; version: number }): Promise<void> {
  if (!requireDeveloper()) return;
  const chatId = currentChatId();
  if (chatId == null) return;
  if (chat.get().running) await hooks.stop();
  await post(`/api/chats/${chatId}/queue/${item.id}/promote?version=${item.version}`);
  await resumeQueue();
}

/** Набранное сообщение уходит в очередь и сразу запускается вместо текущей задачи. */
export async function interruptAndSend(): Promise<void> {
  const added = await enqueueDraft();
  if (added) await runQueueItemNow(added);
}

export async function resumeQueue(): Promise<void> {
  if (!requireDeveloper()) return;
  const chatId = currentChatId();
  if (chatId == null) return;
  await post(`/api/chats/${chatId}/queue/resume`);
  await refreshQueue();
}

export async function editQueueItem(item: { id: number | string; version: number; content: string }): Promise<void> {
  const chatId = currentChatId();
  if (chatId == null) return;
  const value = await inputDialog('Изменить ожидающий запрос', [{ name: 'content', label: 'Запрос', value: item.content }], 'Сохранить');
  if (!value) return;
  await api(`/api/chats/${chatId}/queue/${item.id}`, { method: 'PATCH', body: JSON.stringify({ version: item.version, content: value.content }) });
  await refreshQueue();
}

export async function removeQueueItem(item: { id: number | string; version: number }): Promise<void> {
  const chatId = currentChatId();
  if (chatId == null) return;
  await api(`/api/chats/${chatId}/queue/${item.id}?version=${item.version}`, { method: 'DELETE' });
  await refreshQueue();
}

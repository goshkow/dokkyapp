/* Работа с открытым чатом: открытие, синхронизация, отправка, остановка. Режимы разработки и Докки (чат) делят одну ленту. */
import { api, cloudPath, patch, post } from '../lib/api';
import { cacheGet, cacheSet } from '../lib/cache';
import { cloudMessage } from '../lib/cloud';
import { serverTime } from '../lib/format';
import type { Message } from '../lib/journal';
import type { Chat, Id } from '../lib/types';
import { forgetParked, refreshLists } from './actions';
import { setChanges, setPlan, setPlanTurnState, showRequests } from './chatAgent';
import {
  chat, ctl, currentChatId, hooks, isDevelopment, pinFeed, resetChat, selectedLocalModel, setRunning, setStatus, stale,
} from './chatCore';
import { cloudCursor, applyMessages, clearStreamState, consume } from './chatStream';
import type { Attachment, PlanSnapshot } from './chatTypes';
import type { Change } from './chatTypes';
import { loadCodingSession, resetCodingRequests } from './codingSession';
import { attachmentsReady, clearAttachments, composer, draftContent, focusComposer, releaseAttachments, type AgentMode } from './composer';
import { beginProgress, bindProgress, clearProgress, finishProgress, progress } from './cloudProgress';
import { data, isReady, login, revoke, saveSettings, setScope } from './data';
import { clearBodies } from './stream';
import { choiceDialog, confirmDialog, inputDialog, notify } from './overlay';
import { enqueueDraft, refreshProjectPermissions, refreshQueue, resetPermissions, resetQueue } from './queue';
import { resetCloudPreferences } from './cloudPrefs';
import { resetSearch } from './search';
import { sendToSite, siteFieldActive, stopSite } from './siteField';
import { surface } from './surface';
import { closeViewers } from './viewers';
import { openSettings } from './settingsUi';
import { ui } from './ui';
import { adoptChat, view } from './view';
import { handlePlanRequired, requireDeveloper } from './access';

interface TurnState {
  active: boolean;
  turn?: { id?: number | string; status?: string; summary?: string };
}
interface CloudPage {
  items: Message[];
  next_cursor: string | null;
}

const isCloud = () => !isDevelopment();
const json = { 'content-type': 'application/json' };

/* переход на экран */
function resetForView(): void {
  localPreparation = null;
  if (submission) submission.cancelled = true;
  submission = null;
  pendingSend = null;
  createKey = null;
  ctl.view.abort();
  ctl.view = new AbortController();
  ctl.stream?.abort();
  ctl.stream = null;
  clearTimeout(ctl.syncTimer);
  clearStreamState();
  clearBodies();
  resetQueue();
  clearAttachments();
  resetCodingRequests();
  clearProgress();
  resetChat();
  localOlder.items = [];
  localOlder.exhausted = false;
}

const initialMode = (current: Chat | null): AgentMode => {
  const mode = current?.mode === 'build' ? 'development' : current?.mode;
  return (['development', 'chat', 'plan', 'debug', 'review'] as const).find((value) => value === mode) ?? 'development';
};

async function enter(version: number): Promise<void> {
  resetForView();
  const { page, chat: current } = view.get();
  if (page !== 'chat') return;
  composer.set({ agentMode: initialMode(current), ...(current ? {} : { text: '' }) });
  if (!current) {
    void refreshProjectPermissions().catch(() => undefined);
    return;
  }
  void refreshProjectPermissions().catch(() => undefined);
  if (isDevelopment()) await openLocal(current, version);
  else await openCloud(current, version);
}

let seen = -1;
view.subscribe(() => {
  const { version } = view.get();
  if (version === seen) return;
  seen = version;
  void enter(version).catch((error: Error) => notify(error.message, true));
});

/** Выход из аккаунта или отзыв сессии: открытый чат и всё, что с ним связано, сбрасывается. */
let wasReady = false;
data.subscribe(() => {
  const ready = isReady(data.get().account);
  if (wasReady && !ready) {
    resetForView();
    composer.set({ text: '' });
    seen = -1;
    // всё, что принадлежало аккаунту: поиск, библиотека, окна просмотра, настройки ответов, права проекта
    resetSearch();
    closeViewers();
    resetCloudPreferences();
    clearProgress(true);
    resetPermissions();
    forgetParked();
  }
  wasReady = ready;
});

/* локальный чат (разработка) */
const localOlder = { items: [] as Message[], exhausted: false };

function showLocal(messages: Message[]): void {
  const oldest = messages[0]?.id ?? Infinity;
  const combined = [...localOlder.items.filter((item) => Number(item.id) < Number(oldest)), ...messages].slice(-1000);
  applyMessages(combined);
  chat.set({ olderAvailable: !localOlder.exhausted && combined.length >= 200 && combined.length < 1000 });
}

async function openLocal(current: Chat, version: number): Promise<void> {
  const signal = ctl.view.signal;
  chat.set({ load: 'loading', loadError: '' });
  setRunning(false);
  let loaded: [Message[], TurnState, PlanSnapshot, Change[]];
  try {
    loaded = await Promise.all([
      api<Message[]>(`/api/chats/${current.id}/messages`, { signal }),
      api<TurnState>(`/api/chats/${current.id}/turn`, { signal }),
      api<PlanSnapshot>(`/api/chats/${current.id}/plan`, { signal }),
      api<Change[]>(`/api/chats/${current.id}/changes`, { signal }),
    ]);
  } catch (error) {
    if ((error as Error).name === 'AbortError' || signal.aborted || stale(version, current.id)) return;
    chat.set({ load: 'failed', loadError: (error as Error).message });
    setStatus('Не удалось загрузить историю чата');
    return;
  }
  if (signal.aborted || stale(version, current.id)) return;
  const [messages, turn, plan, changes] = loaded;
  chat.set({ load: 'ready' });
  showLocal(messages);
  setPlan(plan);
  setPlanTurnState(turn);
  setChanges(changes);
  setRunning(turn.active);
  await loadCodingSession(current.id, version);
  if (stale(version, current.id)) return;
  clearStreamState();
  if (turn.turn?.status === 'failed') setStatus(`Ошибка: ${turn.turn.summary || 'ответ прерван'}`);
  else setStatus(turn.active ? 'Агент работает…' : 'Готово');
  await showRequests();
  void refreshQueue().catch(() => undefined);
  if (turn.active) void attachLocal(current.id);
}

export async function syncLocal(): Promise<void> {
  const current = view.get().chat;
  const version = view.get().version;
  if (!current || view.get().page !== 'chat' || !isDevelopment()) return;
  const [messages, turn, plan, changes] = await Promise.all([
    api<Message[]>(`/api/chats/${current.id}/messages`),
    api<TurnState>(`/api/chats/${current.id}/turn`),
    api<PlanSnapshot>(`/api/chats/${current.id}/plan`),
    api<Change[]>(`/api/chats/${current.id}/changes`),
  ]);
  if (stale(version, current.id)) return;
  showLocal(messages);
  setPlan(plan);
  setPlanTurnState(turn);
  setChanges(changes);
  setRunning(turn.active);
  if (!turn.active) chat.set({ optimistic: null });
  // ход мог начаться без подключённого потока (очередь, телефон): карточка подтверждения не должна теряться
  if (turn.active || chat.get().requests.length) await showRequests();
  await loadCodingSession(current.id, version);
  await refreshQueue();
}

export async function attachLocal(chatId: Id, after: string | number = 0): Promise<void> {
  ctl.stream?.abort();
  const controller = new AbortController();
  ctl.stream = controller;
  try {
    const response = await fetch(`/api/chats/${chatId}/turn/events?after=${after}`, { signal: controller.signal });
    if (!response.ok) {
      await syncLocal();
      return;
    }
    await consume(response, chatId, controller);
  } catch (error) {
    if ((error as Error).name !== 'AbortError') notify(`Поток прервался: ${(error as Error).message}`, true);
  } finally {
    if (ctl.stream === controller) {
      ctl.stream = null;
      scheduleSyncNow();
    }
  }
}

const scheduleSyncNow = () => {
  clearTimeout(ctl.syncTimer);
  ctl.syncTimer = window.setTimeout(() => void hooks.sync().catch(() => undefined), 0);
};

export async function loadOlderLocal(): Promise<void> {
  const state = chat.get();
  const current = view.get().chat;
  const first = state.messages[0];
  if (!current || !first || state.olderBusy) return;
  const version = view.get().version;
  chat.set({ olderBusy: true });
  try {
    const previous = await api<Message[]>(`/api/chats/${current.id}/messages?before=${first.id}&limit=200`);
    if (stale(version, current.id)) return;
    localOlder.items = [...previous, ...localOlder.items];
    localOlder.exhausted = previous.length < 200;
    showLocal(chat.get().messages.filter((message) => !previous.some((item) => item.id === message.id)));
  } finally {
    chat.set({ olderBusy: false });
  }
}

/* облачный чат (Докки) */
async function openCloud(current: Chat, version: number): Promise<void> {
  // чат из списка открывается в режиме чата, даже если был включён поиск
  if (ui.get().scope === 'search' && current.mode !== 'search') setScope('chat');
  const signal = ctl.view.signal;
  chat.set({ load: 'loading', loadError: '' });
  setRunning(false);
  const id = encodeURIComponent(String(current.id));
  const cacheName = `messages:${current.id}`;
  let loaded: [CloudPage, Chat];
  // сохранённая копия чата показывается сразу, свежая история подтягивается следом
  let arrived = false;
  let shown = false;
  void cacheGet<CloudPage>(cacheName).then((cached) => {
    if (!cached || arrived || signal.aborted || stale(version, current.id)) return;
    shown = true;
    chat.set({ load: 'ready' });
    applyMessages(cached.items.map(cloudMessage));
  });
  try {
    loaded = await Promise.all([
      api<CloudPage>(cloudPath(`chats/${id}/messages?limit=100`), { signal }),
      api<Chat>(cloudPath(`chats/${id}`), { signal }),
    ]);
    arrived = true;
  } catch (error) {
    arrived = true;
    if ((error as Error).name === 'AbortError' || signal.aborted || stale(version, current.id)) return;
    if (shown) {
      setStatus('Нет связи: показана сохранённая копия чата');
      return;
    }
    chat.set({ load: 'failed', loadError: (error as Error).message });
    setStatus('Не удалось загрузить историю чата');
    return;
  }
  if (signal.aborted || stale(version, current.id)) return;
  const [history, fresh] = loaded;
  cacheSet(cacheName, history);
  adoptChat(fresh);
  clearStreamState();
  cloudCursor.value = '0';
  chat.set({ load: 'ready', olderCursor: history.next_cursor, runId: fresh.active_run?.id ?? null });
  applyMessages(history.items.map(cloudMessage));
  setRunning(Boolean(fresh.active_run));
  if (fresh.active_run) {
    const lastUser = [...history.items].reverse().find((item) => item.role === 'user');
    beginProgress(current.id, fresh.active_run.id, serverTime(lastUser?.created_at) || null);
    void attachCloud(current.id, '0');
  }
}

export async function syncCloud(): Promise<void> {
  const current = view.get().chat;
  if (!current) return;
  const version = view.get().version;
  const id = encodeURIComponent(String(current.id));
  const [page, fresh] = await Promise.all([
    api<CloudPage>(cloudPath(`chats/${id}/messages?limit=100`)),
    api<Chat>(cloudPath(`chats/${id}`)),
  ]);
  if (stale(version, current.id)) return;
  cacheSet(`messages:${current.id}`, page);
  adoptChat(fresh);
  const runId = fresh.active_run?.id ?? null;
  chat.set({ runId });
  setRunning(Boolean(runId));
  const latest = page.items.map(cloudMessage);
  const ids = new Set(latest.map((item) => String(item.id)));
  if (!runId && progress.get().live) {
    const last = [...latest].reverse().find((item) => item.role === 'assistant');
    finishProgress(last ? String(last.id) : undefined);
  }
  // старые страницы истории остаются, пока пользователь их не закрыл сменой чата
  const older = chat.get().messages.filter((item) => !ids.has(String(item.id)) && String(item.id).startsWith('msg_'));
  applyMessages([...older, ...latest]);
  if (!runId) chat.set({ optimistic: null });
}

export async function attachCloud(chatId: Id, after: string | number = '0'): Promise<void> {
  ctl.stream?.abort();
  const controller = new AbortController();
  ctl.stream = controller;
  const runId = chat.get().runId;
  if (!runId) return;
  const reconnect = (delay: number) => window.setTimeout(() => {
    if (currentChatId() === chatId && !controller.signal.aborted) void attachCloud(chatId, cloudCursor.value || '0');
  }, delay);
  try {
    const response = await fetch(cloudPath(`runs/${encodeURIComponent(runId)}/events?after=${encodeURIComponent(String(after || '0'))}`), { signal: controller.signal });
    if (!response.ok) {
      if (response.status === 401) {
        revoke();
        return;
      }
      if (response.status >= 400 && response.status < 500) {
        setRunning(false);
        notify('Не удалось открыть ответ Докки. Обновите чат', true);
        return;
      }
      throw new Error('Не удалось подключиться к ответу Докки');
    }
    await consume(response, chatId, controller);
    if (chat.get().running && !controller.signal.aborted && currentChatId() === chatId) {
      setStatus('Восстанавливаю соединение…');
      reconnect(1500);
    }
  } catch (error) {
    if ((error as Error).name !== 'AbortError' && !controller.signal.aborted) {
      setStatus('Соединение прервано. Переподключаюсь…');
      reconnect(3000);
    }
  }
}

export async function loadOlderCloud(): Promise<void> {
  const state = chat.get();
  const current = view.get().chat;
  if (!current || !state.olderCursor || state.olderBusy) return;
  const version = view.get().version;
  chat.set({ olderBusy: true });
  try {
    const page = await api<CloudPage>(
      cloudPath(`chats/${encodeURIComponent(String(current.id))}/messages?limit=100&cursor=${encodeURIComponent(state.olderCursor)}`),
      { signal: ctl.view.signal },
    );
    if (stale(version, current.id)) return;
    applyMessages([...page.items.map(cloudMessage), ...chat.get().messages]);
    chat.set({ olderCursor: page.next_cursor });
  } catch (error) {
    if (!ctl.view.signal.aborted && !stale(version, current.id)) throw error;
  } finally {
    chat.set({ olderBusy: false });
  }
}

/* отправка */
/** Ошибка из ответа локального сервера; отказ по тарифу (`plan_required`) закрывает запись и показывает приглашение. */
async function failed(response: Response, fallback: string): Promise<Error> {
  let detail: unknown;
  try {
    detail = ((await response.json()) as { detail?: unknown }).detail;
  } catch {
    detail = undefined;
  }
  const fields = detail && typeof detail === 'object' ? (detail as { code?: string; message?: string }) : null;
  if (fields?.code === 'plan_required') handlePlanRequired();
  return new Error(typeof detail === 'string' ? detail : fields?.message || fallback);
}

interface Submission {
  version: number;
  scope: string;
  chat: Chat | null;
  cancelled: boolean;
}
let submission: Submission | null = null;
let pendingSend: { fingerprint: string; key: string; body: Record<string, unknown> } | null = null;
let createKey: string | null = null;
let localPreparation: object | null = null;

export async function sendLocal(): Promise<void> {
  // блокируем повтор ещё до выбора папки и создания чата, пока running не установлен
  if (localPreparation) return;
  const token = {};
  localPreparation = token;
  const release = () => { if (localPreparation === token) localPreparation = null; };
  try { await sendLocalDraft(release); } finally { release(); }
}

async function sendLocalDraft(prepared: () => void): Promise<void> {
  // у Free, пробного периода и Base Докки Code закрыт: писать в чат разработки нельзя, читать можно
  if (!requireDeveloper()) return;
  const state = chat.get();
  if (state.load !== 'ready' || state.reasoningBusy) return;
  if (!attachmentsReady()) return;
  const content = draftContent();
  if (!content) return;
  if (state.running) { await enqueueDraft(); return; }
  const model = selectedLocalModel();
  if (!model) {
    notify('Войдите с помощью Докки');
    openSettings('account');
    return;
  }
  const mode = composer.get().agentMode;
  const version = view.get().version;
  const scope = ui.get().scope;
  const accountId = data.get().account?.user?.id;
  const currentView = () => version === view.get().version && scope === ui.get().scope && accountId === data.get().account?.user?.id;
  // чат без проекта работает в папке «Докки» в документах: её выдаёт сервер приложения при создании чата
  const { project } = view.get();
  let current = view.get().chat;
  if (!current) {
    current = await post<Chat>('/api/chats', { title: 'Новый чат', project_id: project?.id ?? null, mode, model }, { signal: ctl.view.signal });
    if (!currentView()) { void refreshLists().catch(() => undefined); return; }
    adoptChat(current);
    // чат появляется в левом меню сразу, а не после конца первого хода
    void refreshLists().catch(() => undefined);
  }
  const chatId = current.id;
  if (!currentView() || draftContent() !== content) return;
  const draftText = composer.get().text;
  const skills = composer.get().skills;
  composer.set({ text: '', skills: [] });
  const sent = releaseAttachments();
  pinFeed();
  chat.set({
    optimistic: { id: 'pending-user', role: 'user', content, metadata: {}, attachments: sent },
    running: true, turnEnded: false, deltaSeen: false, liveStatus: 'Думаю…', plan: null,
  });
  prepared();
  const controller = new AbortController();
  ctl.stream?.abort();
  ctl.stream = controller;
  clearStreamState();
  let accepted = false;
  try {
    const response = await fetch(`/api/chats/${chatId}/messages`, {
      method: 'POST',
      headers: json,
      body: JSON.stringify({ content, attachment_ids: sent.map((item) => item.id), skill_ids: skills.map((item) => item.id), mode, model, options: {} }),
      signal: controller.signal,
    });
    if (!response.ok) throw await failed(response, 'Не удалось отправить сообщение');
    accepted = true;
    await consume(response, chatId, controller);
  } catch (error) {
    if ((error as Error).name !== 'AbortError') {
      notify((error as Error).message, true);
      if (!accepted && currentView()) {
        const draft = composer.get();
        composer.set({
          text: draft.text || draftText,
          skills: [...draft.skills, ...skills.filter((item) => !draft.skills.some((value) => value.id === item.id))],
          attachments: [...draft.attachments, ...sent.filter((item) => !draft.attachments.some((value) => value.id === item.id))],
        });
      }
    }
  } finally {
    if (ctl.stream === controller) {
      ctl.stream = null;
      await syncLocal();
    }
  }
}

export async function stopLocal(): Promise<void> {
  const chatId = currentChatId();
  const version = view.get().version;
  const state = chat.get();
  const turnId = state.plan?.tracking_turn_id ?? state.turnId ?? state.plan?.turn_id;
  if (chatId == null || !state.running || state.stopping) return;
  chat.set({ stopping: true, liveStatus: 'Останавливаю…' });
  try {
    await post(`/api/chats/${chatId}/turn/cancel`);
    if (stale(version, chatId)) return;
    ctl.stream?.abort();
    ctl.stream = null;
    clearStreamState();
    setRunning(false);
    setPlanTurnState({ active: false, turn: { id: turnId, status: 'cancelled' } });
    await syncLocal();
  } finally {
    if (!stale(version, chatId)) chat.set({ stopping: false });
  }
}

export async function sendCloud(): Promise<void> {
  const state = chat.get();
  if (state.load !== 'ready' || state.running || submission) return;
  const account = data.get().account;
  if (!account?.authenticated) return login();
  if (!attachmentsReady()) return;
  const content = draftContent();
  if (!content) return;
  const scope = ui.get().scope;
  if (scope === 'search' && composer.get().attachments.length) throw new Error('В поиске вложения не поддерживаются. Переключитесь в чат');
  const { cloudModel } = composer.get();
  const body = {
    content,
    mode: scope,
    model: cloudModel[scope] || undefined,
    attachment_ids: composer.get().attachments.map((item) => item.id),
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  };
  const fingerprint = JSON.stringify(body);
  if (pendingSend?.fingerprint !== fingerprint) pendingSend = { fingerprint, key: crypto.randomUUID(), body };
  const pending = pendingSend;
  const { key } = pending;
  const context: Submission = { version: view.get().version, scope, chat: view.get().chat, cancelled: false };
  const draftText = composer.get().text;
  const signal = ctl.view.signal;
  submission = context;
  const current = () => context.version === view.get().version && context.scope === ui.get().scope;
  composer.set({ text: '' });
  const sent = releaseAttachments();
  setRunning(true);
  setStatus('Думаю…');
  pinFeed();
  chat.set({ optimistic: { id: `pending-submit-${key}`, role: 'user', content, metadata: {}, attachments: sent } });
  beginProgress(view.get().chat?.id ?? null);
  try {
    if (!context.chat) {
      createKey ||= crypto.randomUUID();
      context.chat = await api<Chat>(cloudPath('chats'), {
        method: 'POST', headers: { ...json, 'Idempotency-Key': createKey }, body: JSON.stringify({ mode: scope, model: body.model }), signal,
      });
    }
    const id = context.chat.id;
    if (current()) {
      adoptChat(context.chat);
      bindProgress({ chatId: id });
    }
    if (context.cancelled) return;
    const result = await api<{ run_id: string }>(cloudPath(`chats/${encodeURIComponent(String(id))}/messages`), {
      method: 'POST', headers: { ...json, 'Idempotency-Key': key }, body: JSON.stringify({ ...pending.body, client_message_id: key }), signal,
    });
    if (context.cancelled) {
      await post(cloudPath(`runs/${encodeURIComponent(result.run_id)}/cancel`));
      return;
    }
    if (!current()) return;
    pendingSend = null;
    createKey = null;
    cloudCursor.value = '0';
    chat.set({ runId: result.run_id });
    bindProgress({ runId: result.run_id });
    await syncCloud();
    await refreshLists();
    await attachCloud(id);
  } catch (error) {
    if (current()) {
      setRunning(false);
      clearProgress();
      chat.set({ optimistic: null });
      const draft = composer.get();
      composer.set({ text: draft.text || draftText, attachments: [...draft.attachments, ...sent.filter((item) => !draft.attachments.some((value) => value.id === item.id))] });
    }
    throw error;
  } finally {
    if (submission === context) submission = null;
    if (context.cancelled && current()) {
      setRunning(false);
      clearProgress();
      if (view.get().chat) await syncCloud();
      setStatus('Отправка остановлена');
    }
  }
}

/* общие действия, которые выбирают ветку по режиму */
export const syncChat = (): Promise<void> => (isDevelopment() ? syncLocal() : syncCloud());
export const attachStream = (id: Id, after?: string | number): Promise<void> => (isDevelopment() ? attachLocal(id, after) : attachCloud(id, after));
hooks.sync = syncChat;
hooks.attach = attachStream;
hooks.stop = stopLocal;

// в режиме чата со стеклом поле приложения стоит на месте поля сайта: отправляет и останавливает страница сайта
export const send = (): Promise<void> => (siteFieldActive() ? Promise.resolve(sendToSite()) : isDevelopment() ? sendLocal() : sendCloud());
async function stopCloud(): Promise<void> {
  const { runId } = chat.get();
  if (runId) await post(cloudPath(`runs/${encodeURIComponent(runId)}/cancel`));
  else if (submission) { submission.cancelled = true; setStatus('Останавливаю отправку…'); }
}
/** Кнопка отправки во время работы агента: в разработке останавливает ход, в облаке отменяет запуск. */
export const sendOrStop = (): Promise<void> => {
  if (siteFieldActive()) return Promise.resolve(surface.get().siteField?.busy ? stopSite() : sendToSite());
  return chat.get().running ? (isDevelopment() ? stopLocal() : stopCloud()) : send();
};
export const loadOlder = (): Promise<void> => (isDevelopment() ? loadOlderLocal() : loadOlderCloud());
export const reloadChat = (): void => {
  const current = view.get().chat;
  if (current) void enter(view.get().version);
};

/* сообщения */
interface RewindResult { content: string; attachments: Attachment[]; files_rolled_back: number; files_failed: number }

/** Правка в разработке, как в Claude Code: чат откатывается к сообщению, а его текст возвращается в поле ввода. */
async function rewindToMessage(item: Message): Promise<void> {
  const chatId = currentChatId();
  const version = view.get().version;
  if (chatId == null) return;
  const base = `/api/chats/${chatId}/messages/${item.id}/rewind`;
  const preview = await api<{ messages_after: number; file_changes: number }>(base);
  const later = preview.messages_after ? ' Это сообщение и всё, что после него, удалятся из чата.' : ' Сообщение удалится из чата.';
  let files = false;
  if (preview.file_changes) {
    const choice = await choiceDialog(
      'Изменить сообщение?',
      `Чат вернётся к моменту, когда вы его отправили.${later} Текст появится в поле ввода, его можно поправить и отправить заново.\n\nПосле этого сообщения агент менял файлы проекта. Их можно оставить как есть или вернуть к прежнему виду.`,
      'Откатить только чат',
      'Откатить чат и файлы',
    );
    if (choice === null) return;
    files = choice === 'alternative';
  } else if (!(await confirmDialog(
    'Изменить сообщение?',
    `Чат вернётся к моменту, когда вы его отправили.${later} Текст появится в поле ввода, его можно поправить и отправить заново.`,
    'Изменить',
  ))) return;
  if (stale(version, chatId) || chat.get().running) return notify('Чат изменился. Откройте нужное сообщение заново');
  const result = await post<RewindResult>(base, { files });
  if (stale(version, chatId)) return;
  const draft = composer.get();
  const kept = draft.attachments.filter((value) => !result.attachments.some((next) => String(next.id) === String(value.id)));
  composer.set({ text: result.content, attachments: [...kept, ...result.attachments] });
  await syncChat();
  focusComposer();
  if (result.files_failed) notify(`Не удалось вернуть часть файлов (${result.files_failed}). Проверьте изменения проекта`, true);
  else if (result.files_rolled_back) notify('Чат и файлы возвращены к этому сообщению');
}

export async function editMessage(item: Message): Promise<void> {
  if (chat.get().running) return notify('Сначала остановите выполнение');
  const scope = ui.get().scope;
  if (scope === 'development') return rewindToMessage(item);
  const chatId = currentChatId();
  const version = view.get().version;
  const value = await inputDialog('Изменить сообщение', [{ name: 'content', label: 'Сообщение', type: 'textarea', value: item.content, required: true }]);
  if (!value) return;
  if (stale(version, chatId) || ui.get().scope !== scope) return notify('Чат изменился. Откройте нужное сообщение заново');
  const path = `${cloudPath(`chats/${encodeURIComponent(String(chatId))}`)}/messages/${item.id}`;
  await api(path, {
    method: 'PATCH',
    body: JSON.stringify({ ...value, ...(item.version ? { expected_version: item.version } : {}) }),
  });
  if (!stale(version, chatId)) await syncChat();
}

export async function executePlan(snapshot: PlanSnapshot): Promise<void> {
  if (!requireDeveloper()) return;
  const chatId = currentChatId();
  const state = chat.get();
  if (chatId == null || state.running || state.reasoningBusy) return;
  if (!view.get().project) {
    await chooseWorkspace();
    if (!view.get().project) return;
  }
  const controller = new AbortController();
  ctl.stream?.abort();
  ctl.stream = controller;
  chat.set({ running: true, turnEnded: false, deltaSeen: false, liveStatus: 'Думаю…' });
  setStatus('Начинаю выполнение плана…');
  try {
    const response = await fetch(`/api/chats/${chatId}/plan/execute`, {
      method: 'POST', headers: json, body: JSON.stringify({ turn_id: snapshot.turn_id }), signal: controller.signal,
    });
    if (!response.ok) throw await failed(response, 'Не удалось выполнить план');
    composer.set({ agentMode: 'development' });
    const current = view.get().chat;
    if (current) view.set({ chat: { ...current, mode: 'development' } });
    const plan = chat.get().plan;
    if (plan) chat.set({ plan: { ...plan, executed: true, ready: false } });
    await consume(response, chatId, controller);
  } finally {
    if (ctl.stream === controller) {
      ctl.stream = null;
      await syncLocal();
    }
  }
}

/* рабочая папка, модель, режим */
export async function chooseWorkspace(): Promise<void> {
  const current = view.get().project;
  const value = await inputDialog('Рабочая папка', [{
    name: 'project_id', label: 'Проект', type: 'select', value: current?.id ?? '',
    options: [{ value: '', label: 'Без проекта (Документы/Докки)' }, ...data.get().projects.map((item) => ({ value: item.id, label: item.name }))],
  }]);
  if (!value) return;
  const project = data.get().projects.find((item) => item.id === Number(value.project_id)) ?? null;
  const open = view.get().chat;
  if (open) {
    const updated = await patch<Chat>(`/api/chats/${open.id}`, { project_id: project?.id ?? null });
    view.set({ chat: updated });
  }
  view.set({ project });
  void refreshProjectPermissions().catch(() => undefined);
}

export async function changeChatModel(model: string): Promise<void> {
  const open = view.get().chat;
  if (isCloud()) {
    if (open) adoptChat(await patch<Chat>(cloudPath(`chats/${encodeURIComponent(String(open.id))}`), { model, expected_version: open.version }));
    composer.set({ cloudModel: { ...composer.get().cloudModel, [ui.get().scope]: model } });
    return;
  }
  await post('/api/models/select', { model });
  data.set({ settings: { ...data.get().settings, selected_model: model } });
  if (open && view.get().page === 'chat') view.set({ chat: await patch<Chat>(`/api/chats/${open.id}`, { model }) });
  notify('Модель выбрана');
}

export async function changeAgentMode(mode: AgentMode): Promise<void> {
  composer.set({ agentMode: mode });
  const open = view.get().chat;
  if (open && isDevelopment()) view.set({ chat: await patch<Chat>(`/api/chats/${open.id}`, { mode }) });
}

export async function savePermission(value: 'ask' | 'trusted' | 'full'): Promise<void> {
  const previous = data.get().settings.permission_profile;
  if (value === previous) return;
  const owner = data.get().account?.user?.id;
  const version = view.get().version;
  const project = view.get().project;
  let target = value;
  if (value === 'full') {
    const choice = await choiceDialog(
      'Разрешить Докки всё?',
      'В этом режиме Докки выполняет любые действия без подтверждений: изменяет любые файлы на компьютере, в том числе вне проекта, запускает любые команды и программы, меняет настройки. Откатить сделанное получится только там, где сохранена резервная копия.'
        + '\n\nРекомендуем вместо этого автоматический режим: Докки сам проверит каждое действие на нужность для задачи и безопасность перед выполнением, а в сомнительных случаях спросит вас.',
      'Разрешить всё',
      'Включить автоматический режим',
      'alternative',
    );
    if (choice === null) return;
    target = choice === 'alternative' ? 'trusted' : 'full';
  } else if (value === 'trusted' && !(await confirmDialog(
    'Включить автоматический режим?',
    'Докки будет сам проверять каждое действие на нужность для задачи и безопасность и выполнять его без вопросов, если всё в порядке. Сомнительные действия по-прежнему придут вам на подтверждение. Режим действует только для выбранного проекта и текущего аккаунта.',
    'Включить',
  ))) return;
  if (target === previous) return;
  if (owner !== data.get().account?.user?.id || version !== view.get().version || project?.id !== view.get().project?.id) return;
  await saveSettings({
    permission_profile: target,
    ...(target === 'full' ? { confirm_full_access: true } : {}),
    ...(project ? { project_id: project.id } : {}),
  });
}

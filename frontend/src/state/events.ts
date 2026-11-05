/* Живые обновления: события локального сервера (отзыв сессии, изменения чатов с другого устройства),
   плюс подтяжка списка и лимитов, когда окно снова становится видимым, и раз в минуту. */
import { onBrowserSession } from './browser';
import { attachLocal, syncLocal } from './chat';
import { reloadPermissions } from './queue';
import { showRequests } from './chatAgent';
import { markSeen, onQuestion, onRunState } from './chatActivity';
import { chat, ctl, currentChatId, isDevelopment } from './chatCore';
import { data, loadCloud, loadLocal, revoke } from './data';
import { loadLimits } from './limits';
import { onMapUpdated } from './projectMap';
import { refreshRemote } from './remoteControl';
import { ui } from './ui';
import { announceReady, applyUpdateState, loadUpdates } from './updates';

let source: EventSource | null = null;
let timer = 0;
let lastSweep = 0;

const authed = (): boolean => data.get().account?.authenticated === true;

const refreshChats = () => {
  clearTimeout(timer);
  timer = window.setTimeout(() => {
    if (authed() && ui.get().scope !== 'development') void loadCloud().catch(() => undefined);
  }, 300);
};

let limitsTimer = 0;
const refreshLimits = () => {
  clearTimeout(limitsTimer);
  limitsTimer = window.setTimeout(() => { if (authed()) void loadLimits(); }, 400);
};

function sweep(force = false): void {
  if (!authed() || document.visibilityState !== 'visible') return;
  if (!force && Date.now() - lastSweep < 15_000) return;
  lastSweep = Date.now();
  void loadLimits();
  refreshChats();
  // события хода могли потеряться (сон, обрыв шины): состояние чатов в меню сверяется со списком
  if (ui.get().scope === 'development') void loadLocal(false).catch(() => undefined);
}

const kindOf = (event: Event): string => {
  try {
    const value = (JSON.parse((event as MessageEvent<string>).data) as { kind?: unknown }).kind;
    return typeof value === 'string' ? value : 'question';
  } catch {
    return 'question';
  }
};

const chatOf = (event: Event): number | null => {
  try {
    const value = (JSON.parse((event as MessageEvent<string>).data) as { chat_id?: unknown }).chat_id;
    return typeof value === 'number' ? value : null;
  } catch {
    return null;
  }
};

// ход начали с телефона: открытый чат показывает сообщение и подключается к потоку, как будто его отправили здесь
async function remoteTurn(event: Event): Promise<void> {
  const chatId = chatOf(event);
  if (chatId === null || !isDevelopment() || String(currentChatId()) !== String(chatId) || ctl.stream) return;
  await syncLocal();
  if (String(currentChatId()) === String(chatId) && !ctl.stream) await attachLocal(chatId);
}

let started = false;
// карточка вопроса не должна потеряться: без неё ход ждёт ответа вечно
function loadRequests(attempt = 0): void {
  showRequests().catch(() => {
    if (attempt < 3) window.setTimeout(() => loadRequests(attempt + 1), 1000 * (attempt + 1));
  });
}

let lost = false;
function listen(): void {
  if (source) return;
  source = new EventSource('/api/events');
  source.addEventListener('error', () => { lost = true; });
  // связь вернулась: за время обрыва ход мог начаться, закончиться или задать вопрос
  source.addEventListener('open', () => {
    if (!lost) return;
    lost = false;
    sweep(true);
    if (!isDevelopment() || ctl.stream || currentChatId() == null) return;
    void syncLocal().catch(() => undefined);
    loadRequests();
  });
  source.addEventListener('dokky.auth_revoked', () => revoke());
  for (const kind of ['dokky.chat.updated', 'dokky.chat.deleted']) source.addEventListener(kind, refreshChats);
  // агент открыл или закрыл страницу во встроенном браузере
  source.addEventListener('browser.session', (event) => {
    try { onBrowserSession(JSON.parse((event as MessageEvent<string>).data) as { action?: string }); } catch { /* повреждённое событие */ }
  });
  // карта проекта обновилась после правок файлов или новой заметки агента
  source.addEventListener('project_map.updated', (event) => {
    try { onMapUpdated(JSON.parse((event as MessageEvent<string>).data) as { project_id?: unknown; version?: unknown }); } catch { /* повреждённое событие */ }
  });
  source.addEventListener('remote.turn', (event) => { void remoteTurn(event).catch(() => undefined); });
  // новый чат с телефона появляется в списке сразу
  source.addEventListener('remote.chats', () => { void loadLocal(false).catch(() => undefined); refreshRemote(); });
  source.addEventListener('remote.status', refreshRemote);
  // режим сменили с телефона: поле ввода показывает то же
  source.addEventListener('remote.settings', () => {
    void reloadPermissions().catch(() => undefined);
    void syncLocal().catch(() => undefined);
  });
  // агент ждёт ответа: карточка появляется, даже если окно не читает поток этого хода (очередь, переподключение)
  source.addEventListener('agent.request.created', (event) => {
    const chatId = chatOf(event);
    if (chatId !== null) onQuestion(chatId, true, kindOf(event));
    if (chatId === null || !isDevelopment() || String(currentChatId()) !== String(chatId)) return;
    loadRequests();
    if (!ctl.stream) void remoteTurn(event).catch(() => undefined);
  });
  // на запрос агента ответили с телефона: карточка в открытом чате закрывается
  source.addEventListener('agent.request.resolved', (event) => {
    const chatId = chatOf(event);
    if (chatId !== null) onQuestion(chatId, false);
    if (chatId !== null && isDevelopment() && String(currentChatId()) === String(chatId)) loadRequests();
  });
  // ход начался или закончился в любом чате: меню показывает крутилку или синюю точку
  source.addEventListener('agent.run.state', (event) => {
    try {
      const value = JSON.parse((event as MessageEvent<string>).data) as { chat_id?: unknown; active?: unknown };
      if (typeof value.chat_id === 'number') onRunState(value.chat_id, value.active === true);
    } catch { /* повреждённое событие */ }
  });
  // чат компьютера прочитали на сайте или телефоне: синяя точка «готово» в меню снимается и здесь
  source.addEventListener('chat.read', (event) => {
    const chatId = chatOf(event);
    if (chatId !== null) markSeen(chatId);
  });
  // обновление приложения: состояние для настроек и точки у аккаунта
  source.addEventListener('update.state', (event) => {
    try { applyUpdateState(JSON.parse((event as MessageEvent<string>).data)); } catch { /* повреждённое событие */ }
  });
  source.addEventListener('update.ready', (event) => {
    try { announceReady(String((JSON.parse((event as MessageEvent<string>).data) as { version?: unknown }).version ?? '')); } catch { /* повреждённое событие */ }
  });
  // оплата или смена плана на сайте приходят событиями аккаунта: лимиты и план перечитываются почти сразу
  for (const kind of ['dokky.quota.updated', 'dokky.profile.updated']) source.addEventListener(kind, refreshLimits);
}

export function startEvents(): void {
  listen();
  if (started) return;
  started = true;
  refreshRemote();
  void loadUpdates().catch(() => undefined);
  document.addEventListener('visibilitychange', () => sweep());
  window.addEventListener('focus', () => sweep());
  window.setInterval(() => sweep(true), 60_000);
  // страховка: если событие о запросе потерялось (сон, обрыв шины), карточка всё равно появится
  window.setInterval(() => {
    if (document.visibilityState !== 'visible' || !isDevelopment() || !chat.get().running || chat.get().requests.length) return;
    void showRequests().catch(() => undefined);
  }, 4000);
  window.addEventListener('beforeunload', () => source?.close(), { once: true });
}

/** После входа поток событий открывается заново: прежний мог быть создан без аккаунта. */
export function restartEvents(): void {
  source?.close();
  source = null;
  listen();
}

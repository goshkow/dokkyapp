/* Серверная сессия разработки: режим рассуждений (Light, Pro, Max), сжатие контекста, продление кэша. */
import { api, patch, post, put } from '../lib/api';
import { CACHE_ACTIVE_MS, keepaliveDelay } from '../lib/cacheLease';
export { keepaliveDelay } from '../lib/cacheLease';
import type { Id } from '../lib/types';
import { applyBillingLimits } from './limits';
import { chat, currentChatId, isDevelopment, stale } from './chatCore';
import type { CodingSession } from './chatTypes';
import { data, applySettings } from './data';
import { confirmDialog, notify } from './overlay';
import { view } from './view';

export const reasoningModes = ['light', 'pro', 'max'] as const;
export type ReasoningMode = (typeof reasoningModes)[number];

/** Выбранный, но ещё не применённый режим показывается как текущий: он вступит в силу со следующим сообщением. */
export function currentReasoningMode(): ReasoningMode {
  const session = chat.get().coding;
  const value = session?.pending_reasoning_mode || session?.reasoning_mode || data.get().settings.coding_reasoning_mode;
  return (reasoningModes as readonly string[]).includes(value) ? (value as ReasoningMode) : 'pro';
}

let request = 0;

export async function loadCodingSession(chatId: Id, version: number): Promise<void> {
  if (chat.get().reasoningBusy) return;
  const account = data.get().account;
  // тестовый провайдер без входа не создаёт серверные сессии
  if (account?.internal_test && !account.authenticated) return;
  const mine = ++request;
  const before = chat.get().usage;
  try {
    const session = await api<CodingSession>(`/api/chats/${chatId}/coding-session`);
    if (mine !== request || stale(version, chatId) || chat.get().reasoningBusy) return;
    const current = chat.get().coding;
    if (current && session.version < current.version) return;
    chat.set({ coding: session });
    scheduleKeepalive();
    if (session.last_usage && chat.get().usage === before) chat.set({ usage: session.last_usage });
  } catch (error) {
    if ((error as Error).name === 'AbortError') return;
  }
}

export function resetCodingRequests(): void {
  request += 1;
}

export async function changeReasoningMode(mode: string): Promise<void> {
  const previous = currentReasoningMode();
  const owner = data.get().account?.user?.id;
  const rememberMode = (value: ReasoningMode): void => {
    if (data.get().account?.user?.id === owner) applySettings({ ...data.get().settings, coding_reasoning_mode: value });
  };
  const state = chat.get();
  if (!(reasoningModes as readonly string[]).includes(mode) || mode === previous || state.running || state.reasoningBusy || !isDevelopment()) return;
  const chatId = currentChatId();
  const version = view.get().version;
  const current = () => !stale(version, chatId) && isDevelopment();
  request += 1;
  chat.set({ reasoningBusy: true });
  try {
    if (chatId == null) {
      const saved = await put<{ coding_reasoning_mode?: 'light' | 'pro' | 'max' }>('/api/settings', { coding_reasoning_mode: mode });
      rememberMode(saved.coding_reasoning_mode || (mode as ReasoningMode));
      return;
    }
    const session = await api<CodingSession>(`/api/chats/${chatId}/coding-session`);
    if (!current() || chat.get().running) return;
    chat.set({ coding: session });
    // возврат к режиму, который стоит сейчас, отменяет отложенную смену сразу и без вопросов
    if (session.reasoning_mode === mode) {
      const restored = await patch<CodingSession>(`/api/chats/${chatId}/coding-session`, { reasoning_mode: mode, acknowledge_cache_cost: false });
      rememberMode(mode as ReasoningMode);
      if (current()) chat.set({ coding: restored });
      return;
    }
    const hasHistory = Number(session.history?.length || 0) > 0 || chat.get().messages.length > 0;
    // у модели, которая меняет режим без потери кэша, предупреждать не о чем
    if (hasHistory && !session.cache_preserved && !(await confirmDialog(
      'Сменить режим?',
      'Смена режима может увеличить стоимость следующего запроса, поскольку ИИ может потребоваться повторно обработать историю диалога. Сама история сохранится.',
      'Сменить режим',
    ))) return;
    if (!current() || chat.get().running) return;
    // с историей режим только запоминается: ни плана, ни сжатия, ни запросов к серверу до следующего сообщения
    const updated = await patch<CodingSession>(`/api/chats/${chatId}/coding-session`, { reasoning_mode: mode, acknowledge_cache_cost: hasHistory });
    rememberMode(mode as ReasoningMode);
    if (current()) {
      chat.set({ coding: updated });
      if (hasHistory) notify('Режим сменится со следующим сообщением');
    }
  } finally {
    if (current()) {
      chat.set({ reasoningBusy: false });
      scheduleKeepalive();
    }
  }
}

/* сжатие контекста */
let compactController: AbortController | null = null;

export const cancelCompact = (): void => compactController?.abort();

export async function compactContext(): Promise<void> {
  const chatId = currentChatId();
  const version = view.get().version;
  const state = chat.get();
  if (chatId == null || state.running || state.reasoningBusy) return;
  const controller = (compactController = new AbortController());
  chat.set({ reasoningBusy: true, contextOperation: 'Сжимаю контекст…' });
  try {
    const result = await api<{ session: CodingSession; result: { text: string } }>(`/api/chats/${chatId}/coding-session/compact`, {
      method: 'POST', signal: controller.signal,
    });
    if (!stale(version, chatId)) chat.set({ coding: result.session, contextOperation: result.result.text });
  } catch (error) {
    if (!stale(version, chatId)) {
      chat.set({ contextOperation: (error as Error).name === 'AbortError' ? 'Сжатие отменено. История сохранена.' : (error as Error).message });
    }
    if ((error as Error).name !== 'AbortError') throw error;
  } finally {
    compactController = null;
    if (!stale(version, chatId)) chat.set({ reasoningBusy: false });
  }
}

/* продление кэша разработки */
const keepalive = { timer: 0, lastActivity: 0, chatId: null as Id | null, reported: 0 };

export function recordCodingActivity(): void {
  const chatId = currentChatId();
  if (chatId == null || !isDevelopment() || !data.get().account?.authenticated || document.visibilityState === 'hidden') return;
  const now = Date.now();
  if (keepalive.chatId !== chatId) keepalive.reported = 0;
  keepalive.chatId = chatId;
  keepalive.lastActivity = now;
  if (now - keepalive.reported >= 30_000) {
    keepalive.reported = now;
    void post(`/api/chats/${chatId}/coding-session/activity`).then(scheduleKeepalive).catch(() => { keepalive.reported = 0; });
    return;
  }
  scheduleKeepalive();
}
for (const type of ['pointerdown', 'keydown', 'wheel']) {
  document.addEventListener(type, (event) => {
    if (event.isTrusted && event.target instanceof Element && event.target.closest('.chat-page')) recordCodingActivity();
  }, { passive: true });
}

export function scheduleKeepalive(): void {
  clearTimeout(keepalive.timer);
  const chatId = currentChatId();
  const version = view.get().version;
  const account = data.get().account;
  if (chatId == null || keepalive.chatId !== chatId || !isDevelopment() || !account?.authenticated || document.visibilityState === 'hidden') return;
  const state = chat.get();
  const cache = state.coding?.last_usage?.dokky?.cache;
  const delay = keepaliveDelay(cache, Date.now(), Date.now() - keepalive.lastActivity, data.get().settings.coding_keepalive === true, state.running || state.reasoningBusy);
  if (delay == null) return;
  keepalive.timer = window.setTimeout(async () => {
    const now = chat.get();
    if (stale(version, chatId) || now.running || now.reasoningBusy || data.get().settings.coding_keepalive !== true ||
        !data.get().account?.authenticated || keepalive.chatId !== chatId || document.visibilityState === 'hidden' || Date.now() - keepalive.lastActivity > CACHE_ACTIVE_MS) return;
    try {
      const result = await post<{ warmed?: boolean; cache?: { prompt_tokens?: number; valid_until?: string }; billing?: Parameters<typeof applyBillingLimits>[0] }>(`/api/chats/${chatId}/coding-session/warm`);
      if (stale(version, chatId)) return;
      const session = chat.get().coding;
      if (result.warmed && session?.last_usage?.dokky) {
        chat.set({ coding: { ...session, last_usage: { ...session.last_usage, dokky: { ...session.last_usage.dokky, cache: result.cache } } } });
        applyBillingLimits(result.billing);
        scheduleKeepalive();
      }
    } catch {
      /* следующий рабочий ответ заново запланирует продление */
    }
  }, delay);
}

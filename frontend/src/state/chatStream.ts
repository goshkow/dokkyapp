/* События хода: живой текст, статус, журнал, план, лимиты. Один обработчик на локальный и облачный потоки. */
import { registerMedia } from '../lib/cloud';
import { conciseStatus, liveLabel, type Message } from '../lib/journal';
import { readSse } from '../lib/sse';
import type { Id } from '../lib/types';
import { refreshLists } from './actions';
import { setPlan, setPlanTurnState, showRequests } from './chatAgent';
import {
  chat, currentChatId, followFeed, hooks, isDevelopment, scheduleSync, setRunning, setStatus,
} from './chatCore';
import { handlePlanRequired } from './access';
import { scheduleKeepalive } from './codingSession';
import { revoke } from './data';
import { applyBillingLimits } from './limits';
import { view } from './view';
import { notify } from './overlay';
import { onQualityEvent } from './quality';
import {
  beginProgress, bindProgress, finishProgress, progress, progressAnswer, progressCommentary, progressStatus,
} from './cloudProgress';
import {
  clearBuffers, clearReplays, emitStream, markSynced, moveReplay, pushDelta, resetBuffer, streamBuffers, bufferContent, type Delta,
} from './stream';

/** Курсор облачного потока: после обрыва чтение продолжается с него. */
export const cloudCursor = { value: '0' };

/* сообщения сервера → состояние */
/** Принимает список сообщений: сводит с живым текстом и временными узлами, не теряя уже напечатанного. */
export function applyMessages(incoming: Message[]): void {
  const previous = chat.get();
  const known = new Set(previous.messages.map((message) => String(message.id)));
  const messages = incoming.map((item) => {
    const id = String(item.id);
    markSynced(id);
    if (item.role !== 'assistant') return item;
    if (item.metadata?.status !== 'running') {
      resetBuffer(id);
      return item;
    }
    const buffered = bufferContent(id);
    return buffered !== undefined && buffered.length >= (item.content || '').length ? { ...item, content: buffered } : item;
  });

  let alias = previous.alias;
  let sent = previous.sent;
  let optimistic = previous.optimistic;
  let live = previous.live;
  const fresh = messages.filter((message) => !known.has(String(message.id)));
  // отправленное сообщение занимает место временного: узел не пересоздаётся и не играет вход повторно
  if (optimistic) {
    const match = fresh.find((message) => message.role === 'user' && message.content === optimistic?.content);
    if (match) {
      alias = { ...alias, [String(match.id)]: `m:${optimistic.id}` };
      if (optimistic.attachments?.length) sent = { ...sent, [String(match.id)]: optimistic.attachments };
      optimistic = null;
    }
  }
  // печатавшийся ответ без номера становится сообщением сервера тем же узлом
  const runId = previous.runId;
  const pending = runId ? live.find((message) => String(message.id) === `pending_${runId}`) : undefined;
  if (pending) {
    const match = fresh.find((message) => message.role === 'assistant');
    if (match) {
      alias = { ...alias, [String(match.id)]: `m:${pending.id}` };
      rekeyStream(String(pending.id), String(match.id));
      live = live.filter((message) => message !== pending);
    }
  }
  live = live.filter((message) => !messages.some((item) => String(item.id) === String(message.id)));
  if (optimistic && !previous.running) optimistic = null;
  chat.set({ messages, optimistic, live, alias, sent });
  followFeed();
}

const redirects = new Map<string, string>();
export const resolveStreamId = (id: string): string => redirects.get(id) ?? id;

function rekeyStream(from: string, to: string): void {
  redirects.set(from, to);
  moveReplay(from, to);
  const buffer = streamBuffers.get(from);
  if (buffer) {
    streamBuffers.delete(from);
    streamBuffers.set(to, buffer);
  }
}

export function clearStreamState(): void {
  clearBuffers();
  clearReplays();
  redirects.clear();
  if (frame) cancelAnimationFrame(frame);
  frame = 0;
}

/* порции текста */
let frame = 0;

export function queueDelta(chatId: Id, delta: Delta & { message_id?: string | number }): void {
  if (currentChatId() !== chatId || typeof delta.content !== 'string') return;
  const id = resolveStreamId(String(delta.message_id || 'stream'));
  const base = chat.get().messages.find((message) => String(message.id) === id)?.content ?? '';
  pushDelta(id, delta, base);
  if (!frame) frame = requestAnimationFrame(flushBuffers);
}

export function addLive(id: string): void {
  const state = chat.get();
  if (state.live.some((message) => String(message.id) === id) || state.messages.some((message) => String(message.id) === id)) return;
  chat.set({ live: [...state.live, { id, role: 'assistant', content: bufferContent(id) ?? '', metadata: { status: 'running' } }] });
}

function flushBuffers(): void {
  frame = 0;
  let held = false;
  const known = new Set(chat.get().messages.map((message) => String(message.id)));
  const live = new Set(chat.get().live.map((message) => String(message.id)));
  const now = performance.now();
  for (const [id, buffer] of streamBuffers) {
    if (!buffer.dirty) continue;
    buffer.dirty = false;
    if (!known.has(id) && !live.has(id)) {
      // пока сервер не ответил, неизвестно, комментарий это или итог: не создаём блок вне журнала
      buffer.heldAt ??= now;
      if (isDevelopment() && chat.get().running && !buffer.synced && now - buffer.heldAt < 700) {
        if (!buffer.syncAsked) {
          buffer.syncAsked = true;
          scheduleSync(0);
        }
        buffer.dirty = true;
        held = true;
        continue;
      }
      addLive(id);
    }
    emitStream(id, buffer.content);
  }
  followFeed();
  if (held) frame = requestAnimationFrame(flushBuffers);
}

export function resetStreamMessage(delta: { message_id?: string | number }): void {
  const id = resolveStreamId(String(delta.message_id || 'stream'));
  resetBuffer(id);
  emitStream(id, '');
}

/* обработка событий */
const terminal = ['turn.completed', 'turn.failed', 'turn.cancelled'];

function localJournal(kind: string, data: Record<string, any>): void {
  if (!isDevelopment()) return;
  if (data.channel && !['commentary', 'final'].includes(data.channel)) return;
  if (data.turn_id) chat.set({ turnId: data.turn_id });
  if (kind === 'agent.waiting' || kind === 'tool.waiting') return;
  const patch: Partial<ReturnType<typeof chat.get>> = {};
  if (kind === 'turn.started') {
    patch.liveStatus = 'Думаю…';
    patch.turnEnded = false;
    patch.deltaSeen = false;
  } else if (kind === 'message.delta') patch.deltaSeen = true;
  else if (kind === 'message.updated') scheduleSync(0);
  else if (kind === 'agent.status') {
    patch.liveStatus = conciseStatus(data.text || 'Агент работает…');
    scheduleSync();
  } else if (['agent.step', 'agent.input_required', 'context.compacting', 'context.compacted'].includes(kind)) {
    patch.liveStatus = data.text || (kind === 'agent.step' ? 'Думаю…' : kind === 'agent.input_required' ? 'Ожидаю подтверждение' : 'Контекст сохранён');
    scheduleSync();
  } else if (kind === 'tool.progress') {
    // путь берётся из ещё недописанного вызова: «Редактирую templates/landing.html · 12 КБ»
    const kb = Math.round((data.bytes_received || 0) / 1024);
    patch.liveStatus = data.name ? `${liveLabel({ event: 'tool.call', data: { ...data, arguments: { path: data.path } } })}${kb ? ` · ${kb} КБ` : ''}` : 'Готовлю действие…';
  } else if (kind === 'tool.call' || kind === 'tool.result') patch.liveStatus = liveLabel({ event: kind, data });
  else if (terminal.includes(kind)) {
    patch.turnEnded = true;
    scheduleSync(0);
  }
  if (Object.keys(patch).length) chat.set(patch);
}

function cloudProgressEvent(kind: string, data: Record<string, any>): void {
  if (isDevelopment()) return;
  const chatId = currentChatId();
  let live = progress.get().live;
  if (!live && chat.get().running) {
    beginProgress(chatId, data.run_id || chat.get().runId);
    live = progress.get().live;
  }
  if (!live || live.chatId !== chatId) return;
  if (data.run_id) bindProgress({ runId: data.run_id });
  if (kind === 'activity.updated') {
    const activity = data.activity || {};
    if (activity.kind === 'commentary') progressCommentary(activity.text || '');
    else if (activity.kind !== 'title' && activity.text) progressStatus(activity.kind || 'status', activity.text);
  } else if (kind === 'search.started') progressStatus('web_search', 'Ищу информацию…');
  else if (kind === 'search.source') {
    const source = data.source || data;
    progressStatus('web_search', `Изучаю источник: ${source.title || source.url || 'страницу'}`);
  } else if (kind === 'message.delta' && data.content) progressAnswer();
  else if (terminal.includes(kind)) finishProgress(data.message_id);
}

/** Читает поток событий до конца. Возвращает управление, когда поток закрыт или остановлен. */
export async function consume(response: Response, chatId: Id, controller: AbortController): Promise<void> {
  // пока шёл await, пользователь мог открыть другой чат или режим: итоги старого потока туда не применяются
  const development = isDevelopment();
  const away = () => currentChatId() !== chatId || isDevelopment() !== development;
  const gone = () => controller.signal.aborted || away();
  for await (const event of readSse(response, controller.signal)) {
    const { event: kind, data: payload } = event;
    if (!isDevelopment()) cloudCursor.value = event.id || cloudCursor.value;
    if (away()) continue;
    localJournal(kind, payload);
    cloudProgressEvent(kind, payload);
    if (kind === 'stream.reset') {
      await hooks.sync();
      if (gone()) return;
      clearStreamState();
      if (payload.live_message) streamBuffers.set(String(payload.live_message.id), { content: payload.live_message.content, dirty: false });
      controller.abort();
      window.setTimeout(() => {
        if (currentChatId() === chatId && chat.get().running) void hooks.attach(chatId, payload.last_sequence || payload.last_event_id);
      }, 100);
      continue;
    }
    if (kind === 'message.delta') queueDelta(chatId, payload as Delta & { message_id?: string });
    if (kind === 'message.reset') resetStreamMessage(payload);
    if (kind === 'agent.usage') {
      const usage = { usage: payload.usage || {}, dokky: payload.dokky || {} };
      const coding = chat.get().coding;
      chat.set({ usage, ...(coding ? { coding: { ...coding, last_usage: usage } } : {}) });
      applyBillingLimits(payload.dokky?.billing);
      scheduleKeepalive();
    }
    if (kind === 'turn.started') setPlanTurnState({ active: true, turn: { id: payload.turn_id ?? chat.get().turnId ?? undefined } });
    if (kind === 'turn.started' && payload.chat_title) retitle(payload.chat_title);
    if (kind === 'agent.waiting') setStatus(`Докки обрабатывает запрос · ${payload.seconds || 1} с`);
    if (kind === 'agent.step') setStatus('Агент выполняет задачу…');
    if (kind === 'search.started') setStatus('Докки ищет информацию…');
    if (kind === 'search.source') {
      const source = payload.source || payload;
      setStatus(`Найден источник: ${source.title || source.url || 'источник'}`);
    }
    if (kind === 'artifact.updated' || kind === 'blocks.updated') {
      registerMedia(payload.media);
      scheduleSync(400);
    }
    if (kind === 'activity.updated') {
      const activity = payload.activity || {};
      if (activity.kind === 'title' && activity.text) retitle(activity.text);
      else if (activity.text) setStatus(activity.text);
    }
    if (kind === 'auth.revoked') revoke();
    if (kind === 'agent.status') setStatus(payload.text || 'Агент работает…');
    if (kind === 'agent.warning') notify(payload.error || 'Предупреждение агента', true);
    if (kind === 'tool.waiting') setStatus(`${liveLabel({ event: 'tool.call', data: payload })} · ${payload.seconds} с`);
    if (kind === 'tool.progress') {
      // подпись собирает компьютер по имени и пути действия, которое модель ещё пишет
      setStatus(payload.text || (payload.name ? liveLabel({ event: 'tool.call', data: { name: payload.name, display_name: payload.display_name, arguments: { path: payload.path } } }) : 'Подготавливаю действие…'));
    }
    if (kind === 'tool.call' || kind === 'tool.result') scheduleSync();
    // гейты, red/green и решения меняют карточку «Качество»
    if (kind === 'quality.gates' || kind === 'quality.updated' || kind === 'turn.completed'
      || (kind === 'tool.result' && ['qa.run', 'quality.run', 'plan.update'].includes(String(payload.name ?? '')))) onQualityEvent();
    if (kind === 'plan.updated') setPlan({ ...payload, turn_id: payload.turn_id ?? chat.get().turnId ?? undefined });
    if (kind === 'agent.input_required') await showRequests();
    if (kind === 'context.compacting' || kind === 'context.compacted') setStatus(payload.text || 'Сжимаю контекст…');
    if (kind === 'turn.failed') {
      if (payload.code === 'plan_required') {
        // подписка закончилась или не включает режим: задача остановлена, всё сделанное и очередь сохранены, запись закрыта
        handlePlanRequired();
        notify('Докки Code сейчас недоступен на вашем тарифе. Работа сохранена, чат можно читать. Обновите план, чтобы продолжить', true);
      } else notify(`Агент остановился: ${payload.error?.message || payload.error || 'Неизвестная ошибка'}`, true);
    }
    if (terminal.includes(kind)) {
      setPlanTurnState({ active: false, turn: { id: payload.turn_id ?? chat.get().turnId ?? undefined, status: kind.slice(5) } });
      clearBuffers();
      await hooks.sync();
      if (gone()) return;
      setRunning(false);
      if (kind === 'turn.completed') setStatus('Готово');
      if (kind === 'turn.cancelled') setStatus('Выполнение остановлено');
      await refreshLists();
    }
  }
}

/** Название чата пришло с сервера: обновляем и открытый чат, и заголовок. */
function retitle(title: string): void {
  const current = view.get().chat;
  if (current) view.set({ chat: { ...current, title } });
}

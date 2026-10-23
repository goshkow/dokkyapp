/* Поиск Докки: запрос создаёт чат режима search, поток событий даёт статус, источники и текст ответа. */
import { api, cloudPath, post } from '../lib/api';
import { cloudMessage, safeSource, type CloudSource } from '../lib/cloud';
import type { Message } from '../lib/journal';
import { createStore, useStore } from '../lib/store';
import type { Chat } from '../lib/types';
import { refreshLists } from './actions';
import { composer } from './composer';
import { data, login, revoke } from './data';
import { selectedCloudModel } from './models';
import { emitStream } from './stream';
import { readSse } from '../lib/sse';

export const ANSWER_STREAM = 'search:answer';

export interface SearchSource {
  id: string;
  url: string;
  title: string;
  snippet: string;
}

export interface SearchState {
  busy: boolean;
  query: string;
  hasQuery: boolean;
  hasAnswer: boolean;
  status: string;
  reading: SearchSource[];
  sources: SearchSource[];
  error: string;
  /** Итоговый ответ сервера: после него показываются источники и кнопка продолжения. */
  final: Message | null;
  history: Chat[];
  chat: Chat | null;
}

const initial = (): SearchState => ({
  busy: false, query: '', hasQuery: false, hasAnswer: false, status: '', reading: [], sources: [], error: '', final: null, history: [], chat: null,
});

export const search = createStore<SearchState>(initial());
export const useSearch = <S,>(selector: (state: SearchState) => S): S => useStore(search, selector);

const run = { token: 0, cursor: '0', runId: null as string | null, controller: null as AbortController | null, answer: '' };

export const currentAnswer = (): string => run.answer;

export function sourceInfo(raw: unknown): SearchSource | null {
  const value = raw as { source?: CloudSource } & CloudSource;
  const source = value?.source ?? value ?? {};
  if (!safeSource(source)) return null;
  return { id: String(source.id || source.url), url: source.url, title: String(source.title || ''), snippet: String(source.snippet || '') };
}

function addSource(source: SearchSource): void {
  const state = search.get();
  if (state.sources.some((item) => item.url === source.url)) return;
  search.set({ sources: [...state.sources, source] });
}

function handle(kind: string, payload: Record<string, any>): boolean {
  if (kind === 'search.started') search.set({ status: 'Ищу информацию…' });
  else if (kind === 'search.source') {
    const source = sourceInfo(payload);
    if (source) {
      addSource(source);
      search.set({ reading: [...search.get().reading, source].slice(-4), status: `Изучаю источник: ${new URL(source.url).hostname.replace(/^www\./, '')}` });
    }
  } else if (kind === 'activity.updated') {
    const activity = payload.activity || {};
    if (activity.text && !['title', 'commentary'].includes(activity.kind)) search.set({ status: activity.text });
  } else if (kind === 'message.delta' && typeof payload.content === 'string') {
    if (payload.replace) run.answer = payload.content;
    else if (Number.isInteger(payload.offset)) run.answer = run.answer.slice(0, payload.offset) + payload.content;
    else run.answer += payload.content;
    if (!search.get().hasAnswer) search.set({ hasAnswer: true, status: '' });
    emitStream(ANSWER_STREAM, run.answer);
  } else if (kind === 'auth.revoked') revoke();
  else if (kind === 'turn.failed') {
    search.set({ error: payload.error?.message || payload.error || 'Поиск завершился с ошибкой', status: '' });
    return true;
  } else if (['turn.completed', 'turn.cancelled'].includes(kind)) return true;
  return false;
}

async function readEvents(runId: string, token: number): Promise<void> {
  for (let attempt = 0; attempt < 40 && token === run.token; attempt += 1) {
    const controller = (run.controller = new AbortController());
    let terminal = false;
    try {
      const response = await fetch(cloudPath(`runs/${encodeURIComponent(runId)}/events?after=${encodeURIComponent(run.cursor)}`), { signal: controller.signal });
      if (response.status === 401) return void revoke();
      if (!response.ok) throw new Error('Не удалось получить ответ поиска');
      for await (const event of readSse(response, controller.signal)) {
        if (token !== run.token) break;
        run.cursor = event.id || run.cursor;
        if (handle(event.event, event.data)) {
          terminal = true;
          break;
        }
      }
    } catch (error) {
      if ((error as Error).name === 'AbortError') return;
      if (attempt > 3) throw error;
    }
    if (terminal) return;
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
}

async function finish(chatId: string | number, token: number): Promise<void> {
  const page = await api<{ items: Message[] }>(cloudPath(`chats/${encodeURIComponent(String(chatId))}/messages?limit=10`));
  if (token !== run.token) return;
  const message = [...page.items].reverse().find((item) => item.role === 'assistant' && item.content);
  search.set({ status: '' });
  if (message) showMessage(cloudMessage(message));
}

function showMessage(message: Message): void {
  run.answer = message.content;
  search.set({ final: message, hasAnswer: true, sources: mergeCitations(message) });
}

function mergeCitations(message: Message): SearchSource[] {
  const current = search.get().sources;
  const known = new Set(current.map((item) => item.url));
  const extra = ((message.citations ?? []) as CloudSource[]).map(sourceInfo).filter((item): item is SearchSource => Boolean(item) && !known.has((item as SearchSource).url));
  return [...current, ...extra];
}

export async function runSearch(raw: string): Promise<void> {
  const query = raw.trim();
  if (!query || search.get().busy) return;
  if (!data.get().account?.authenticated) return login();
  const token = ++run.token;
  Object.assign(run, { cursor: '0', runId: null, answer: '' });
  search.set({ ...initial(), history: search.get().history, busy: true, query, hasQuery: true, status: 'Ищу информацию…' });
  emitStream(ANSWER_STREAM, '');
  const model = selectedCloudModel('search') || undefined;
  try {
    const chat = await api<Chat>(cloudPath('chats'), {
      method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ mode: 'search', title: query.slice(0, 80), model }),
    });
    search.set({ chat });
    if (token !== run.token) return;
    const result = await api<{ run_id: string }>(cloudPath(`chats/${encodeURIComponent(String(chat.id))}/messages`), {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({
        content: query, mode: 'search', model, attachment_ids: [], timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, client_message_id: crypto.randomUUID(),
      }),
    });
    run.runId = result.run_id;
    await readEvents(result.run_id, token);
    if (token === run.token) await finish(chat.id, token);
    search.set({ history: [] });
  } catch (error) {
    if (token === run.token && (error as Error).name !== 'AbortError') search.set({ error: (error as Error).message, status: '' });
  } finally {
    if (token === run.token) {
      search.set({ busy: false, status: '' });
      void refreshLists().catch(() => undefined);
    }
  }
}

export async function stopSearch(): Promise<void> {
  const id = run.runId;
  run.controller?.abort();
  if (id) await post(cloudPath(`runs/${encodeURIComponent(id)}/cancel`)).catch(() => undefined);
  run.token += 1;
  const chat = search.get().chat;
  search.set({ busy: false, status: '' });
  if (chat) void finish(chat.id, run.token).catch(() => undefined);
}

export async function loadHistory(): Promise<void> {
  if (search.get().history.length || !data.get().account?.authenticated) return;
  try {
    const page = await api<{ items: Chat[] }>(cloudPath('chats?mode=search&limit=12'));
    search.set({ history: page.items ?? [] });
  } catch {
    /* подсказки необязательны */
  }
}

/** Сохранённый поиск открывается без нового запроса. */
export async function openStoredSearch(chat: Chat): Promise<void> {
  const token = ++run.token;
  Object.assign(run, { cursor: '0', runId: null, answer: '' });
  search.set({ ...initial(), history: search.get().history, query: chat.title, chat, hasQuery: true, status: 'Открываю результаты…' });
  emitStream(ANSWER_STREAM, '');
  const page = await api<{ items: Message[] }>(cloudPath(`chats/${encodeURIComponent(String(chat.id))}/messages?limit=10`));
  if (token !== run.token) return;
  search.set({ status: '' });
  const user = page.items.find((item) => item.role === 'user');
  if (user?.content) search.set({ query: user.content });
  const message = [...page.items].reverse().find((item) => item.role === 'assistant' && item.content);
  if (message) showMessage(cloudMessage(message));
  else search.set({ error: 'В этом поиске нет сохранённого ответа' });
}

export function resetSearch(): void {
  run.token += 1;
  run.controller?.abort();
  Object.assign(run, { cursor: '0', runId: null, answer: '' });
  search.set(initial());
  emitStream(ANSWER_STREAM, '');
}

export function continueInChat(setScope: () => Promise<void>): Promise<void> {
  const query = search.get().query;
  return setScope().then(() => {
    composer.set({ text: `Продолжим по поиску «${query}»: `, focus: composer.get().focus + 1 });
  });
}

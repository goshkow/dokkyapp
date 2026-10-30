/* Состояние чатов режима кода для меню, как на сайте Докки: агент работает (крутилка), ждёт ответа человека
   (оранжевая точка) или закончил, пока чат не открыт (синяя точка до открытия чата). */
import { readJson, writeJson } from '../lib/storage';
import { createStore, useStore } from '../lib/store';
import type { Chat } from '../lib/types';
import { data } from './data';
import { ui } from './ui';
import { view } from './view';

export type Activity = 'working' | 'question';
export type ChatMark = Activity | 'done' | null;

interface ActivityState {
  activity: Record<string, Activity>;
  done: Record<string, true>;
}

const DONE_KEY = 'dokky.code.done-chats';
const DONE_LIMIT = 200;

const restored = (): Record<string, true> => {
  const list = readJson<unknown>(DONE_KEY, []);
  return Array.isArray(list) ? Object.fromEntries(list.filter((id) => typeof id === 'string').slice(-DONE_LIMIT).map((id) => [id, true as const])) : {};
};

export const chatActivity = createStore<ActivityState>({ activity: {}, done: restored() });

export const useChatMark = (id: Chat['id']): ChatMark => useStore(chatActivity, (state) => state.activity[String(id)] ?? (state.done[String(id)] ? 'done' : null));

/** Открытый в режиме кода чат человек и так видит: отметка «готово» ему не нужна. */
const viewing = (id: string): boolean =>
  ui.get().scope === 'development' && String(view.get().chat?.id ?? '') === id && document.visibilityState === 'visible';

function saveDone(done: Record<string, true>): void {
  writeJson(DONE_KEY, Object.keys(done).slice(-DONE_LIMIT));
}

function update(id: string, next: Activity | null): void {
  const { activity, done } = chatActivity.get();
  const previous = activity[id];
  if (previous === next) return;
  const patch: Partial<ActivityState> = {};
  const activityNext = { ...activity };
  if (next) activityNext[id] = next;
  else delete activityNext[id];
  patch.activity = activityNext;
  if (next && done[id]) {
    const doneNext = { ...done };
    delete doneNext[id];
    patch.done = doneNext;
  } else if (!next && previous && !viewing(id)) {
    patch.done = { ...done, [id]: true };
  }
  chatActivity.set(patch);
  if (patch.done) saveDone(patch.done);
}

/** Список чатов с сервера - источник правды: ходы, закончившиеся без события (перезапуск окна), тоже снимаются. */
export function applyChatActivity(chats: Chat[]): void {
  const known = new Set(chats.map((chat) => String(chat.id)));
  for (const chat of chats) update(String(chat.id), chat.activity ?? null);
  for (const id of Object.keys(chatActivity.get().activity)) if (!known.has(id)) update(id, null);
  const { done } = chatActivity.get();
  const kept = Object.fromEntries(Object.keys(done).filter((id) => known.has(id)).map((id) => [id, true as const]));
  if (Object.keys(kept).length !== Object.keys(done).length) {
    chatActivity.set({ done: kept });
    saveDone(kept);
  }
}

export function onRunState(chatId: number, active: boolean): void {
  const id = String(chatId);
  if (!active) update(id, null);
  else if (chatActivity.get().activity[id] !== 'question') update(id, 'working');
}

export function onQuestion(chatId: number, asking: boolean): void {
  const id = String(chatId);
  if (asking) update(id, 'question');
  else if (chatActivity.get().activity[id] === 'question') update(id, 'working');
}

export function markSeen(chatId: Chat['id'] | null | undefined): void {
  const id = String(chatId ?? '');
  const { done } = chatActivity.get();
  if (!id || !done[id]) return;
  const next = { ...done };
  delete next[id];
  chatActivity.set({ done: next });
  saveDone(next);
}

// открытый чат снимает свою синюю точку; так же при возврате в окно
let lastSeen = '';
const seeOpen = () => {
  const id = String(view.get().chat?.id ?? '');
  if (ui.get().scope !== 'development' || document.visibilityState !== 'visible') return;
  if (id && (id !== lastSeen || chatActivity.get().done[id])) markSeen(id);
  lastSeen = id;
};
view.subscribe(seeOpen);
// список чатов приходит с сервера при загрузке и по событиям: состояние меню сверяется с ним
let lastChats: Chat[] | null = null;
data.subscribe(() => {
  const chats = data.get().localChats;
  if (chats === lastChats) return;
  lastChats = chats;
  applyChatActivity(chats);
});
ui.subscribe(seeOpen);
document.addEventListener('visibilitychange', seeOpen);

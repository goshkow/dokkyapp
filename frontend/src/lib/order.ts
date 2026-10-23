import { readJson, writeJson } from './storage';
import type { Chat } from './types';

const KEY = 'koddi-chat-order';

type Orders = Record<string, string[]>;

/** Порядок чатов, заданный перетаскиванием, помнится между запусками. Новые чаты идут по дате над ним. */
export function applyOrder(chats: Chat[], group: string): Chat[] {
  const order = readJson<Orders>(KEY, {})[group];
  if (!order?.length) return chats;
  const rank = new Map(order.map((id, index) => [id, index]));
  const known = chats.filter((chat) => rank.has(String(chat.id))).sort((a, b) => (rank.get(String(a.id)) ?? 0) - (rank.get(String(b.id)) ?? 0));
  const fresh = chats.filter((chat) => !rank.has(String(chat.id)));
  return [...fresh, ...known];
}

export function moveChat(chats: Chat[], group: string, from: string, to: string, after: boolean): void {
  const ids = chats.map((chat) => String(chat.id)).filter((id) => id !== from);
  const target = ids.indexOf(to);
  if (target < 0) return;
  ids.splice(target + (after ? 1 : 0), 0, from);
  writeJson(KEY, { ...readJson<Orders>(KEY, {}), [group]: ids });
}

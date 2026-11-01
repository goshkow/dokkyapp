/* Поиск по чату (Cmd+F): сервер ищет по всей переписке, найденное подгружается и подсвечивается через CSS Highlight - разметку сообщений не трогаем. */
import { api } from '../lib/api';
import { createStore, useStore } from '../lib/store';
import { loadOlderLocal } from './chat';
import { chat, releaseFeed } from './chatCore';
import { focusComposer } from './composer';
import { notify } from './overlay';
import { view } from './view';

export interface ChatSearchState {
  open: boolean;
  /** Сообщает полю, что пора забрать фокус, даже если строка уже открыта. */
  focusTick: number;
  query: string;
  busy: boolean;
  messages: (number | string)[];
  total: number;
  index: number;
}

const closed = (): ChatSearchState => ({ open: false, focusTick: 0, query: '', busy: false, messages: [], total: 0, index: 0 });
export const chatSearch = createStore<ChatSearchState>(closed());
export const useChatSearch = <S,>(selector: (state: ChatSearchState) => S): S => useStore(chatSearch, selector);

const HIGHLIGHT = 'chat-search';
const FEED_WAIT_MS = 1500;
let request = 0;
let timer = 0;

export function clearHighlight(): void {
  try { CSS.highlights?.delete(HIGHLIGHT); } catch { /* старый движок без CSS Highlight */ }
  document.querySelector('[data-search-current]')?.removeAttribute('data-search-current');
}

/** Диапазоны совпадений в узле; совпадение через границу разметки (жирное слово) не считается. */
export function matchRanges(root: Node, query: string): Range[] {
  const pattern = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'giu');
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const ranges: Range[] = [];
  for (let node = walker.nextNode(); node && ranges.length < 200; node = walker.nextNode()) {
    for (const match of (node.textContent ?? '').matchAll(pattern)) {
      const range = document.createRange();
      range.setStart(node, match.index);
      range.setEnd(node, match.index + match[0].length);
      ranges.push(range);
    }
  }
  return ranges;
}

const messageNode = (id: number | string): HTMLElement | null =>
  document.getElementById('feed')?.querySelector<HTMLElement>(`[data-message-id="${CSS.escape(String(id))}"]`) ?? null;

/** Сообщения старше загруженных дочитываются порциями; в окне держится последняя тысяча сообщений. */
async function ensureLoaded(id: number | string, token: number): Promise<boolean> {
  if (typeof id !== 'number') return chat.get().messages.some((item) => item.id === id);
  while (token === request) {
    const { messages } = chat.get();
    if (messages.some((item) => item.id === id)) return true;
    const first = Number(messages[0]?.id ?? 0);
    if (!first || first < id) return false;
    await loadOlderLocal();
    if (Number(chat.get().messages[0]?.id ?? 0) === first) return false;
  }
  return false;
}

async function waitForNode(id: number | string, token: number): Promise<HTMLElement | null> {
  const deadline = performance.now() + FEED_WAIT_MS;
  while (token === request && performance.now() < deadline) {
    const node = messageNode(id);
    if (node) return node;
    await new Promise((resolve) => requestAnimationFrame(resolve));
  }
  return null;
}

async function show(index: number): Promise<void> {
  const state = chatSearch.get();
  const id = state.messages[index];
  clearHighlight();
  if (id === undefined) return;
  chatSearch.set({ index });
  const token = ++request;
  if (!(await ensureLoaded(id, token))) {
    if (token === request) notify('Это сообщение слишком давнее: в окне показана последняя тысяча сообщений');
    return;
  }
  const node = await waitForNode(id, token);
  if (!node || token !== request) return;
  releaseFeed();
  const ranges = matchRanges(node, state.query.trim());
  node.setAttribute('data-search-current', '');
  try {
    if (ranges.length && CSS.highlights) CSS.highlights.set(HIGHLIGHT, new Highlight(...ranges));
  } catch { /* старый движок: остаётся выделенное сообщение */ }
  const first = ranges[0]?.startContainer.parentElement ?? node;
  first.scrollIntoView({ block: 'center' });
}

async function run(query: string): Promise<void> {
  const token = ++request;
  const needle = query.trim();
  clearHighlight();
  if (!needle) {
    chatSearch.set({ busy: false, messages: [], total: 0, index: 0 });
    return;
  }
  const current = view.get().chat;
  if (!current) return;
  chatSearch.set({ busy: true });
  let messages: (number | string)[] = [];
  let total = 0;
  try {
    if (typeof current.id === 'number') {
      ({ messages, total } = await api<{ messages: number[]; total: number }>(`/api/chats/${current.id}/messages/search?q=${encodeURIComponent(needle)}`));
    } else {
      // облачный чат: на клиенте только загруженная часть
      const lowered = needle.toLocaleLowerCase();
      const found = chat.get().messages.filter((item) => String(item.content ?? '').toLocaleLowerCase().includes(lowered));
      messages = found.map((item) => item.id);
      total = found.length;
    }
  } catch (error) {
    if (token === request) chatSearch.set({ busy: false, messages: [], total: 0, index: 0 });
    notify((error as Error).message || 'Поиск не удался', true);
    return;
  }
  if (token !== request || !chatSearch.get().open) return;
  // с конца: ищут обычно недавнее
  chatSearch.set({ busy: false, messages, total, index: Math.max(0, messages.length - 1) });
  if (messages.length) void show(messages.length - 1);
}

export function setChatSearchQuery(query: string): void {
  chatSearch.set({ query });
  window.clearTimeout(timer);
  timer = window.setTimeout(() => void run(query), 180);
}

export function stepChatSearch(direction: 1 | -1): void {
  const { messages, index } = chatSearch.get();
  if (messages.length) void show((index + direction + messages.length) % messages.length);
}

export function openChatSearch(): void {
  const { page, chat: current } = view.get();
  if (page !== 'chat' || !current) return;
  // на macOS поле ввода - свой веб-вид: клавиатура сначала возвращается в главный
  try { window.koddiWindowBridge?.focusView?.('main'); } catch { /* окно без моста */ }
  chatSearch.set((state) => ({ open: true, focusTick: state.focusTick + 1 }));
}

export function closeChatSearch(refocus = true): void {
  if (!chatSearch.get().open) return;
  window.clearTimeout(timer);
  request += 1;
  clearHighlight();
  chatSearch.set(closed());
  if (refocus) focusComposer();
}

let lastChat: unknown = null;
view.subscribe(() => {
  const id = view.get().chat?.id ?? null;
  if (id !== lastChat) {
    lastChat = id;
    closeChatSearch(false);
  }
});

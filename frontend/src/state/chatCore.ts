/* Состояние открытого чата: сообщения, ход выполнения, план, изменения, запросы агента.
   Потоковые порции текста сюда не попадают: они идут напрямую в узлы (см. stream.ts). */
import { createStore, useStore } from '../lib/store';
import type { Chat, Id } from '../lib/types';
import type { Message } from '../lib/journal';
import { data } from './data';
import { ui } from './ui';
import { view } from './view';
import type {
  AgentRequest, Change, CodingSession, PlanSnapshot, QueueSnapshot, UsageBundle,
} from './chatTypes';

export type LoadState = 'ready' | 'loading' | 'failed';

export interface ChatState {
  load: LoadState;
  loadError: string;
  /** Сообщения сервера в порядке времени (в режиме разработки - вместе с подгруженными старыми). */
  messages: Message[];
  /** Отправленное сообщение, которое сервер ещё не вернул. */
  optimistic: Message | null;
  /** Ответы, которые уже печатаются, но ещё не пришли от сервера. */
  live: Message[];
  /** Ключи узлов ленты: сообщение сервера занимает место временного без перерисовки. */
  alias: Record<string, string>;
  running: boolean;
  stopping: boolean;
  runId: string | null;
  status: string;
  /** Подпись идущего хода в журнале разработки («Думаю…», «Читаю: …»). */
  liveStatus: string;
  turnId: number | string | null;
  /** Ход завершён, но сервер ещё не прислал итоговое состояние журнала. */
  turnEnded: boolean;
  /** Пошёл текст ответа: общий индикатор ожидания больше не нужен. */
  deltaSeen: boolean;
  /** Старые сообщения: кнопка «Предыдущие сообщения». */
  olderCursor: string | null;
  olderAvailable: boolean;
  olderBusy: boolean;
  plan: PlanSnapshot | null;
  changes: Change[];
  changeSelection: string | null;
  requests: AgentRequest[];
  queue: QueueSnapshot | null;
  coding: CodingSession | null;
  reasoningBusy: boolean;
  usage: UsageBundle | null;
  contextOperation: string;
  /** Выбранные варианты ответа Докки: ключ «чат:корень» → id сообщения. */
  variants: Record<string, string>;
  /** Вложения отправленных в этом сеансе сообщений: сервер локального режима не возвращает их в истории. */
  sent: Record<string, unknown[]>;
  /** Растёт при каждом открытии/сбросе ленты: компоненты сбрасывают своё состояние. */
  epoch: number;
}

const initial = (): ChatState => ({
  load: 'ready',
  loadError: '',
  messages: [],
  optimistic: null,
  live: [],
  alias: {},
  running: false,
  stopping: false,
  runId: null,
  status: '',
  liveStatus: '',
  turnId: null,
  turnEnded: false,
  deltaSeen: false,
  olderCursor: null,
  olderAvailable: false,
  olderBusy: false,
  plan: null,
  changes: [],
  changeSelection: null,
  requests: [],
  queue: null,
  coding: null,
  reasoningBusy: false,
  usage: null,
  contextOperation: '',
  variants: {},
  sent: {},
  epoch: 0,
});

export const chat = createStore<ChatState>(initial());
export const useChat = <S,>(selector: (state: ChatState) => S): S => useStore(chat, selector);

export const isDevelopment = (): boolean => ui.get().scope === 'development';
export const currentChatId = (): Id | null => view.get().chat?.id ?? null;

/** Ответ относится к уже закрытому экрану или другому чату. */
export const stale = (version: number, chatId?: Id | null): boolean =>
  version !== view.get().version || (chatId !== undefined && (view.get().chat?.id ?? null) !== chatId);

/* ленточный контроллер: прокрутка живёт в компоненте */
export interface FeedHandle {
  /** Прокрутка к концу, если пользователь не листал вверх. */
  follow(): void;
  /** Принудительно возвращает чтение к концу ленты. */
  pin(): void;
  /** Лента перестаёт следовать за новым текстом (пользователь читает выше). */
  release(): void;
  following(): boolean;
}
let feed: FeedHandle | null = null;
export const registerFeed = (handle: FeedHandle | null): void => { feed = handle; };
export const followFeed = (): void => feed?.follow();
export const pinFeed = (): void => feed?.pin();
export const releaseFeed = (): void => feed?.release();
export const isFollowing = (): boolean => feed?.following() ?? true;

/* связь с потоками и синхронизацией */
export interface Controllers {
  /** Активный поток событий хода. */
  stream: AbortController | null;
  /** Запросы текущего экрана; отменяются при переходе. */
  view: AbortController;
  syncTimer: number;
}

export const ctl: Controllers = { stream: null, view: new AbortController(), syncTimer: 0 };

export interface Hooks {
  sync(): Promise<void>;
  attach(chatId: Id, after?: string | number): Promise<void>;
  /** Остановить выполнение в разработке (подключается в chat.ts). */
  stop(): Promise<void>;
}
export const hooks: Hooks = {
  sync: async () => undefined,
  attach: async () => undefined,
  stop: async () => undefined,
};

export function scheduleSync(delay = 180): void {
  clearTimeout(ctl.syncTimer);
  ctl.syncTimer = window.setTimeout(() => {
    hooks.sync().catch(() => undefined);
  }, delay);
}

export function setStatus(text: string): void {
  chat.set({ status: text });
}

export function setRunning(running: boolean): void {
  chat.set({ running });
}

/** Сброс всего, что принадлежит открытому экрану. */
export function resetChat(): void {
  chat.set({ ...initial(), epoch: chat.get().epoch + 1 });
}

export const chatModel = (state: Chat | null): string => state?.model ?? '';
export const selectedLocalModel = (): string => {
  const { settings, models } = data.get();
  const saved = view.get().chat?.model || settings.selected_model;
  return models.some((model) => model.tag === saved) ? saved : (models[0]?.tag ?? '');
};

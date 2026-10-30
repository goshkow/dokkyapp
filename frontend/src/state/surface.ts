/**
 * Режим чата - страница сайта Докки во фрейме (правила сайта: dokky/docs/APP_SURFACE.md). Разделы, чаты и их вид
 * задаёт сайт, поэтому его изменения приходят без обновления приложения. Главный вид держит фрейм и его токен,
 * меню (и его плитка на macOS) рисует разделы и чаты по состоянию, которое сайт сообщает о себе.
 */
import { api, post } from '../lib/api';
import { kickGlass, setGlassSource, type GlassBlock, type GlassFade } from '../lib/glass';
import { exposeStore, isMirror, mirrorStore, remote } from '../lib/mirror';
import { createStore, useStore } from '../lib/store';
import { data, isReady } from './data';
import { openBilling } from './settingsUi';
import { ui } from './ui';
import type { Project } from '../lib/types';
import { newChat, view } from './view';

const PROTOCOL = 1;
/** Событие окна: Esc со страницы сайта, которому там нечего закрывать. */
export const SITE_ESCAPE = 'koddi:site-escape';
// сайт сам перезапрашивает токен при 401, а приложение меняет его заранее
const RENEW_BEFORE_MS = 120_000;
const HELLO_TIMEOUT_MS = 25_000;

export interface SurfaceSection { key: string; label: string; icon: string; active: boolean; badge: boolean }
export interface SurfaceAction { action: string; label: string; danger: boolean }
export interface SurfaceRemote { host_id: string; host_name: string; online: boolean; revoked: boolean }
export interface SurfaceChat {
  id: number;
  group: 'pinned' | 'recent';
  title: string;
  active: boolean;
  working: boolean;
  /** Агент ждёт ответа человека (вопрос или подтверждение), сайт помечает это классом `.chat-asking`. */
  asking: boolean;
  unread: boolean;
  draft: boolean;
  kinds: string[];
  icon: string;
  remote: SurfaceRemote | null;
  workspace: string;
  actions: SurfaceAction[];
}

/** Рамка блока страницы сайта в координатах фрейма; o - видимость с учётом предков. */
export interface SurfaceBox { x: number; y: number; w: number; h: number; r: number; o: number }
/** Поле ввода и «Чат / Поиск» страницы, которые на macOS со стеклом рисует приложение (state/siteField.ts). */
export interface SiteField {
  composer: SurfaceBox | null;
  product: SurfaceBox | null;
  choice: 'chat' | 'search';
  /** Идёт ответ: кнопка отправки останавливает его. */
  busy: boolean;
  /** Есть что отправить: текст или вложения страницы. */
  ready: boolean;
  /** Страница закрыла отправку: компьютер чата не в сети, идёт передача чата. */
  disabled: boolean;
  placeholder: string;
}

export interface SurfaceState {
  /** off - сервер не умеет или нет входа: работает свой чат приложения. */
  status: 'off' | 'loading' | 'ready' | 'failed';
  url: string;
  origin: string;
  /** Этот компьютер в удалённом доступе: его чаты у нас и так свои. */
  hostId: string;
  sections: SurfaceSection[];
  chats: SurfaceChat[];
  hasMore: boolean;
  multiChat: boolean;
  product: 'chat' | 'search';
  /** Растёт, когда фрейм нужно открыть заново: другой аккаунт, сбой, «Повторить». */
  epoch: number;
  /** Чат другого компьютера, открытый из режима кода: фрейм показывается поверх. */
  remoteChat: number | null;
  /** На странице сайта открыта новость: остальное окно затемняет приложение. */
  overlay: boolean;
  /** Что страница умеет сверх первой версии протокола (из hello). */
  features: string[];
  /** Поле ввода и «Чат / Поиск» рисует приложение: нативные, в стекле над лентой. */
  field: boolean;
  siteField: SiteField | null;
}

export const surface = createStore<SurfaceState>({
  status: 'off', url: '', origin: '', hostId: '', sections: [], chats: [], hasMore: false, multiChat: true,
  product: 'chat', epoch: 0, remoteChat: null, overlay: false, features: [], field: false, siteField: null,
});
export const useSurface = <S,>(selector: (state: SurfaceState) => S): S => useStore(surface, selector);
if (isMirror) mirrorStore('surface', surface);
else exposeStore('surface', surface);

/** Фрейм сайта виден: режим чата (или открыт чат другого компьютера), страница сайта поддерживается. */
export const surfaceShown = (state: SurfaceState = surface.get(), scope = ui.get().scope): boolean =>
  state.status !== 'off' && (scope !== 'development' || state.remoteChat !== null);
/** В режиме чата разделы, чаты и новый чат принадлежат сайту. */
export const surfaceOwnsChat = (scope = ui.get().scope): boolean => scope !== 'development' && surface.get().status !== 'off';

/* разбор состояния от сайта: всё, что пришло из фрейма, проверяется и обрезается */
const text = (value: unknown, max: number): string => (typeof value === 'string' ? value.slice(0, max) : '');
const flag = (value: unknown): boolean => value === true;
// иконки - только SVG в data: URL от самого сайта, они идут в mask-image
const icon = (value: unknown): string => {
  const raw = text(value, 40_000);
  return /^data:image\/svg\+xml;charset=utf-8,[A-Za-z0-9%._~!*'()-]+$/.test(raw) ? raw : '';
};
const record = (value: unknown): Record<string, unknown> => (value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {});
const list = (value: unknown, max: number): unknown[] => (Array.isArray(value) ? value.slice(0, max) : []);

function parseRemote(value: unknown): SurfaceRemote | null {
  const item = record(value);
  const host = text(item.host_id, 80);
  if (!host) return null;
  return { host_id: host, host_name: text(item.host_name, 120), online: flag(item.online), revoked: flag(item.revoked) };
}

export function parseState(value: unknown): Pick<SurfaceState, 'sections' | 'chats' | 'hasMore' | 'multiChat' | 'product'> {
  const raw = record(value);
  const sections = list(raw.sections, 24).map((entry) => {
    const item = record(entry);
    return { key: text(item.key, 40), label: text(item.label, 80), icon: icon(item.icon), active: flag(item.active), badge: flag(item.badge) };
  }).filter((item) => /^[a-z][a-z0-9-]*$/.test(item.key) && item.label);
  const chats = list(raw.chats, 500).map((entry) => {
    const item = record(entry);
    return {
      id: Number(item.id),
      group: item.group === 'pinned' ? 'pinned' as const : 'recent' as const,
      title: text(item.title, 300) || 'Новый чат',
      active: flag(item.active), working: flag(item.working), asking: flag(item.asking), unread: flag(item.unread), draft: flag(item.draft),
      kinds: list(item.kinds, 8).map((kind) => text(kind, 30)).filter((kind) => /^[a-z][a-z0-9-]*$/.test(kind)),
      icon: icon(item.icon),
      remote: parseRemote(item.remote),
      workspace: text(item.workspace, 120),
      actions: list(item.actions, 12).map((action) => {
        const fields = record(action);
        return { action: text(fields.action, 40), label: text(fields.label, 80), danger: flag(fields.danger) };
      }).filter((action) => /^[a-z][a-z0-9-]*$/.test(action.action) && action.label),
    };
  }).filter((item) => Number.isSafeInteger(item.id));
  return { sections, chats, hasMore: flag(raw.hasMore), multiChat: raw.multiChat !== false, product: raw.product === 'search' ? 'search' : 'chat' };
}

/* связь с фреймом: только главный вид */
let frame: HTMLIFrameElement | null = null;
let renewTimer = 0;
let helloTimer = 0;
let session = 0;
// до первого токена страница команды не принимает: им не за чем уходить раньше
let delivered = false;

function send(type: string, payload: Record<string, unknown> = {}): void {
  const target = frame?.contentWindow;
  const { origin } = surface.get();
  if (target && origin) target.postMessage({ dokkySurface: PROTOCOL, type, ...payload }, origin);
}

export function command(name: string, args: Record<string, unknown> = {}): void {
  if (delivered) send('command', { name, args });
}

/** Токен странице; после первого токена - тема, отступы и открытый чат: команды до токена страница не принимает. */
async function sendSession(first = false): Promise<void> {
  const ticket = ++session;
  window.clearTimeout(renewTimer);
  try {
    const value = await post<{ token: string; expires_in: number }>('/api/dokky/surface/session');
    if (ticket !== session) return;
    send('session', { token: value.token });
    delivered = true;
    if (first) {
      sendTheme();
      layoutProvider();
      const { remoteChat } = surface.get();
      if (remoteChat !== null) command('open-chat', { id: remoteChat });
    }
    renewTimer = window.setTimeout(() => void sendSession(), Math.max(60_000, value.expires_in * 1000 - RENEW_BEFORE_MS));
  } catch {
    if (ticket !== session || !isReady(data.get().account)) return;
    // сеть пропала: повторяем, пока фрейм жив; без токена страница сама покажет ошибку запроса
    renewTimer = window.setTimeout(() => void sendSession(first), 15_000);
  }
}

export function sendTheme(): void {
  command('theme', { theme: ui.get().theme });
}

export function sendLayout(insetLeft: number, insetRight: number, background: string, glass: boolean, field = false): void {
  command('layout', { insetLeft, insetRight, background, glass, field });
}

function parseBox(value: unknown): SurfaceBox | null {
  const box = record(value);
  const [x, y, w, h, r, o] = [box.x, box.y, box.w, box.h, box.r, box.o ?? 1].map(Number) as [number, number, number, number, number, number];
  if (![x, y, w, h, r, o].every(Number.isFinite) || w < 2 || h < 2 || w > 8000 || h > 8000 || Math.abs(x) > 8000 || Math.abs(y) > 8000) return null;
  return { x, y, w, h, r: Math.max(0, Math.min(r, w / 2, h / 2)), o: Math.max(0, Math.min(1, o)) };
}

/** Поле страницы сайта: рамки и состояние кнопки отправки; всё обрезается и проверяется. */
export function parseField(value: unknown): SiteField | null {
  if (!value || typeof value !== 'object') return null;
  const field = record(value);
  return {
    composer: parseBox(field.composer),
    product: parseBox(field.product),
    choice: field.choice === 'search' ? 'search' : 'chat',
    busy: flag(field.busy),
    ready: flag(field.ready),
    disabled: flag(field.disabled),
    placeholder: text(field.placeholder, 200),
  };
}

/* текст и фокус поля страницы принимает state/siteField.ts: так нет круга импортов через composer */
let fieldText: (value: string) => void = () => undefined;
let fieldFocus: () => void = () => undefined;
export function onSiteField(handlers: { text: (value: string) => void; focus: () => void }): void {
  fieldText = handlers.text;
  fieldFocus = handlers.focus;
}

/** Блоки страницы сайта под стеклом (поле ввода, «Чат / Поиск») в координатах фрейма. */
let frameGlass: GlassBlock[] = [];

const unit = (value: unknown): number | null => {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 && number <= 1 ? number : null;
};

/** Угасание слоя страницы, которое хост проигрывает сам: прозрачность 0..1, до 2 с, кривая из 4 чисел. */
function parseFade(value: unknown): GlassFade | null {
  if (!value || typeof value !== 'object') return null;
  const fade = record(value);
  const [from, to] = [unit(fade.from), unit(fade.to)];
  const [ms, at] = [Number(fade.ms), Number(fade.at)];
  const c = Array.isArray(fade.c) ? fade.c.map(Number) : [];
  if (from === null || to === null || !(ms > 0 && ms <= 2000) || !(Number.isFinite(at) && at > 0)) return null;
  if (c.length !== 4 || !c.every((part) => Number.isFinite(part) && part >= -2 && part <= 3)) return null;
  return { from, to, ms, c, at };
}

/** Только числа в разумных пределах: координаты приходят со страницы и уходят хосту окна. */
export function parseGlass(value: unknown): GlassBlock[] {
  if (!Array.isArray(value)) return [];
  const blocks: GlassBlock[] = [];
  for (const item of value.slice(0, 6)) {
    const block = record(item);
    const [x, y, w, h, r] = [block.x, block.y, block.w, block.h, block.r].map(Number) as [number, number, number, number, number];
    const id = typeof block.id === 'string' && /^[a-z]{1,16}$/.test(block.id) ? block.id : '';
    if (!id || ![x, y, w, h, r].every(Number.isFinite) || w < 2 || h < 2 || w > 8000 || h > 8000 || Math.abs(x) > 8000 || Math.abs(y) > 8000) continue;
    // прозрачность блока во время перехода страницы: без неё стекло исчезало бы рывком в конце анимации
    const o = Number(block.o ?? 1);
    const fade = parseFade(block.fade);
    blocks.push({ id: `site-${id}`, x, y, w, h, r: Math.max(0, Math.min(r, w / 2, h / 2)), o: Number.isFinite(o) ? Math.max(0, Math.min(1, o)) : 1, ...(fade && { fade }) });
  }
  return blocks;
}

function frameBlocks(): GlassBlock[] {
  if (!frame || !frameGlass.length || !frame.checkVisibility()) return [];
  const box = frame.getBoundingClientRect();
  return frameGlass.map((block) => ({ ...block, x: block.x + box.left, y: block.y + box.top }));
}

let layoutProvider: () => void = () => undefined;
/** Компонент фрейма знает размеры окна и цвет фона: он и отдаёт их странице. */
export const setLayoutProvider = (fn: () => void) => { layoutProvider = fn; };

function onMessage(event: MessageEvent): void {
  const { origin } = surface.get();
  if (!frame || event.source !== frame.contentWindow || !origin || event.origin !== origin) return;
  const message = record(event.data);
  if (message.dokkySurface !== PROTOCOL) return;
  switch (message.type) {
    case 'hello':
      // страница перезагрузилась сама (новый выпуск сайта): заново токен, тема и отступы
      delivered = false;
      surface.set({
        features: list(message.features, 8).map((item) => text(item, 32)).filter((item) => /^[a-z][a-z-]*$/.test(item)),
        siteField: null,
      });
      window.clearTimeout(helloTimer);
      void sendSession(true);
      return;
    case 'need-session':
      void sendSession();
      return;
    case 'ready':
      surface.set({ status: 'ready' });
      return;
    case 'state':
      surface.set(parseState(message.state));
      return;
    case 'billing':
      openBilling();
      return;
    case 'glass':
      frameGlass = parseGlass(message.regions);
      kickGlass();
      return;
    case 'overlay':
      surface.set({ overlay: message.open === true });
      return;
    case 'field':
      surface.set({ siteField: parseField(message.field) });
      // текст приходит, только когда его поменяла сама страница: отправка, другой чат, правка сообщения
      if (typeof message.text === 'string' && surface.get().field) fieldText(message.text.slice(0, 100_000));
      return;
    case 'field-focus':
      if (surface.get().field) fieldFocus();
      return;
    case 'key':
      // Esc, которому на странице сайта нечего закрывать: навигация с клавиатуры уводит в левое меню (state/keyNav.ts)
      if (message.key === 'Escape') window.dispatchEvent(new Event(SITE_ESCAPE));
      return;
    case 'window': {
      // окно тянется за верхнюю полосу страницы сайта так же, как за свою
      const action = message.action === 'maximize' ? 'maximize' : message.action === 'move' ? 'move' : '';
      if (action) window.koddiWindowBridge?.command(action);
      return;
    }
    default:
  }
}
if (!isMirror) {
  window.addEventListener('message', onMessage);
  setGlassSource(frameBlocks);
}

/** Компонент фрейма сообщает о своём элементе; новый элемент - новая загрузка страницы. */
export function bindFrame(element: HTMLIFrameElement | null): void {
  frame = element;
  frameGlass = [];
  if (surface.get().siteField) surface.set({ siteField: null });
  if (surface.get().overlay) surface.set({ overlay: false });
  kickGlass();
  delivered = false;
  window.clearTimeout(helloTimer);
  if (!element) {
    session += 1;
    window.clearTimeout(renewTimer);
    return;
  }
  helloTimer = window.setTimeout(() => {
    if (frame === element && surface.get().status === 'loading') surface.set({ status: 'failed' });
  }, HELLO_TIMEOUT_MS);
}

let checking = 0;
let checkedFor = '';
/** Есть ли на сервере страница для режима чата; вызывается после входа и при смене аккаунта. */
export async function checkSurface(force = false): Promise<void> {
  if (isMirror) return;
  const account = data.get().account;
  const owner = account?.authenticated ? String(account.user?.id ?? '') : '';
  if (!owner) {
    checkedFor = '';
    surface.set({ status: 'off', sections: [], chats: [], remoteChat: null });
    return;
  }
  if (!force && owner === checkedFor && surface.get().status !== 'failed') return;
  const ticket = ++checking;
  try {
    const value = await api<{ available: boolean; url?: string; origin?: string; host_id?: string }>('/api/dokky/surface');
    if (ticket !== checking) return;
    checkedFor = owner;
    if (!value.available || !value.url || !value.origin) {
      surface.set({ status: 'off' });
      return;
    }
    const url = new URL(value.url);
    url.searchParams.set('theme', ui.get().theme);
    surface.set((state) => ({
      // другой аккаунт - другие чаты: прежний список не показываем ни секунды
      status: 'loading', url: url.toString(), origin: value.origin!, hostId: value.host_id ?? '',
      sections: [], chats: [], epoch: state.epoch + 1,
    }));
  } catch {
    if (ticket !== checking) return;
    // сервер недоступен: остаётся свой чат приложения, проверим при следующем входе или «Повторить»
    if (surface.get().status === 'off') return;
    surface.set({ status: 'failed' });
  }
}

/** «Повторить» после сбоя загрузки страницы. */
export function reloadSurface(): void {
  void checkSurface(true);
}

/** «Новый чат» в режиме чата создаёт сайт, в режиме кода - приложение. */
export function startChat(project: Project | null = null): void {
  if (!project && surfaceOwnsChat()) command('new-chat');
  else newChat(project);
}

/* действия меню: в плитке macOS вызываются у главного вида */
function realOpenSurfaceChat(id: number): void {
  if (ui.get().scope === 'development') surface.set({ remoteChat: id });
  command('open-chat', { id });
}
function realSurfaceNewChat(): void { command('new-chat'); }
function realOpenSection(key: string): void { command('open-section', { key }); }
function realChatAction(id: number, action: string): void { command('chat-action', { id, action }); }
function realLoadMore(): void { command('load-more'); }
function realEnableMultiChat(): void { command('enable-multi-chat'); }
function realSetProduct(product: 'chat' | 'search'): void { command('product', { product }); }

export const openSurfaceChat = remote('openSurfaceChat', realOpenSurfaceChat);
export const surfaceNewChat = remote('surfaceNewChat', realSurfaceNewChat);
export const openSection = remote('openSection', realOpenSection);
export const surfaceChatAction = remote('surfaceChatAction', realChatAction);
export const surfaceLoadMore = remote('surfaceLoadMore', realLoadMore);
export const surfaceEnableMultiChat = remote('surfaceEnableMultiChat', realEnableMultiChat);
export const setSurfaceProduct = remote('setSurfaceProduct', realSetProduct);
export const surfaceActions = {
  openSurfaceChat: realOpenSurfaceChat,
  surfaceNewChat: realSurfaceNewChat,
  openSection: realOpenSection,
  surfaceChatAction: realChatAction,
  surfaceLoadMore: realLoadMore,
  surfaceEnableMultiChat: realEnableMultiChat,
  setSurfaceProduct: realSetProduct,
};

/** Чаты сайта для меню: свои удалённые чаты этого компьютера не показываются нигде, чужие - только в режиме кода. */
export const chatModeChats = (chats: SurfaceChat[]): SurfaceChat[] => chats.filter((chat) => !chat.remote);
export const otherComputerChats = (chats: SurfaceChat[], hostId: string): SurfaceChat[] =>
  chats.filter((chat) => chat.remote && chat.remote.host_id !== hostId);

if (!isMirror) {
  // переход в приложении (свой чат, страница, другой режим) закрывает чат другого компьютера
  let version = view.get().version;
  view.subscribe(() => {
    const next = view.get().version;
    if (next !== version && surface.get().remoteChat !== null) surface.set({ remoteChat: null });
    version = next;
  });
  let theme = ui.get().theme;
  let scope = ui.get().scope;
  ui.subscribe(() => {
    const state = ui.get();
    if (state.theme !== theme) { theme = state.theme; sendTheme(); }
    if (state.scope !== scope) {
      scope = state.scope;
      if (scope !== 'development' && surface.get().remoteChat !== null) surface.set({ remoteChat: null });
    }
  });
  // вход, выход и смена аккаунта
  let owner = '';
  data.subscribe(() => {
    const account = data.get().account;
    const next = account?.authenticated ? String(account.user?.id ?? '') : '';
    if (next === owner) return;
    owner = next;
    void checkSurface(true);
  });
}

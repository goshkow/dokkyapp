// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { settingsUi } from './settingsUi';
import { connection } from './connection';
import { data } from './data';
import { bindFrame, checkSurface, chatModeChats, command, otherComputerChats, parseField, parseGlass, parseState, parseTabs, startChat, surface } from './surface';
import { ui } from './ui';
import { view } from './view';

const ORIGIN = 'https://dokky.goshkow.com';
const svg = `data:image/svg+xml;charset=utf-8,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg"/>')}`;

const chat = (id: number, extra: Record<string, unknown> = {}) => ({
  id, group: 'recent', title: `Чат ${id}`, active: false, working: false, asking: false, unread: false, draft: false,
  kinds: [], icon: '', remote: null, workspace: '', actions: [], ...extra,
});

describe('состояние страницы сайта', () => {
  it('берёт только ожидаемые поля и значения', () => {
    const state = parseState({
      sections: [
        { key: 'cloud', label: 'Облако', icon: svg, active: true, badge: 1 },
        { key: 'Bad Key', label: 'x' },
        { key: 'food', label: '' },
        { key: 'scheduled', label: 'Запланированное', icon: 'https://evil.example/x.svg' },
        { key: 'library', label: 'Библиотека', icon: 'data:image/svg+xml;charset=utf-8,");background:url(https://evil' },
      ],
      chats: [
        chat(1, { kinds: ['cloud', 'BAD', 'x y'], icon: svg, workspace: 'Сайт', actions: [{ action: 'rename', label: 'Переименовать' }, { action: 'drop table', label: 'x' }] }),
        chat(2, { remote: { host_id: 'h1', host_name: 'Ноутбук', online: 'yes' } }),
        { id: 'nope', title: 'x' },
        { id: 2 ** 60, title: 'x' },
      ],
      hasMore: 'true',
      multiChat: false,
      product: 'admin',
    });
    expect(state.sections.map((item) => item.key)).toEqual(['cloud', 'scheduled', 'library']);
    expect(state.sections[0]).toEqual({ key: 'cloud', label: 'Облако', icon: svg, active: true, badge: false });
    // чужие адреса и попытка выйти из url("...") не становятся иконкой
    expect(state.sections[1]?.icon).toBe('');
    expect(state.sections[2]?.icon).toBe('');
    expect(state.chats.map((item) => item.id)).toEqual([1, 2]);
    expect(state.chats[0]?.kinds).toEqual(['cloud']);
    expect(state.chats[0]?.actions).toEqual([{ action: 'rename', label: 'Переименовать', danger: false }]);
    expect(state.chats[1]?.remote).toEqual({ host_id: 'h1', host_name: 'Ноутбук', online: false, revoked: false });
    expect(state).toMatchObject({ hasMore: false, multiChat: false, product: 'chat' });
  });

  it('мусор вместо состояния даёт пустой список, а не ошибку', () => {
    for (const value of [null, 'x', 1, [], { sections: 'x', chats: { a: 1 } }]) {
      expect(parseState(value)).toEqual({ sections: [], chats: [], hasMore: false, multiChat: true, product: 'chat' });
    }
  });

  it('удалённые чаты этого компьютера скрыты везде, других компьютеров - только в режиме кода', () => {
    const { chats } = parseState({
      chats: [chat(1), chat(2, { remote: { host_id: 'me' } }), chat(3, { remote: { host_id: 'other', host_name: 'ПК' } })],
    });
    expect(chatModeChats(chats).map((item) => item.id)).toEqual([1]);
    expect(otherComputerChats(chats, 'me').map((item) => item.id)).toEqual([3]);
    // прежняя запись этого же компьютера после переустановки: то же имя, не в сети
    const stale = (id: number, online: boolean) => ({ ...chats[0]!, id, remote: { host_id: `old${id}`, host_name: 'Мой', online, revoked: false } });
    expect(otherComputerChats([stale(7, false), stale(8, true)], 'me', 'Мой').map((item) => item.id)).toEqual([8]);
  });
});

describe('стекло под блоками страницы сайта', () => {
  it('берёт только числа в пределах и свои имена блоков', () => {
    const blocks = parseGlass([
      { id: 'composer', x: 10, y: 700, w: 600, h: 56, r: 28 },
      { id: 'product', x: '400', y: 13, w: 244, h: 42, r: 999, o: 0.4 },
      { id: 'search', x: 0, y: 0, w: 10, h: 10, r: 0, o: 7 },
      { id: 'fade', x: 0, y: 0, w: 10, h: 10, r: 0, o: 'x' },
      { id: 'Bad Id', x: 0, y: 0, w: 10, h: 10, r: 0 },
      { id: 'tiny', x: 0, y: 0, w: 1, h: 10, r: 0 },
      { id: 'huge', x: 0, y: 0, w: 1e9, h: 10, r: 0 },
      { id: 'nan', x: 'x', y: 0, w: 10, h: 10, r: 0 },
      ...Array.from({ length: 10 }, () => ({ id: 'extra', x: 0, y: 0, w: 10, h: 10, r: 0 })),
    ]);
    expect(blocks[0]).toEqual({ id: 'site-composer', x: 10, y: 700, w: 600, h: 56, r: 28, o: 1 });
    // радиус не больше половины блока
    expect(blocks[1]).toEqual({ id: 'site-product', x: 400, y: 13, w: 244, h: 42, r: 21, o: 0.4 });
    // прозрачность только 0..1, мусор - полностью видимый блок
    expect(blocks[2]?.o).toBe(1);
    expect(blocks[3]?.o).toBe(1);
    expect(blocks.map((block) => block.id)).toEqual(['site-composer', 'site-product', 'site-search', 'site-fade']);
    // угасание слоя: только целое и в пределах, иначе блок идёт без него
    const fade = { from: 1, to: 0, ms: 220, c: [0.22, 1, 0.36, 1], at: 1_700_000_000_000 };
    const box = { id: 'composer', x: 0, y: 0, w: 10, h: 10, r: 0 };
    expect(parseGlass([{ ...box, fade }])[0]?.fade).toEqual(fade);
    for (const broken of [{ ...fade, to: 2 }, { ...fade, ms: 0 }, { ...fade, ms: 9000 }, { ...fade, c: [0, 0, 1] }, { ...fade, c: [0, 9, 1, 1] }, { ...fade, at: 'x' }, 'x']) {
      expect(parseGlass([{ ...box, fade: broken }])[0]?.fade).toBeUndefined();
    }
    expect(parseGlass('x')).toEqual([]);
    expect(parseGlass(Array.from({ length: 10 }, () => ({ id: 'a', x: 0, y: 0, w: 10, h: 10, r: 0 })))).toHaveLength(6);
  });
});

describe('поле страницы под стеклом приложения', () => {
  it('берёт только флаги и ограниченные строки, мусор - не поле', () => {
    const field = parseField({
      composer: { x: 10, y: 600, w: 500, h: 56, r: 28, o: 1 }, product: 'x', choice: 'drop',
      busy: 'yes', ready: true, disabled: true, placeholder: 'п'.repeat(500),
    });
    expect(field).toMatchObject({ product: null, choice: 'chat', busy: false, ready: true, disabled: true });
    expect(field?.composer).not.toBeNull();
    expect(field?.placeholder.length).toBe(200);
    expect(parseField({ choice: 'search' })).toMatchObject({ choice: 'search', disabled: false, ready: false });
    expect(parseField(null)).toBeNull();
    expect(parseField('field')).toBeNull();
  });
});

describe('связь с фреймом', () => {
  let frame: HTMLIFrameElement;
  let posted: unknown[];

  const deliver = (data: unknown, origin = ORIGIN, source: MessageEventSource | null = frame.contentWindow) =>
    window.dispatchEvent(new MessageEvent('message', { data, origin, source }));

  beforeEach(async () => {
    frame = document.createElement('iframe');
    document.body.append(frame);
    posted = [];
    vi.spyOn(frame.contentWindow!, 'postMessage').mockImplementation((message: unknown) => { posted.push(message); });
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ token: 'dks1.t', expires_in: 900 }), { headers: { 'content-type': 'application/json' } })));
    surface.set({ status: 'loading', url: `${ORIGIN}/app-surface`, origin: ORIGIN, chats: [], sections: [], remoteChat: null });
    bindFrame(frame);
    // команды до токена не уходят: сначала рукопожатие
    deliver({ dokkySurface: 1, type: 'hello' });
    await vi.waitFor(() => expect(posted).toContainEqual({ dokkySurface: 1, type: 'session', token: 'dks1.t' }));
    expect(posted[1]).toMatchObject({ type: 'command', name: 'theme' });
    posted.length = 0;
  });

  afterEach(() => {
    bindFrame(null);
    frame.remove();
    surface.set({ status: 'off' });
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('до токена команды не уходят', () => {
    bindFrame(frame);
    ui.set({ scope: 'chat' });
    startChat();
    expect(posted).toEqual([]);
  });

  it('принимает состояние только от своего фрейма с адреса сайта', () => {
    const state = { dokkySurface: 1, type: 'state', state: { chats: [chat(5)] } };
    deliver(state, 'https://evil.example');
    deliver(state, ORIGIN, window);
    deliver({ ...state, dokkySurface: 2 });
    expect(surface.get().chats).toEqual([]);
    deliver(state);
    expect(surface.get().chats.map((item) => item.id)).toEqual([5]);
    deliver({ dokkySurface: 1, type: 'ready' });
    expect(surface.get().status).toBe('ready');
  });

  it('новость на сайте затемняет окно, пока открыта; чужие сообщения и новый фрейм её снимают', () => {
    deliver({ dokkySurface: 1, type: 'overlay', open: true }, 'https://evil.example');
    expect(surface.get().overlay).toBe(false);
    deliver({ dokkySurface: 1, type: 'overlay', open: 'yes' });
    expect(surface.get().overlay).toBe(false);
    deliver({ dokkySurface: 1, type: 'overlay', open: true });
    expect(surface.get().overlay).toBe(true);
    command('close-overlay');
    expect(posted).toContainEqual({ dokkySurface: 1, type: 'command', name: 'close-overlay', args: {} });
    deliver({ dokkySurface: 1, type: 'overlay', open: false });
    expect(surface.get().overlay).toBe(false);
    deliver({ dokkySurface: 1, type: 'overlay', open: true });
    bindFrame(frame);
    expect(surface.get().overlay).toBe(false);
  });

  it('оплата со страницы открывает оплату приложения', () => {
    settingsUi.set({ open: false });
    deliver({ dokkySurface: 1, type: 'billing', intent: 'plans' });
    expect(settingsUi.get()).toMatchObject({ open: true, section: 'billing' });
  });

  it('новый чат в режиме чата создаёт сайт, в режиме кода - приложение', () => {
    ui.set({ scope: 'chat' });
    const before = view.get().version;
    startChat();
    expect(posted).toEqual([{ dokkySurface: 1, type: 'command', name: 'new-chat', args: {} }]);
    expect(view.get().version).toBe(before);
    ui.set({ scope: 'development' });
    startChat();
    expect(posted).toHaveLength(1);
    expect(view.get().version).toBe(before + 1);
  });

  it('сообщения уходят только на адрес сайта', () => {
    ui.set({ scope: 'chat' });
    const target = vi.spyOn(frame.contentWindow!, 'postMessage');
    startChat();
    expect(target).toHaveBeenCalledWith(expect.anything(), ORIGIN);
  });
});

describe('страница сайта без связи', () => {
  beforeEach(() => {
    data.set({ account: { authenticated: true, user: { id: 'offline' } } as never });
    surface.set({ status: 'off' });
  });
  afterEach(() => {
    connection.set({ online: true });
    vi.unstubAllGlobals();
  });

  it('нет сети: сразу экран ошибки, а когда связь вернулась, страница открывается сама', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ available: true, url: `${ORIGIN}/app-surface`, origin: ORIGIN }), { status: 200 }));
    vi.stubGlobal('fetch', fetcher);
    connection.set({ online: false });
    await checkSurface(true);
    expect(surface.get().status).toBe('failed');
    expect(fetcher).not.toHaveBeenCalled();
    connection.set({ online: true });
    await vi.waitFor(() => expect(surface.get().status).toBe('loading'));
  });

  it('сервер не ответил при первом запуске: тоже экран ошибки, а не пустой чат', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'нет' }), { status: 503 })));
    await checkSurface(true);
    expect(surface.get().status).toBe('failed');
  });
});

describe('режимы нового экрана от сайта', () => {
  const tab = { id: 'task:deck', label: 'Презентация', text: 'Презентация', x: 10, y: 5, w: 120, h: 34, o: 1, font: 14, gap: 7, selected: true, open: false, icon: '', iconW: 16, iconO: 1 };

  it('разбирает вкладки и индикатор', () => {
    const parsed = parseTabs({ items: [tab], indicator: { x: 4, y: 4, w: 80, h: 36 } });
    expect(parsed?.items[0]).toMatchObject({ id: 'task:deck', w: 120, selected: true });
    expect(parsed?.indicator).toEqual({ x: 4, y: 4, w: 80, h: 36 });
  });

  it('чужие вкладки и иконки не проходят', () => {
    expect(parseTabs({ items: [{ ...tab, id: 'javascript:alert(1)' }] })).toBeNull();
    expect(parseTabs(null)).toBeNull();
    const bad = parseTabs({ items: [{ ...tab, icon: 'https://evil.test/x.svg', x: 'x', w: 1e9 }] });
    expect(bad?.items[0]).toMatchObject({ icon: '', x: 0, w: 2000 });
  });

  it('поле старого сайта без режимов остаётся рабочим', () => {
    const field = parseField({ composer: null, product: null, choice: 'chat' });
    expect(field).toMatchObject({ tabs: null, flash: 0 });
  });
});

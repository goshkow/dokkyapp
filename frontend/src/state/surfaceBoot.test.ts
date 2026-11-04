// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cacheGet, cacheSet } from '../lib/cache';
import { connection } from './connection';
import { data } from './data';
import { closeSurfaceOverlay, surface, surfaceActions, bindFrame } from './surface';
import { ui } from './ui';

vi.mock('../lib/cache', async (original) => ({ ...(await original<typeof import('../lib/cache')>()), cacheGet: vi.fn(), cacheSet: vi.fn() }));

const ORIGIN = 'https://dokky.goshkow.com';
const URL_ = `${ORIGIN}/app-surface`;
const chat = (id: number, extra: Record<string, unknown> = {}) => ({
  id, group: 'recent', title: `Чат ${id}`, active: false, working: false, asking: false, unread: false, draft: false,
  kinds: [], icon: '', remote: null, workspace: '', actions: [], ...extra,
});
const remembered = { url: URL_, origin: ORIGIN, hostId: 'h', hostName: 'Мак', sections: [{ key: 'cloud', label: 'Облако', icon: '', active: false, badge: false }], chats: [chat(1), chat(2)], hasMore: false, multiChat: true };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });

let answer: (response: Response) => void;
let fail: (error: Error) => void;
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

function serve(): void {
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    if (String(input).includes('/api/dokky/surface/session')) return Promise.resolve(json({ token: 't', expires_in: 900 }));
    return new Promise<Response>((resolve, reject) => { answer = resolve; fail = reject; });
  }));
}
const signIn = () => data.set({ account: { authenticated: true, user: { id: 'u1' } } as never });

describe('запуск режима чата по кэшу', () => {
  beforeEach(() => {
    vi.mocked(cacheGet).mockReset();
    vi.mocked(cacheSet).mockReset();
    connection.set({ online: true });
    ui.set({ scope: 'chat', theme: 'dark' });
    data.set({ account: { authenticated: false } as never });
    surface.set({ status: 'off', checking: false, url: '', origin: '', sections: [], chats: [], overlay: false });
    serve();
  });
  afterEach(() => { data.set({ account: { authenticated: false } as never }); vi.unstubAllGlobals(); });

  it('меню и фрейм открываются сразу, пока сервер отвечает; тот же адрес страницу не перезагружает', async () => {
    vi.mocked(cacheGet).mockResolvedValue(remembered);
    signIn();
    expect(surface.get().checking).toBe(true);
    await settle();
    const started = surface.get();
    expect(started.status).toBe('loading');
    expect(started.chats.map((item) => item.id)).toEqual([1, 2]);
    expect(started.url).toContain('home=1');
    answer(json({ available: true, url: URL_, origin: ORIGIN, host_id: 'h2', host_name: 'Мак' }));
    await settle();
    expect(surface.get().checking).toBe(false);
    expect(surface.get().epoch).toBe(started.epoch);
    expect(surface.get().hostId).toBe('h2');
    expect(surface.get().chats).toHaveLength(2);
  });

  it('сервер сказал, что страницы больше нет: свой чат и пустой кэш', async () => {
    vi.mocked(cacheGet).mockResolvedValue(remembered);
    signIn();
    await settle();
    answer(json({ available: false }));
    await settle();
    expect(surface.get().status).toBe('off');
    expect(surface.get().chats).toEqual([]);
    expect(surface.get().checking).toBe(false);
    expect(cacheSet).toHaveBeenCalledWith('surface', null);
  });

  it('сервер не ответил при живом кэше: ошибку решает таймер страницы, а не проверка', async () => {
    vi.mocked(cacheGet).mockResolvedValue(remembered);
    signIn();
    await settle();
    fail(new TypeError('Failed to fetch'));
    await settle();
    expect(surface.get().status).toBe('loading');
    expect(surface.get().checking).toBe(false);
  });

  it('без кэша ждёт сервер, а при его ошибке показывает сбой', async () => {
    vi.mocked(cacheGet).mockResolvedValue(undefined);
    signIn();
    await settle();
    expect(surface.get().status).toBe('off');
    expect(surface.get().checking).toBe(true);
    fail(new TypeError('Failed to fetch'));
    await settle();
    expect(surface.get().status).toBe('failed');
    expect(surface.get().checking).toBe(false);
  });

  it('кэш с адресом чужого сайта фрейм не открывает', async () => {
    vi.mocked(cacheGet).mockResolvedValue({ ...remembered, url: 'https://evil.example/app-surface' });
    signIn();
    await settle();
    expect(surface.get().status).toBe('off');
    expect(surface.get().url).toBe('');
  });

  it('выход во время проверки не оставляет заставку и чужой список', async () => {
    vi.mocked(cacheGet).mockResolvedValue(remembered);
    signIn();
    await settle();
    data.set({ account: { authenticated: false } as never });
    answer(json({ available: true, url: URL_, origin: ORIGIN }));
    await settle();
    expect(surface.get().status).toBe('off');
    expect(surface.get().checking).toBe(false);
    expect(surface.get().chats).toEqual([]);
  });

  it('после выхода фрейма прежнего аккаунта больше нет', async () => {
    vi.mocked(cacheGet).mockResolvedValue(remembered);
    signIn();
    await settle();
    expect(surface.get().url).not.toBe('');
    data.set({ account: { authenticated: false } as never });
    await settle();
    expect(surface.get().url).toBe('');
    expect(surface.get().origin).toBe('');
    expect(surface.get().hostId).toBe('');
  });

  it('другой аккаунт сразу теряет страницу и чаты прежнего, не дожидаясь сервера', async () => {
    vi.mocked(cacheGet).mockResolvedValue(remembered);
    signIn();
    await settle();
    answer(json({ available: true, url: URL_, origin: ORIGIN }));
    await settle();
    expect(surface.get().chats).toHaveLength(2);
    vi.mocked(cacheGet).mockResolvedValue(undefined);
    data.set({ account: { authenticated: true, user: { id: 'other' } } as never });
    expect(surface.get().chats).toEqual([]);
    expect(surface.get().url).toBe('');
  });

  it('кэш с адресом без https фрейм не открывает', async () => {
    vi.mocked(cacheGet).mockResolvedValue({ ...remembered, url: 'http://dokky.example/app-surface', origin: 'http://dokky.example' });
    signIn();
    await settle();
    expect(surface.get().url).toBe('');
  });

  it('пустой список страницы не затирает сохранённый', async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(cacheGet).mockResolvedValue(undefined);
      signIn();
      await vi.advanceTimersByTimeAsync(0);
      answer(json({ available: true, url: URL_, origin: ORIGIN }));
      await vi.advanceTimersByTimeAsync(2000);
      expect(surface.get().url).not.toBe('');
      expect(vi.mocked(cacheSet)).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('нажатия в режиме чата', () => {
  let posted: Array<Record<string, unknown>>;
  beforeEach(() => {
    posted = [];
    vi.stubGlobal('fetch', vi.fn(async () => json({ token: 't', expires_in: 900 })));
    const element = document.createElement('iframe');
    Object.defineProperty(element, 'contentWindow', { value: { postMessage: (message: Record<string, unknown>) => posted.push(message) } });
    surface.set({ status: 'ready', origin: ORIGIN, chats: [chat(1, { active: true }), chat(2)] as never, sections: [], overlay: false });
    bindFrame(element);
    window.dispatchEvent(new MessageEvent('message', { origin: ORIGIN, source: element.contentWindow as never, data: { dokkySurface: 1, type: 'hello' } }));
    ui.set({ scope: 'chat' });
  });
  afterEach(() => { bindFrame(null); vi.unstubAllGlobals(); vi.useRealTimers(); });

  it('открытый чат заново не открывается, другой открывается', async () => {
    await settle();
    posted.length = 0;
    surfaceActions.openSurfaceChat(1);
    expect(posted).toEqual([]);
    surfaceActions.openSurfaceChat(2);
    expect(posted[0]).toMatchObject({ name: 'open-chat', args: { id: 2 } });
  });

  it('из поиска открытый чат всё же возвращается', async () => {
    await settle();
    posted.length = 0;
    surface.set({ product: 'search' });
    surfaceActions.openSurfaceChat(1);
    surface.set({ product: 'chat' });
    expect(posted[0]).toMatchObject({ name: 'open-chat', args: { id: 1 } });
  });

  it('из раздела сайта открытый чат всё же возвращается', async () => {
    await settle();
    posted.length = 0;
    surface.set({ sections: [{ key: 'cloud', label: 'Облако', icon: '', active: true, badge: false }] });
    surfaceActions.openSurfaceChat(1);
    expect(posted[0]).toMatchObject({ name: 'open-chat', args: { id: 1 } });
  });

  it('новая страница снимает затемнение прежней, иначе меню осталось бы тёмным', async () => {
    surface.set({ overlay: true });
    bindFrame(null);
    const element = document.createElement('iframe');
    const target = { postMessage: () => undefined };
    Object.defineProperty(element, 'contentWindow', { value: target });
    bindFrame(element);
    surface.set({ overlay: true });
    window.dispatchEvent(new MessageEvent('message', { origin: ORIGIN, source: target as never, data: { dokkySurface: 1, type: 'hello' } }));
    expect(surface.get().overlay).toBe(false);
  });

  it('страница не ответила на закрытие новости: окно не остаётся закрытым', () => {
    vi.useFakeTimers();
    surface.set({ overlay: true });
    closeSurfaceOverlay();
    expect(surface.get().overlay).toBe(true);
    vi.advanceTimersByTime(500);
    expect(surface.get().overlay).toBe(false);
  });
});

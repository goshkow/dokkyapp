import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { bootApp, data, revoke } from './data';
import { toasts } from './overlay';

type Route = (url: string, init?: RequestInit) => Response | Promise<Response>;
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const session = { authenticated: true, user: { id: 'u1' } };

function serve(route: Route): void {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => route(String(input), init)));
}

const local: Route = (url) => {
  if (url.includes('/api/dokky/session')) return json(session);
  if (url.includes('/api/projects') || url.includes('/api/chats') || url.includes('/api/modes') || url.includes('/api/models')) return json([]);
  if (url.includes('/api/settings')) return json({});
  return json({ detail: { code: 'upstream_unavailable', message: 'Не удалось связаться с Dokky' } }, 503);
};

describe('запуск приложения', () => {
  beforeEach(() => {
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    data.set({ booted: false, bootFailed: '', account: null });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('без сети открывается с локальными чатами и говорит, в чём дело', async () => {
    serve(local);
    await bootApp();
    expect(data.get().booted).toBe(true);
    expect(data.get().bootFailed).toBe('');
    expect(data.get().account?.authenticated).toBe(true);
    expect(toasts.get().items.some((item) => item.text.includes('связаться'))).toBe(true);
  });

  it('отказ в сессии ведёт на вход, а не на вечный логотип', async () => {
    serve((url, init) => (url.includes('/bootstrap') ? json({ detail: { code: 'invalid_grant', message: 'Войдите снова' } }, 401) : local(url, init)));
    await bootApp();
    expect(data.get().booted).toBe(true);
    expect(data.get().account?.authenticated).toBe(false);
    expect(data.get().bootFailed).toBe('');
  });

  it('локальный сервер не ответил: на экране причина, повтор загружает снова', async () => {
    serve(() => { throw new TypeError('Failed to fetch'); });
    await bootApp();
    expect(data.get().booted).toBe(true);
    expect(data.get().bootFailed).toContain('Докки не отвечает');
    serve(local);
    await bootApp();
    expect(data.get().bootFailed).toBe('');
    expect(data.get().account?.authenticated).toBe(true);
  });

  it('вход есть, а локальные данные не загрузились: экран с повтором, а не пустое окно', async () => {
    serve((url, init) => (url.includes('/api/settings') ? json({ detail: 'Сбой базы' }, 500) : local(url, init)));
    await bootApp();
    expect(data.get().bootFailed).toBe('Сбой базы');
  });

  it('сохранённый ответ bootstrap: запуск не ждёт сервер, модели обновляются, когда он ответит', async () => {
    let answer: (response: Response) => void = () => undefined;
    const models: string[] = [];
    serve((url, init) => {
      if (url.includes('/api/dokky/bootstrap')) return new Promise((resolve) => { answer = resolve; });
      if (url.includes('/api/dokky/session')) return json({ ...session, bootstrap: { models: [{ id: 'x' }] } });
      if (url.includes('/api/models/options')) { models.push(url); return json([{ id: 'fresh', name: 'Свежая', available: true }]); }
      return local(url, init);
    });
    await bootApp();
    expect(data.get().booted).toBe(true);
    expect(models).toHaveLength(1);
    answer(json({}));
    await vi.waitFor(() => expect(models).toHaveLength(2));
    await vi.waitFor(() => expect((data.get().models as unknown as Array<{ id: string }>).map((model) => model.id)).toEqual(['fresh']));
  });

  it('первый запуск без сохранённого ответа ждёт bootstrap', async () => {
    let asked = false;
    serve((url, init) => {
      if (url.includes('/api/dokky/bootstrap')) { asked = true; return json({}); }
      return local(url, init);
    });
    await bootApp();
    expect(asked).toBe(true);
    expect(data.get().booted).toBe(true);
  });

  it('сессия закончилась: на экране входа есть объяснение, а обычный выход без него', async () => {
    serve(local);
    await bootApp();
    revoke();
    expect(data.get().authNotice).toContain('Сессия закончилась');
    serve(local);
    await bootApp();
    revoke('');
    expect(data.get().authNotice).toBe('');
  });
});

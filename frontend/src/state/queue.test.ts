import { beforeEach, describe, expect, it, vi } from 'vitest';

const env = vi.hoisted(() => ({
  draft: { text: 'Задача', attachments: [{ id: 1 }], skills: [{ id: 10 }], agentMode: 'development' },
  screen: { version: 1, chat: { id: 1 } },
  state: { queue: null as unknown, running: false },
  post: vi.fn(), api: vi.fn(),
}));
vi.mock('../lib/api', () => ({ api: env.api, post: env.post }));
vi.mock('./chatCore', () => ({
  chat: { get: () => env.state, set: (change: object) => Object.assign(env.state, change) },
  currentChatId: () => env.screen.chat.id, stale: (version: number, id: number) => env.screen.version !== version || env.screen.chat.id !== id,
  ctl: { view: { signal: new AbortController().signal } }, hooks: {}, isDevelopment: () => true, selectedLocalModel: () => 'dokky-code',
}));
vi.mock('./access', () => ({ requireDeveloper: () => true }));
vi.mock('./composer', () => ({
  draftContent: () => env.draft.text.trim() || (env.draft.attachments.length ? 'Вложения' : ''),
  attachmentsReady: () => true,
  composer: { get: () => env.draft, set: (change: object) => { env.draft = { ...env.draft, ...change }; } },
}));
vi.mock('./data', () => ({ data: { get: () => ({ settings: {} }), set: vi.fn() } }));
vi.mock('./overlay', () => ({ inputDialog: vi.fn() }));
vi.mock('./view', () => ({ view: { get: () => env.screen } }));
import { enqueueDraft, resetQueue } from './queue';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
beforeEach(() => {
  resetQueue(); vi.clearAllMocks();
  env.draft = { text: 'Задача', attachments: [{ id: 1 }], skills: [{ id: 10 }], agentMode: 'development' };
  env.screen = { version: 1, chat: { id: 1 } };
  env.api.mockResolvedValue({ items: [], paused: false });
});

describe('однократная отправка очереди', () => {
  it('добавляет один запрос и сохраняет введённое и новые вложения за время POST', async () => {
    const request = deferred<unknown>(); env.post.mockReturnValue(request.promise);
    const first = enqueueDraft();
    expect(await enqueueDraft()).toBeNull();
    env.draft = { ...env.draft, text: 'Следующая задача', attachments: [{ id: 1 }, { id: 2 }], skills: [{ id: 10 }, { id: 20 }] };
    request.resolve({ items: [], paused: false }); await first;
    expect(env.post).toHaveBeenCalledTimes(1);
    expect(env.draft.text).toBe('Следующая задача');
    expect(env.draft.attachments).toEqual([{ id: 2 }]);
    expect(env.draft.skills).toEqual([{ id: 20 }]);
  });
  it('после сетевого отказа повторяет тот же ключ без дублирования', async () => {
    env.post.mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ items: [], paused: false });
    await expect(enqueueDraft()).rejects.toThrow('offline');
    await enqueueDraft();
    expect(env.post.mock.calls[0]?.[1].request_id).toBe(env.post.mock.calls[1]?.[1].request_id);
  });
  it('старый запрос не блокирует другой чат и не снимает его блокировку', async () => {
    const old = deferred<unknown>(), next = deferred<unknown>();
    env.post.mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise);
    const first = enqueueDraft();
    env.screen = { version: 2, chat: { id: 2 } }; resetQueue();
    const second = enqueueDraft();
    old.resolve({ items: [], paused: false }); await first;
    expect(await enqueueDraft()).toBeNull();
    expect(env.post).toHaveBeenCalledTimes(2);
    next.resolve({ items: [], paused: false }); await second;
    expect(env.draft.text).toBe('');
  });
});

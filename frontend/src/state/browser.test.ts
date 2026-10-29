import { afterEach, describe, expect, it, vi } from 'vitest';
import { browser, sendBrowserInput } from './browser';

describe('ввод в браузер панели', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    browser.set({ sessions: [], active: null, frame: null, error: '' });
  });

  it('отправляет нажатие и отпускание по очереди, а лишнее движение пропускает', async () => {
    const order: string[] = [];
    let release: () => void = () => undefined;
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      const kind = JSON.parse(String(init.body)).kind as string;
      order.push(`start:${kind}`);
      if (kind === 'down') await new Promise<void>((resolve) => { release = resolve; });
      order.push(`end:${kind}`);
      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    browser.set({ active: 3 });
    const down = sendBrowserInput({ kind: 'down', x: 0.5, y: 0.5 });
    const move = sendBrowserInput({ kind: 'move', x: 0.6, y: 0.6 });
    const up = sendBrowserInput({ kind: 'up', x: 0.5, y: 0.5 });
    await vi.waitFor(() => expect(order).toContain('start:down'));
    expect(order).not.toContain('start:up');
    release();
    await Promise.all([down, move, up]);
    expect(order).toEqual(['start:down', 'end:down', 'start:up', 'end:up']);
    expect(fetchMock.mock.calls.every(([url]) => url === '/api/browser/3/input')).toBe(true);
  });
});

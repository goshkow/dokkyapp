import { describe, expect, it } from 'vitest';
import { createStore } from './store';
import { readSse } from './sse';

function response(chunks: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  }));
}

describe('readSse', () => {
  it('склеивает события, разорванные между порциями, и пропускает мусор', async () => {
    const body = response(['id: 1\nevent: message.delta\ndata: {"a":', '1}\n\nevent: broken\ndata: {не json}\n\nid: 2\nevent: turn.completed\ndata: {}\n\n']);
    const events = [];
    for await (const event of readSse(body, new AbortController().signal)) events.push(event);
    expect(events).toEqual([
      { event: 'message.delta', id: '1', data: { a: 1 } },
      { event: 'turn.completed', id: '2', data: {} },
    ]);
  });
});

describe('store', () => {
  it('сообщает подписчикам только об изменившихся значениях', () => {
    const store = createStore({ a: 1, b: 2 });
    let calls = 0;
    store.subscribe(() => { calls += 1; });
    store.set({ a: 1 });
    expect(calls).toBe(0);
    store.set({ a: 2 });
    store.set((state) => ({ b: state.b + 1 }));
    expect(calls).toBe(2);
    expect(store.get()).toEqual({ a: 2, b: 3 });
  });
});

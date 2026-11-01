import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const env = vi.hoisted(() => ({ api: vi.fn(), post: vi.fn() }));

vi.mock('../lib/api', async (original) => ({ ...(await original<typeof import('../lib/api')>()), api: env.api, post: env.post }));
vi.mock('../lib/external', () => ({ openExternal: () => true }));
vi.mock('./data', () => ({ refreshAccount: vi.fn(async () => undefined) }));
vi.mock('./limits', () => ({ loadLimits: vi.fn(async () => undefined), limitsStore: { get: () => ({ value: null }) } }));
vi.mock('./plans', () => ({
  loadPlans: vi.fn(async () => undefined),
  planName: () => 'Pro',
  plansStore: { get: () => ({ catalog: null }) },
}));

import { closePayment, payment, startPayment } from './payment';

const MINUTE = 60_000;
const request = { intent: 'plan', plan: 'pro', planName: 'Pro' } as never;

describe('поздняя оплата', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    env.api.mockReset();
    env.post.mockReset();
    env.post.mockResolvedValue({ id: 'link', url: 'https://pay.example/link' });
  });

  afterEach(() => {
    closePayment();
    vi.useRealTimers();
  });

  it('после 15 минут опрос идёт реже и всё ещё видит оплату', async () => {
    env.api.mockResolvedValue({ id: 'link', status: 'awaiting_payment' });
    await startPayment(request);
    await vi.advanceTimersByTimeAsync(15 * MINUTE);
    const before = env.api.mock.calls.length;
    expect(payment.get().stage).toBe('waiting');
    await vi.advanceTimersByTimeAsync(5 * MINUTE);
    const late = env.api.mock.calls.length - before;
    // редкий опрос: раз в 30 секунд, а не каждые 2,5
    expect(late).toBeGreaterThan(5);
    expect(late).toBeLessThan(15);
    env.api.mockResolvedValue({ id: 'link', status: 'paid' });
    await vi.advanceTimersByTimeAsync(MINUTE);
    expect(payment.get().stage).toBe('paid');
  });

  it('сервер сказал, что ссылка устарела: ожидание заканчивается', async () => {
    env.api.mockResolvedValue({ id: 'link', status: 'awaiting_payment' });
    await startPayment(request);
    await vi.advanceTimersByTimeAsync(20 * MINUTE);
    env.api.mockResolvedValue({ id: 'link', status: 'expired' });
    await vi.advanceTimersByTimeAsync(MINUTE);
    expect(payment.get().stage).toBe('expired');
  });
});

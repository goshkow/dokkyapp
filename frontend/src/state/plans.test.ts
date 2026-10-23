import { describe, expect, it } from 'vitest';
import { ApiError } from '../lib/api';
import { paymentFailure } from './payment';
import { developerPlan, nextPlan, parseCatalog, planButton, planName, type Catalog } from './plans';
import { resolveAccess } from './access';
import { normalizeLimits } from './limits';

const raw = {
  catalog_version: 'v1',
  payment_available: true,
  gates: { developer: { min_plan: 'super', min_plan_name: 'Plus' } },
  current: { plan: 'base', plan_name: 'Base', expires_at: '2026-11-18T12:00:00Z' },
  plans: [
    { id: 'base', name: 'Base', action: 'current', price: { amount: 100 }, features: [{ id: 'usage', label: 'Объём', icon: { svg: '<svg/>' } }] },
    { id: 'super', name: 'Plus', action: 'upgrade', price: { amount: 300, original_amount: 300 }, checkout_price: { amount: 200 }, features: [{ id: 'a', label: 'А' }, { id: 'b', label: 'Б', from_previous_plan: true }] },
    { id: 'ultra', name: 'Ultra', action: 'upgrade', price: { amount: 1000 }, checkout_price: { amount: 900 }, features: [] },
  ],
};

describe('каталог планов', () => {
  it('читает ответ сервера и не зашивает названия', () => {
    const catalog = parseCatalog(raw) as Catalog;
    expect(catalog.plans.map((plan) => plan.name)).toEqual(['Base', 'Plus', 'Ultra']);
    expect(catalog.included_heading).toBe('Также включено:');
    expect(planName('super', catalog)).toBe('Plus');
    expect(planName('mega', catalog)).toBe('Mega');
    expect(planName('free', catalog)).toBe('Free');
  });

  it('ответ в непонятном виде - это отсутствие каталога, а не падение', () => {
    for (const bad of [null, 5, {}, { plans: [] }, { plans: [null, { name: 'без id' }] }]) expect(parseCatalog(bad)).toBeNull();
  });

  it('неизвестное действие кнопки закрывает покупку', () => {
    const catalog = parseCatalog({ plans: [{ id: 'x', name: 'X', action: 'steal' }] }) as Catalog;
    expect(catalog.plans[0]?.action).toBe('unavailable');
    expect(planButton(catalog.plans[0]!).disabled).toBe(true);
  });

  it('подписи кнопок берутся из действия сервера', () => {
    const catalog = parseCatalog(raw) as Catalog;
    const [base, plus] = catalog.plans;
    expect(planButton(base!, { expiresAt: '2026-11-18T12:00:00Z' })).toEqual({ label: 'Активно до 18.11', disabled: true });
    expect(planButton(plus!)).toEqual({ label: 'Улучшить до Plus', disabled: false });
    expect(planButton({ ...plus!, action: 'included' }, { name: 'Premium' }).label).toBe('Включено в Premium');
    expect(planButton({ ...plus!, action: 'subscribe' }).label).toBe('Купить Plus');
  });

  it('минимальный план для Докки Code и следующий план берутся из каталога', () => {
    const catalog = parseCatalog(raw) as Catalog;
    expect(developerPlan(catalog).name).toBe('Plus');
    expect(developerPlan(catalog).plan?.checkout_price?.amount).toBe(200);
    expect(nextPlan(catalog)?.id).toBe('super');
    expect(nextPlan({ ...catalog, plans: catalog.plans.map((plan) => ({ ...plan, action: 'current' as const })) })).toBeNull();
  });
});

describe('доступ к Докки Code', () => {
  const closed = { developer: { allowed: false, reason: 'plus_required' } };
  const open = { developer: { allowed: true, reason: '' } };

  it('подписка важнее списка моделей', () => {
    expect(resolveAccess(closed, [{ id: 'dokky-code', available: true }], false).locked).toBe(true);
    expect(resolveAccess(open, [{ id: 'dokky-code', available: false, unavailable_reason: 'plus_required' }], false).locked).toBe(false);
  });

  it('до чтения подписки решает bootstrap', () => {
    expect(resolveAccess(null, [{ id: 'dokky-code', available: false, unavailable_reason: 'plus_required' }], false)).toEqual({ locked: true, reason: 'plus_required' });
    expect(resolveAccess(null, [{ id: 'dokky-code', available: false, unavailable_reason: 'window_exhausted' }], false).locked).toBe(false);
    expect(resolveAccess(null, [], false).locked).toBe(false);
  });

  it('отказ сервера закрывает доступ сразу', () => {
    expect(resolveAccess(open, [], true).locked).toBe(true);
  });

  it('исчерпанный лимит не запирает режим', () => {
    const limits = normalizeLimits({ plan: 'super', developer: { allowed: false, reason_code: 'window_exhausted', window_used_percent: 35, window_percent: 35 } });
    expect(limits.developer.allowed).toBe(true);
    expect(limits.windowRemaining).toBe(0);
    const free = normalizeLimits({ plan: 'free', developer: { allowed: false, reason_code: 'plus_required', reason: 'Нужен Plus' } });
    expect(resolveAccess(free, [], false)).toEqual({ locked: true, reason: 'plus_required' });
  });
});

describe('подписка', () => {
  it('сброс использования приходит готовым блоком', () => {
    const limits = normalizeLimits({
      plan: 'super', remaining_percent: 7, paid_active: true, catalog_version: 'v9',
      usage_reset: { available: true, threshold_percent: 10, options: [{ percent: 25, price: 90, remaining_after_percent: 32 }, { percent: 0 }] },
    });
    expect(limits.catalogVersion).toBe('v9');
    expect(limits.usageReset).toEqual({ available: true, reason: null, thresholdPercent: 10, options: [{ percent: 25, price: 90, remainingAfter: 32 }] });
  });

  it('остаток может быть больше 100%', () => {
    expect(normalizeLimits({ plan: 'ultra', remaining_percent: 109 }).remaining).toBe(109);
  });
});

describe('отказы в ссылке на оплату', () => {
  it('говорят по-человечески и не предлагают бессмысленный повтор', () => {
    expect(paymentFailure(new ApiError('x', 409, 'conflict', { reason: 'already_included' }))).toMatchObject({ title: 'План уже включён', canRetry: false });
    expect(paymentFailure(new ApiError('x', 409, 'conflict', { reason: 'no_paid_plan' }))).toMatchObject({ offerPlans: true, canRetry: false });
    expect(paymentFailure(new ApiError('x', 429, 'rate_limited', { retryAfter: 9 })).text).toContain('9 с');
    expect(paymentFailure(new ApiError('x', 503, 'upstream_unavailable'))).toMatchObject({ title: 'Оплата временно недоступна', canRetry: true });
    expect(paymentFailure(new Error('обрыв'))).toMatchObject({ canRetry: true });
  });
});

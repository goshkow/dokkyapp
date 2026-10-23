import { api, cloudPath } from '../lib/api';
import { createStore, useStore } from '../lib/store';
import type { Limits, UsageReset } from '../lib/types';

/** Остаток лимитов и состояние подписки: проценты, время освобождения, доступ к Докки Code. */
interface LimitsState {
  value: Limits | null;
  error: boolean;
  loading: boolean;
  cap: number;
}

export const limitsStore = createStore<LimitsState>({ value: null, error: false, loading: false, cap: 35 });
export const useLimits = <S,>(selector: (state: LimitsState) => S): S => useStore(limitsStore, selector);

const clamp = (value: number) => Math.max(0, Math.min(100, Math.round(value)));
const num = (value: unknown): number => (value == null || value === '' || !Number.isFinite(Number(value)) ? NaN : Number(value));

interface RawUsageReset {
  available?: boolean;
  reason?: string | null;
  threshold_percent?: number;
  options?: Array<{ percent?: number; price?: number | null; remaining_after_percent?: number | null }>;
}

export interface Subscription {
  plan?: string | null;
  trial?: boolean;
  paid_active?: boolean;
  usage_exhausted?: boolean;
  remaining_percent?: number | null;
  expires_at?: string | null;
  catalog_version?: string | null;
  usage_reset?: RawUsageReset | null;
  developer?: {
    allowed?: boolean | null;
    reason_code?: string | null;
    reason?: string | null;
    window_percent?: number;
    window_used_percent?: number;
    window_resets_at?: string | null;
    window_hours?: number;
  };
}

/** Причины, по которым Докки Code закрыт не тарифом, а израсходованным лимитом: человек остаётся в режиме. */
const LIMIT_REASONS = new Set(['window_exhausted', 'overall_exhausted', 'usage_exhausted', 'quota_exceeded']);

export function normalizeUsageReset(raw: RawUsageReset | null | undefined): UsageReset | null {
  if (!raw || typeof raw !== 'object') return null;
  const options = Array.isArray(raw.options)
    ? raw.options
      .filter((item) => Number.isFinite(Number(item?.percent)) && Number(item.percent) > 0)
      .map((item) => ({
        percent: Number(item.percent),
        price: Number.isFinite(Number(item.price)) && item.price != null ? Number(item.price) : null,
        remainingAfter: Number.isFinite(Number(item.remaining_after_percent)) && item.remaining_after_percent != null ? Number(item.remaining_after_percent) : null,
      }))
    : [];
  return { available: raw.available === true, reason: raw.reason ?? null, thresholdPercent: num(raw.threshold_percent) || 10, options };
}

export function normalizeLimits(subscription: Subscription): Limits {
  const developer = subscription.developer ?? {};
  const cap = num(developer.window_percent) > 0 ? num(developer.window_percent) : limitsStore.get().cap;
  limitsStore.set({ cap });
  const used = num(developer.window_used_percent);
  const remaining = num(subscription.remaining_percent);
  const allowed = typeof developer.allowed === 'boolean' ? developer.allowed : null;
  const reason = String(developer.reason_code ?? '');
  return {
    windowRemaining: Number.isFinite(used) ? clamp((1 - used / cap) * 100) : null,
    windowResetsAt: developer.window_resets_at ?? null,
    windowHours: Number(developer.window_hours) || 5,
    remaining: Number.isFinite(remaining) ? Math.max(0, Math.round(remaining)) : null,
    expiresAt: subscription.expires_at ?? null,
    plan: subscription.plan ? String(subscription.plan).toLowerCase() : null,
    trial: subscription.trial === true,
    paidActive: subscription.paid_active === true,
    usageExhausted: subscription.usage_exhausted === true,
    catalogVersion: subscription.catalog_version ? String(subscription.catalog_version) : null,
    usageReset: normalizeUsageReset(subscription.usage_reset),
    developer: {
      allowed: allowed === false && LIMIT_REASONS.has(reason) ? true : allowed,
      reason,
      message: String(developer.reason ?? '').slice(0, 300),
    },
  };
}

const empty: Limits = {
  windowRemaining: null, windowResetsAt: null, windowHours: 5, remaining: null, expiresAt: null, plan: null,
  trial: false, paidActive: false, usageExhausted: false, catalogVersion: null, usageReset: null,
  developer: { allowed: null, reason: '', message: '' },
};

interface Billing {
  window_used_percent?: number;
  window_resets_at?: string | null;
  remaining_percent?: number;
}

/** Данные из ответа модели (`dokky.billing`): только проценты окна и остатка. */
export function applyBillingLimits(billing: Billing | undefined): void {
  if (!billing || typeof billing !== 'object') return;
  const next: Limits = { ...empty, ...(limitsStore.get().value ?? {}) };
  if (billing.window_used_percent != null && Number.isFinite(Number(billing.window_used_percent))) {
    next.windowRemaining = clamp((1 - Number(billing.window_used_percent) / limitsStore.get().cap) * 100);
    next.windowResetsAt = billing.window_resets_at ?? null;
  }
  if (billing.remaining_percent != null && Number.isFinite(Number(billing.remaining_percent))) {
    next.remaining = Math.max(0, Math.round(Number(billing.remaining_percent)));
  }
  limitsStore.set({ value: next });
}

type Listener = (previous: Limits | null, next: Limits) => void;
const listeners = new Set<Listener>();
/** Подписка на каждое свежее чтение `/subscription`: план сменился, каталог обновился, доступ изменился. */
export const onLimitsLoaded = (listener: Listener): (() => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

let request: Promise<void> | null = null;
export function loadLimits(): Promise<void> {
  if (request) return request;
  limitsStore.set({ loading: true });
  request = api<Subscription>(cloudPath('subscription'))
    .then((value) => {
      const previous = limitsStore.get().value;
      const next = normalizeLimits(value);
      limitsStore.set({ value: next, error: false });
      for (const listener of listeners) listener(previous, next);
    })
    .catch(() => {
      limitsStore.set({ error: !limitsStore.get().value });
    })
    .finally(() => {
      request = null;
      limitsStore.set({ loading: false });
    });
  return request;
}

export function resetLimits(): void {
  limitsStore.set({ value: null, error: false });
}

import { ApiError, api, cloudPath } from '../lib/api';
import { createStore, useStore } from '../lib/store';
import type { UsageReset } from '../lib/types';

/* Каталог планов целиком приходит с сервера (`GET /billing/plans`): названия, цены, функции, иконки и порядок.
   Ничего из этого в приложении не зашито: владелец меняет каталог на сайте, приложение показывает новое без обновления. */

export interface PlanIcon { name?: string; url?: string; svg?: string }
export interface PlanFeature {
  id: string;
  label: string;
  description?: string;
  from_previous_plan?: boolean;
  icon?: PlanIcon;
}
export interface PlanPrice { amount: number; original_amount?: number; discount_percent?: number; currency?: string; period_months?: number }
export type PlanAction = 'subscribe' | 'upgrade' | 'current' | 'included' | 'unavailable';
export interface Plan {
  id: string;
  name: string;
  tagline?: string;
  action: PlanAction;
  is_current?: boolean;
  price?: PlanPrice | null;
  /** Сколько человек заплатит сейчас: при улучшении это доплата; `null`, если платить не нужно. */
  checkout_price?: { amount: number; currency?: string } | null;
  usage_multiplier?: number;
  features: PlanFeature[];
}
export interface CatalogCurrent {
  plan?: string;
  plan_name?: string;
  paid_active?: boolean;
  trial?: boolean;
  unlimited?: boolean;
  expires_at?: string | null;
  remaining_percent?: number | null;
  renewal?: { available?: boolean; enabled?: boolean; next_charge_at?: string | null };
}
export interface CatalogReset {
  available?: boolean;
  reason?: string | null;
  threshold_percent?: number;
  remaining_percent?: number;
  tier?: string;
  tier_name?: string;
  options?: Array<{ percent: number; price?: number | null; currency?: string; remaining_after_percent?: number | null }>;
}
export interface Catalog {
  catalog_version: string;
  currency: string;
  period_months: number;
  payment_available: boolean;
  included_heading: string;
  gates: { developer?: { min_plan?: string; min_plan_name?: string } };
  current: CatalogCurrent;
  usage_reset: CatalogReset | null;
  plans: Plan[];
}

const ACTIONS = new Set<PlanAction>(['subscribe', 'upgrade', 'current', 'included', 'unavailable']);
const text = (value: unknown, fallback = ''): string => (typeof value === 'string' ? value : fallback);
const finite = (value: unknown): number | undefined => (typeof value === 'number' && Number.isFinite(value) ? value : undefined);

function parsePlan(raw: unknown): Plan | null {
  if (!raw || typeof raw !== 'object') return null;
  const item = raw as Record<string, unknown>;
  const id = text(item.id);
  if (!id) return null;
  const action = ACTIONS.has(item.action as PlanAction) ? (item.action as PlanAction) : 'unavailable';
  const price = item.price && typeof item.price === 'object' ? (item.price as Record<string, unknown>) : null;
  const checkout = item.checkout_price && typeof item.checkout_price === 'object' ? (item.checkout_price as Record<string, unknown>) : null;
  const features = Array.isArray(item.features) ? item.features.flatMap((entry): PlanFeature[] => {
    if (!entry || typeof entry !== 'object') return [];
    const feature = entry as Record<string, unknown>;
    const label = text(feature.label);
    if (!label) return [];
    const icon = feature.icon && typeof feature.icon === 'object' ? (feature.icon as Record<string, unknown>) : null;
    return [{
      id: text(feature.id, label),
      label,
      description: text(feature.description) || undefined,
      from_previous_plan: feature.from_previous_plan === true,
      icon: icon ? { name: text(icon.name) || undefined, url: text(icon.url) || undefined, svg: text(icon.svg) || undefined } : undefined,
    }];
  }) : [];
  return {
    id,
    name: text(item.name, id),
    tagline: text(item.tagline) || undefined,
    action,
    is_current: item.is_current === true,
    price: price && finite(price.amount) != null
      ? { amount: price.amount as number, original_amount: finite(price.original_amount), discount_percent: finite(price.discount_percent), currency: text(price.currency, 'RUB'), period_months: finite(price.period_months) }
      : null,
    checkout_price: checkout && finite(checkout.amount) != null ? { amount: checkout.amount as number, currency: text(checkout.currency, 'RUB') } : null,
    usage_multiplier: finite(item.usage_multiplier),
    features,
  };
}

/** Ответ в непонятном виде считается отсутствием каталога: экран подписки показывает ошибку, а не падает. */
export function parseCatalog(raw: unknown): Catalog | null {
  if (!raw || typeof raw !== 'object') return null;
  const value = raw as Record<string, unknown>;
  const plans = Array.isArray(value.plans) ? value.plans.map(parsePlan).filter((plan): plan is Plan => plan !== null) : [];
  if (!plans.length) return null;
  const gates = value.gates && typeof value.gates === 'object' ? (value.gates as Catalog['gates']) : {};
  const reset = value.usage_reset && typeof value.usage_reset === 'object' ? (value.usage_reset as CatalogReset) : null;
  return {
    catalog_version: text(value.catalog_version),
    currency: text(value.currency, 'RUB'),
    period_months: finite(value.period_months) ?? 1,
    payment_available: value.payment_available !== false,
    included_heading: text(value.included_heading, 'Также включено:'),
    gates,
    current: value.current && typeof value.current === 'object' ? (value.current as CatalogCurrent) : {},
    usage_reset: reset,
    plans,
  };
}

interface PlansState {
  catalog: Catalog | null;
  loading: boolean;
  error: string;
}

export const plansStore = createStore<PlansState>({ catalog: null, loading: false, error: '' });
export const usePlans = <S,>(selector: (state: PlansState) => S): S => useStore(plansStore, selector);

let pending: Promise<void> | null = null;
/** Каталог персональный (цены, действие кнопки), поэтому экран подписки всегда читает его заново. */
export function loadPlans(): Promise<void> {
  if (pending) return pending;
  plansStore.set({ loading: true });
  pending = api<unknown>(cloudPath('billing/plans'))
    .then((raw) => {
      const catalog = parseCatalog(raw);
      if (!catalog) throw new ApiError('Сервер вернул планы в непонятном виде', 502);
      plansStore.set({ catalog, error: '' });
    })
    .catch((error: Error) => {
      plansStore.set({ error: error.message || 'Не удалось загрузить планы' });
    })
    .finally(() => {
      pending = null;
      plansStore.set({ loading: false });
    });
  return pending;
}

export function resetPlans(): void {
  plansStore.set({ catalog: null, error: '', loading: false });
}

/** Название плана из каталога сервера; пока каталога нет - идентификатор как есть (кроме служебных free и trial). */
export function planName(id: string | null | undefined, catalog: Catalog | null = plansStore.get().catalog): string {
  const key = String(id ?? '').toLowerCase();
  if (!key) return '';
  const found = catalog?.plans.find((plan) => plan.id === key);
  if (found) return found.name;
  if (catalog?.current.plan === key && catalog.current.plan_name) return catalog.current.plan_name;
  if (key === 'free') return 'Free';
  if (key === 'trial') return 'Trial';
  return key.charAt(0).toUpperCase() + key.slice(1);
}

/** Минимальный план для Докки Code из каталога: название и сам план с ценой. */
export function developerPlan(catalog: Catalog | null): { id: string; name: string; plan: Plan | null } {
  const gate = catalog?.gates.developer;
  const id = gate?.min_plan ?? '';
  const plan = catalog?.plans.find((item) => item.id === id) ?? null;
  return { id, name: gate?.min_plan_name || plan?.name || '', plan };
}

/** Следующий план выше текущего (для подсказки «Улучшить»); у самого старшего плана его нет. */
export function nextPlan(catalog: Catalog | null): Plan | null {
  if (!catalog) return null;
  const upgrades = catalog.plans.filter((plan) => plan.action === 'upgrade' || plan.action === 'subscribe');
  if (!upgrades.length) return null;
  const current = catalog.plans.findIndex((plan) => plan.action === 'current');
  if (current < 0) return upgrades[0] ?? null;
  return catalog.plans.slice(current + 1).find((plan) => plan.action === 'upgrade') ?? null;
}

const money = (amount: number, currency = 'RUB'): string => {
  const sign = currency === 'RUB' ? '₽' : currency;
  return `${amount.toLocaleString('ru-RU', { maximumFractionDigits: 2 })} ${sign}`;
};
export { money };

/** Подпись главной кнопки карточки по `action` сервера; «Включено в …» называет текущий план человека. */
export function planButton(plan: Plan, current?: { expiresAt?: string | null; name?: string; unlimited?: boolean }): { label: string; disabled: boolean } {
  switch (plan.action) {
    case 'subscribe': return { label: `Купить ${plan.name}`, disabled: false };
    case 'upgrade': return { label: `Улучшить до ${plan.name}`, disabled: false };
    case 'current': {
      const date = current?.expiresAt ? new Date(current.expiresAt) : null;
      const until = date && Number.isFinite(date.getTime()) ? date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }) : '';
      return { label: until ? `Активно до ${until}` : 'Активно', disabled: true };
    }
    case 'included': return { label: `Включено в ${current?.name || 'ваш план'}`, disabled: true };
    // безлимитному доступу платить не за что; старый сервер присылал для него unavailable
    default: return current?.unlimited ? { label: 'Включено в ваш доступ', disabled: true } : { label: 'Оплата временно недоступна', disabled: true };
  }
}

/** Блок сброса использования из каталога планов (с ценами) в виде, который понимает экран. */
export function resetFromCatalog(catalog: Catalog | null): UsageReset | null {
  const raw = catalog?.usage_reset;
  if (!raw) return null;
  return {
    available: raw.available === true,
    reason: raw.reason ?? null,
    thresholdPercent: raw.threshold_percent ?? 10,
    options: (raw.options ?? []).map((option) => ({ percent: option.percent, price: option.price ?? null, remainingAfter: option.remaining_after_percent ?? null })),
  };
}

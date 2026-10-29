import { announceTray, type TrayPayload } from '../lib/bridge';
import type { Limits } from '../lib/types';
import { limitsStore } from './limits';
import { planName, plansStore, type Catalog } from './plans';

const seconds = (value: string | null): number | null => {
  const at = value ? Date.parse(value) : NaN;
  return Number.isFinite(at) ? Math.round(at / 1000) : null;
};

/** Лимиты для меню значка в трее: те же проценты, что у плашки внизу меню; у Free проценты не показываются. */
export function trayPayload(limits: Limits | null, catalog: Catalog | null): TrayPayload {
  if (!limits) return { plan: null, window: null, total: null };
  const free = limits.plan === 'free';
  return {
    plan: planName(limits.plan, catalog) || null,
    window: !free && limits.windowRemaining != null
      ? { remaining: limits.windowRemaining, hours: limits.windowHours, resets_at: seconds(limits.windowResetsAt) }
      : null,
    total: !free && limits.remaining != null ? { remaining: limits.remaining, resets_at: seconds(limits.expiresAt) } : null,
  };
}

let sent = '';
function send(): void {
  const payload = JSON.stringify(trayPayload(limitsStore.get().value, plansStore.get().catalog));
  if (payload !== sent && announceTray(payload)) sent = payload;
}

/** Меню трея обновляется при каждом новом чтении `/subscription` и ответе модели с процентами. */
export function startTray(): void {
  limitsStore.subscribe(send);
  plansStore.subscribe(send);
  if (window.koddiWindowBridge) send();
  else addEventListener('koddi-bridge', send, { once: true });
}

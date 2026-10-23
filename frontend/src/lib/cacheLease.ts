export const CACHE_ACTIVE_MS = 5 * 60_000;
const LEAD_MS = 4 * 60_000;

/** продление нужно только тогда, когда срок кэша истекает во время недавней работы с диалогом. */
export function keepaliveDelay(cache: { prompt_tokens?: number; valid_until?: string; ttl_seconds?: number | null } | undefined, now: number, idle: number, enabled: boolean, busy: boolean): number | null {
  const until = Date.parse(cache?.valid_until ?? '');
  if (!enabled || busy || idle > CACHE_ACTIVE_MS || !(Number(cache?.prompt_tokens) >= 4000) || !Number.isFinite(until) || until <= now) return null;
  // запас - пятая часть срока: при кэше на 5 минут одно продление за паузу, а не пинг каждую минуту
  const ttl = Number(cache?.ttl_seconds) * 1000;
  const lead = ttl > 0 ? Math.min(LEAD_MS, ttl / 5) : LEAD_MS;
  const delay = Math.max(0, until - now - lead);
  return idle + delay <= CACHE_ACTIVE_MS ? delay : null;
}

/** Что делать после оборванного хода: повторить позже (сервер не отвечал) или ждать лимит. */

import type { Limits } from './types';

export type RecoveryKind = 'stalled' | 'limit';

// сервер или модель не отвечали и повторы кончились: работу можно продолжить позже
const STALLED = new Set(['ai_unavailable', 'upstream_unavailable', 'empty_response', 'remote_error']);
// закончился лимит использования: продолжить можно, когда он восстановится
const LIMIT = new Set(['quota_exceeded', 'window_exhausted', 'usage_exhausted']);

export function recoveryKind(code: string): RecoveryKind | null {
  if (STALLED.has(code)) return 'stalled';
  if (LIMIT.has(code)) return 'limit';
  return null;
}

/** Код ошибки хода из error_json, который сервер кладёт в metadata.turn. */
export function turnErrorCode(errorJson: unknown): string {
  if (typeof errorJson !== 'string' || !errorJson) return '';
  try {
    const value = JSON.parse(errorJson) as { code?: unknown };
    return typeof value?.code === 'string' ? value.code : '';
  } catch {
    return '';
  }
}

/** «через 2 ч 15 мин» / «через 40 мин» / «через минуту» до восстановления лимита. */
export function untilText(resetsAt: number, now = Date.now()): string {
  const minutes = Math.max(1, Math.ceil((resetsAt - now) / 60_000));
  if (minutes < 2) return 'через минуту';
  if (minutes < 60) return `через ${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  if (hours >= 24) return `через ${Math.round(hours / 24)} дн`;
  const rest = minutes % 60;
  return rest ? `через ${hours} ч ${rest} мин` : `через ${hours} ч`;
}

export interface LimitState {
  /** Работать снова можно: окно освободилось и подписка не кончилась. */
  restored: boolean;
  /** Кончилась вся подписка: время не поможет, нужен сброс или другой план. */
  overall: boolean;
  resetsAt: number;
}

/** Состояние лимита по свежему `/subscription`. Сервер шлёт `quota_exceeded` и для окна, и для подписки, поэтому смотрим на остатки. */
export function limitState(limits: Limits | null, now = Date.now()): LimitState {
  const resetsAt = limits?.windowResetsAt ? Date.parse(limits.windowResetsAt) : NaN;
  if (!limits) return { restored: false, overall: false, resetsAt };
  const reason = limits.developer.reason;
  const overall = limits.usageExhausted || limits.remaining === 0 || reason === 'overall_exhausted' || reason === 'usage_exhausted';
  const windowOut = (limits.windowRemaining != null && limits.windowRemaining <= 0) || reason === 'window_exhausted';
  const windowBack = !windowOut || (Number.isFinite(resetsAt) && now >= resetsAt);
  return { restored: !overall && windowBack, overall, resetsAt };
}

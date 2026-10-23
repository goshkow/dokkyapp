import { describe, expect, it } from 'vitest';
import { CACHE_ACTIVE_MS, keepaliveDelay } from './cacheLease';

const now = Date.UTC(2026, 9, 21, 13, 0);
const cache = (minutes: number, prompt_tokens = 8000) => ({ prompt_tokens, valid_until: new Date(now + minutes * 60_000).toISOString() });

describe('продление кэша во время работы', () => {
  it('продлевает подходящий кэш перед истечением срока', () => {
    expect(keepaliveDelay(cache(3), now, 1000, true, false)).toBe(0);
    expect(keepaliveDelay(cache(6), now, 1000, true, false)).toBe(2 * 60_000);
  });

  it('не планирует платный запрос после завершения окна активности', () => {
    expect(keepaliveDelay(cache(3), now, CACHE_ACTIVE_MS + 1, true, false)).toBeNull();
    expect(keepaliveDelay(cache(20), now, 0, true, false)).toBeNull();
    expect(keepaliveDelay(cache(6), now, 4 * 60_000, true, false)).toBeNull();
  });

  it('не продлевает отключённый, занятый, маленький или истёкший кэш', () => {
    expect(keepaliveDelay(cache(3), now, 0, false, false)).toBeNull();
    expect(keepaliveDelay(cache(3), now, 0, true, true)).toBeNull();
    expect(keepaliveDelay(cache(3, 3999), now, 0, true, false)).toBeNull();
    expect(keepaliveDelay(cache(-1), now, 0, true, false)).toBeNull();
    expect(keepaliveDelay(undefined, now, 0, true, false)).toBeNull();
  });

  it('при кэше на 5 минут продлевает один раз за паузу, а не каждую минуту', () => {
    const short = { ...cache(5), ttl_seconds: 300 };
    expect(keepaliveDelay(short, now, 0, true, false)).toBe(4 * 60_000);
    // после продления в 4 мин кэш до 9 мин, следующий пинг вышел бы за окно активности
    expect(keepaliveDelay({ ...cache(5), ttl_seconds: 300 }, now, 4 * 60_000, true, false)).toBeNull();
  });
});

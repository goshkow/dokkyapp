/** Время сервера в миллисекундах. SQLite отдаёт «2026-10-17 10:00:00» без пояса, это UTC: без разбора браузер читает его как местное. */
export function serverTime(value: unknown): number {
  if (typeof value !== 'string' || !value) return NaN;
  const iso = value.trim().replace(' ', 'T');
  return Date.parse(/[zZ]$|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
}

/** Короткое время в списке чатов: «сейчас», «5 мин», «2 ч», «3 д», «2 нед», «4 мес». */
export function relativeTime(value: string | undefined, now = Date.now()): string {
  const moment = serverTime(value);
  if (!Number.isFinite(moment)) return '';
  const minutes = Math.max(0, Math.round((now - moment) / 60000));
  if (minutes < 1) return 'сейчас';
  if (minutes < 60) return `${minutes} мин`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} ч`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days} д`;
  if (days < 30) return `${Math.round(days / 7)} нед`;
  return `${Math.round(days / 30)} мес`;
}

/** Путь без середины: C:\...\Desktop\project */
export function compactPath(path: string): string {
  const parts = path.split(/[\\/]+/).filter(Boolean);
  if (parts.length <= 3) return path;
  return `${parts[0]}\\...\\${parts.slice(-2).join('\\')}`;
}

export const numberText = (value: number): string => Number(value).toLocaleString('ru-RU');

import { safeSource } from './cloud';

export const SITE = 'https://dokky.goshkow.com/';

/** Открывает http(s)-ссылку в системном браузере (в окне приложения через мост хоста). */
export function openExternal(url: string): boolean {
  const safe = safeSource({ url });
  if (!safe) return false;
  if (window.koddiDesktop) window.location.href = `koddi://open-url?url=${encodeURIComponent(safe.href)}`;
  else window.open(safe.href, '_blank', 'noopener,noreferrer');
  return true;
}

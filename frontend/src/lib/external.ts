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

/** Открывает раздел «Конфиденциальность» Системных настроек macOS (только в окне приложения). */
export function openPrivacySettings(pane: 'accessibility' | 'screen_recording'): boolean {
  if (!window.koddiDesktop) return false;
  window.location.href = `koddi://open-privacy-settings?pane=${pane}`;
  return true;
}

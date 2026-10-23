/** Анимации отключены настройкой приложения или системой. */
export const reducedMotion = (): boolean =>
  document.documentElement.dataset.motion === 'reduced' || matchMedia('(prefers-reduced-motion: reduce)').matches;

export const EASE = 'cubic-bezier(.22,1,.36,1)';

/** Кадр после раскладки: для расчётов, которым нужны готовые размеры. */
export const nextFrame = (): Promise<void> => new Promise((resolve) => requestAnimationFrame(() => resolve()));

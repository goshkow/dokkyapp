import { reducedMotion } from './motion';

let running: Animation | null = null;
let notBefore = 0;

/**
 * Единственное проявление ленты. Его просят и новый экран, когда уходит, и лента, когда чат загрузился:
 * новая просьба заменяет идущую, иначе одно открытие чата мигает дважды. wait - сколько лента ещё закрыта уходящим экраном.
 */
export function revealFeed(duration = 260, wait = 0): void {
  const feed = document.getElementById('feed');
  if (!feed || reducedMotion() || typeof feed.animate !== 'function') return;
  const now = performance.now();
  if (wait > 0) notBefore = now + wait;
  running?.cancel();
  running = feed.animate([{ opacity: 0 }, { opacity: 1 }], { duration, delay: Math.max(0, notBefore - now), easing: 'ease-out', fill: 'backwards' });
}

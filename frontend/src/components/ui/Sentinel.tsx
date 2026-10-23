import { useEffect, useRef } from 'react';

/** Невидимая отметка в конце списка: когда она приближается к краю области прокрутки, вызывается `onReach` (подгрузка вместо кнопки «ещё»). */
export function Sentinel({ onReach, scroller, margin = 320, edge = 'bottom' }: { onReach: () => void; scroller: string; margin?: number; edge?: 'top' | 'bottom' }) {
  const node = useRef<HTMLDivElement>(null);
  const callback = useRef(onReach);
  callback.current = onReach;
  useEffect(() => {
    const element = node.current;
    if (!element) return undefined;
    const root = element.closest<HTMLElement>(scroller) ?? document.getElementById(scroller.replace(/^#/, ''));
    const watcher = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) callback.current();
    }, { root, rootMargin: edge === 'top' ? `${margin}px 0px 0px 0px` : `0px 0px ${margin}px 0px` });
    watcher.observe(element);
    return () => watcher.disconnect();
  }, [scroller, margin, edge]);
  return <div ref={node} className="sentinel" aria-hidden="true" />;
}

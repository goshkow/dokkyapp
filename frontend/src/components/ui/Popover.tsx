import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';

export interface PopoverProps {
  open: boolean;
  /** Элемент, рядом с которым раскрывается меню, или точка (контекстное меню). */
  anchor: RefObject<HTMLElement | null> | { x: number; y: number } | null;
  onClose: () => void;
  className?: string;
  role?: string;
  label?: string;
  /** Выше (above) или ниже (below) якоря; при нехватке места сторона меняется сама. */
  side?: 'below' | 'above';
  align?: 'start' | 'end';
  minWidth?: number | 'anchor';
  children: ReactNode;
  id?: string;
}

/** Плавающая панель: появляется и исчезает плавно, закрывается по Esc и по щелчку мимо, не выходит за окно. */
export function Popover({ open, anchor, onClose, className = '', role = 'menu', label, side = 'below', align = 'start', minWidth, children, id }: PopoverProps) {
  const [mounted, setMounted] = useState(open);
  const [state, setState] = useState<'open' | 'closed'>(open ? 'open' : 'closed');
  const node = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) {
      setMounted(true);
      setState('open');
    } else if (mounted) {
      setState('closed');
      const timer = window.setTimeout(() => setMounted(false), 140);
      return () => window.clearTimeout(timer);
    }
    return undefined;
  }, [open, mounted]);

  useLayoutEffect(() => {
    const panel = node.current;
    if (!mounted || !panel) return;
    // верхний слой: список виден поверх модальных окон (настройки), не прячется под ними и не обрезается
    try {
      if (!panel.matches(':popover-open')) panel.showPopover();
    } catch {
      /* старый движок без Popover API: остаётся обычный fixed */
    }
    if (minWidth === 'anchor' && anchor && 'current' in anchor && anchor.current) panel.style.minWidth = `${Math.round(anchor.current.getBoundingClientRect().width)}px`;
    else if (typeof minWidth === 'number') panel.style.minWidth = `${minWidth}px`;
    const place = () => {
      const rect = anchor && 'current' in anchor ? anchor.current?.getBoundingClientRect() : undefined;
      const point = anchor && !('current' in anchor) ? anchor : undefined;
      const trigger = anchor && 'current' in anchor ? anchor.current : null;
      const field = document.documentElement.classList.contains('native-tiles') && trigger?.closest('#composer')
        ? document.querySelector<HTMLElement>('#composer .composer-field')?.getBoundingClientRect() : null;
      // меню над нативным полем не пересекает его: стекло продолжает размывать ту же ленту
      if (field) panel.style.maxHeight = `${Math.max(40, field.top - 22)}px`;
      const width = panel.offsetWidth;
      const height = panel.offsetHeight;
      const viewport = { width: window.innerWidth, height: window.innerHeight };
      const gap = 6;
      let left = point ? point.x : align === 'end' && rect ? rect.right - width : rect?.left ?? 8;
      let top = point ? point.y : rect ? (side === 'above' ? rect.top - height - gap : rect.bottom + gap) : 8;
      if (field) top = field.top - height - 12;
      if (rect && side === 'below' && top + height > viewport.height - 8 && rect.top > viewport.height - rect.bottom) top = rect.top - height - gap;
      if (!field && rect && side === 'above' && top < 8 && viewport.height - rect.bottom > rect.top) top = rect.bottom + gap;
      left = Math.max(8, Math.min(left, viewport.width - width - 8));
      top = Math.max(8, Math.min(top, viewport.height - height - 8));
      panel.style.left = `${left}px`;
      panel.style.top = `${top}px`;
    };
    place();
    // содержимое меняется само (подменю, список подгрузился): положение пересчитывается по размеру
    const resize = new ResizeObserver(place);
    resize.observe(panel);
    const composer = document.getElementById('composer');
    if (composer) resize.observe(composer);
    return () => resize.disconnect();
  }, [mounted, anchor, side, align, minWidth, children]);

  useEffect(() => {
    if (!open) return undefined;
    const trigger = anchor && 'current' in anchor ? anchor.current : null;
    const pointer = (event: PointerEvent) => {
      const target = event.target as Node;
      if (node.current?.contains(target) || trigger?.contains(target)) return;
      onClose();
    };
    const key = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      // закрывается только список: окно под ним (настройки) остаётся
      event.preventDefault();
      event.stopPropagation();
      onClose();
      trigger?.focus({ preventScroll: true });
    };
    document.addEventListener('pointerdown', pointer, true);
    document.addEventListener('keydown', key, true);
    window.addEventListener('resize', onClose);
    return () => {
      document.removeEventListener('pointerdown', pointer, true);
      document.removeEventListener('keydown', key, true);
      window.removeEventListener('resize', onClose);
    };
  }, [open, anchor, onClose]);

  // фокус внутрь при открытии
  useEffect(() => {
    if (open && node.current) node.current.querySelector<HTMLElement>('[role="menuitem"]:not(:disabled), [role="option"][aria-selected="true"], [role="option"]:not(:disabled)')?.focus({ preventScroll: true });
  }, [open, mounted]);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const items = [...(node.current?.querySelectorAll<HTMLElement>('[role="menuitem"],[role="menuitemradio"],[role="menuitemcheckbox"],[role="option"]') ?? [])].filter((item) => !item.hasAttribute('disabled'));
    const index = items.indexOf(document.activeElement as HTMLElement);
    let next: HTMLElement | undefined;
    if (event.key === 'ArrowDown') next = items[(index + 1) % items.length];
    else if (event.key === 'ArrowUp') next = items[(index - 1 + items.length) % items.length];
    else if (event.key === 'Home') next = items[0];
    else if (event.key === 'End') next = items.at(-1);
    else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && event.key !== ' ') {
      next = items.find((item) => item.textContent?.trim().toLocaleLowerCase().startsWith(event.key.toLocaleLowerCase()));
    } else if (event.key === 'Tab') onClose();
    if (next) {
      event.preventDefault();
      next.focus({ preventScroll: true });
    }
  };

  if (!mounted) return null;
  // внутри открытого модального окна: вне его содержимое недоступно для щелчков
  const dialogs = document.querySelectorAll('dialog[open]');
  const host = dialogs[dialogs.length - 1] ?? document.body;
  return createPortal(
    <div id={id} ref={node} popover="manual" className={`popover ${className}`} role={role} aria-label={label} data-state={state} data-side={side} onKeyDown={onKeyDown}>
      {children}
    </div>,
    host,
  );
}

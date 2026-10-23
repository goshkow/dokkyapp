import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { EASE, reducedMotion } from '../../lib/motion';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  className?: string;
  summary: ReactNode;
  children: ReactNode;
  duration?: number;
  /** Вызывается, когда раскрытие или сворачивание закончено. */
  onSettled?: () => void;
  /** Элемент <details>, например чтобы сдвигать соседей при смене подписи. */
  innerRef?: { current: HTMLDetailsElement | null };
  [key: `data-${string}`]: string | undefined;
}

/** Раскрывающийся блок на <details>: высота меняется плавно, содержимое живёт, пока идёт анимация. */
export function Disclosure({ open, onOpenChange, className, summary, children, duration = 300, onSettled, innerRef, ...rest }: Props) {
  const own = useRef<HTMLDetailsElement>(null);
  const node = innerRef ?? own;
  const first = useRef(true);
  const animation = useRef<Animation | null>(null);
  const [mounted, setMounted] = useState(open);
  if (open && !mounted) setMounted(true);

  useLayoutEffect(() => {
    const el = node.current;
    if (!el) return undefined;
    const head = el.querySelector('summary');
    animation.current?.cancel();
    if (first.current) {
      first.current = false;
      el.open = open;
      return undefined;
    }
    if (reducedMotion() || !head) {
      el.open = open;
      if (!open) setMounted(false);
      return undefined;
    }
    const start = el.getBoundingClientRect().height;
    if (open) el.open = true;
    const end = open ? el.scrollHeight : head.getBoundingClientRect().height;
    el.style.overflow = 'hidden';
    const run = el.animate([{ height: `${start}px` }, { height: `${end}px` }], { duration, easing: EASE });
    animation.current = run;
    run.finished.then(() => {
      if (animation.current !== run) return;
      el.open = open;
      el.style.overflow = '';
      if (!open) setMounted(false);
      onSettled?.();
    }).catch(() => undefined);
    return () => { run.cancel(); el.style.overflow = ''; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  return (
    <details ref={node} className={className} {...rest}>
      <summary
        onClick={(event) => {
          event.preventDefault();
          onOpenChange(!open);
        }}
        aria-expanded={open}
      >
        {summary}
      </summary>
      {(open || mounted) && children}
    </details>
  );
}

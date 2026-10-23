import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';

interface Props {
  open: boolean;
  onClose: () => void;
  className: string;
  label?: string;
  /** Время закрывающей анимации, мс. */
  closeDelay?: number;
  /** Щелчок по затемнению закрывает окно. */
  backdropClose?: boolean;
  children: ReactNode;
}

/** Модальное окно со стилями прежнего интерфейса: класс задаёт вид, анимацию закрытия даёт `is-closing`. */
export function PlainDialog({ open, onClose, className, label, closeDelay = 150, backdropClose = true, children }: Props) {
  const node = useRef<HTMLDialogElement>(null);
  const [mounted, setMounted] = useState(open);
  const [closing, setClosing] = useState(false);

  useEffect(() => {
    if (open) {
      setMounted(true);
      setClosing(false);
      return undefined;
    }
    if (!mounted) return undefined;
    setClosing(true);
    const delay = document.documentElement.dataset.motion === 'reduced' ? 0 : closeDelay;
    const timer = window.setTimeout(() => {
      setMounted(false);
      setClosing(false);
    }, delay);
    return () => window.clearTimeout(timer);
  }, [open, mounted, closeDelay]);

  useLayoutEffect(() => {
    const dialog = node.current;
    if (mounted && dialog && !dialog.open) dialog.showModal();
  }, [mounted]);

  if (!mounted) return null;
  return (
    <dialog
      ref={node}
      className={`${className}${closing ? ' is-closing' : ''}`}
      aria-label={label}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (backdropClose && event.target === node.current) onClose();
      }}
    >
      {children}
    </dialog>
  );
}

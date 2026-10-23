import { useEffect, useRef, useState, type ReactNode } from 'react';

interface DialogProps {
  open: boolean;
  onClose: () => void;
  className?: string;
  label?: string;
  labelledBy?: string;
  children: ReactNode;
}

/** Модальное окно на <dialog>: плавно появляется и закрывается, Esc и щелчок по фону закрывают. */
export function Dialog({ open, onClose, className = '', label, labelledBy, children }: DialogProps) {
  const node = useRef<HTMLDialogElement>(null);
  const [closing, setClosing] = useState(false);

  useEffect(() => {
    const dialog = node.current;
    if (!dialog) return undefined;
    if (open) {
      setClosing(false);
      if (!dialog.open) dialog.showModal();
      return undefined;
    }
    if (!dialog.open) return undefined;
    setClosing(true);
    const delay = document.documentElement.dataset.motion === 'reduced' ? 0 : 150;
    const timer = window.setTimeout(() => {
      dialog.close();
      setClosing(false);
    }, delay);
    return () => window.clearTimeout(timer);
  }, [open]);

  return (
    <dialog
      ref={node}
      className={`dialog ${className}`}
      data-closing={closing || undefined}
      aria-label={label}
      aria-labelledby={labelledBy}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === node.current) onClose();
      }}
    >
      {children}
    </dialog>
  );
}

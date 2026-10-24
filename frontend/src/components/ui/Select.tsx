import { ChevronDown } from 'lucide-react';
import { useRef, useState } from 'react';
import { Popover } from './Popover';

export interface SelectOption<T extends string | number> {
  value: T;
  label: string;
  disabled?: boolean;
}

interface SelectProps<T extends string | number> {
  value: T;
  options: SelectOption<T>[];
  onChange: (value: T) => void;
  label: string;
  className?: string;
  disabled?: boolean;
  hidden?: boolean;
  /** Меню раскрывается над кнопкой (поле ввода внизу окна). */
  side?: 'below' | 'above';
}

/** Список выбора в стиле приложения вместо системного <select>. */
export function Select<T extends string | number>({ value, options, onChange, label, className = '', disabled, hidden, side = 'below' }: SelectProps<T>) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  if (hidden) return null;
  const current = options.find((option) => option.value === value);
  return (
    <>
      <button
        ref={trigger}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={label}
        className={`select-trigger ${className}`}
        disabled={disabled}
        onClick={() => setOpen((state) => !state)}
        onKeyDown={(event) => {
          // удержанный Enter из поля ввода долетает сюда, когда открывается диалог, и щёлкал бы список туда-сюда
          if (event.key === 'Enter' && event.repeat) {
            event.preventDefault();
            return;
          }
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            setOpen(true);
          }
        }}
      >
        <span>{current?.label ?? '—'}</span>
        <ChevronDown aria-hidden="true" />
      </button>
      <Popover open={open} anchor={trigger} onClose={() => setOpen(false)} role="listbox" label={label} side={side} className="select-menu" minWidth="anchor">
        {options.map((option) => (
          <button
            key={String(option.value)}
            type="button"
            role="option"
            aria-selected={option.value === value}
            disabled={option.disabled}
            className="menu-item"
            onClick={() => {
              setOpen(false);
              if (option.value !== value) onChange(option.value);
            }}
          >
            <span>{option.label}</span>
          </button>
        ))}
      </Popover>
    </>
  );
}

import { ChevronDown } from 'lucide-react';
import { useRef, useState, type CSSProperties } from 'react';
import { popupMenu } from '../../lib/bridge';
import { nativeTiles } from '../../lib/glass';
import { Popover } from './Popover';

export interface SelectOption<T extends string | number> {
  value: T;
  label: string;
  disabled?: boolean;
  /** вид пункта, например образец шрифта */
  style?: CSSProperties;
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
  const native = useRef(false);
  if (hidden) return null;
  const current = options.find((option) => option.value === value);
  const toggle = () => {
    const button = trigger.current;
    // у поля ввода на стекле список рисует сам macOS: плитка поля лежит над страницей и закрыла бы веб-меню
    if (!open && button?.closest('#composer') && nativeTiles() && window.koddiWindowBridge?.popupMenu) {
      const rect = button.getBoundingClientRect();
      native.current = true;
      setOpen(true);
      void popupMenu({ x: rect.left, y: rect.top }, options.map((option) => ({ value: String(option.value), label: option.label, disabled: option.disabled })), String(value)).then((picked) => {
        setOpen(false);
        const option = options.find((item) => String(item.value) === picked);
        if (option && option.value !== value && !option.disabled) onChange(option.value);
      });
      return;
    }
    native.current = false;
    setOpen((state) => !state);
  };
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
        onClick={toggle}
        onKeyDown={(event) => {
          // удержанный Enter из поля ввода долетает сюда, когда открывается диалог, и щёлкал бы список туда-сюда
          if (event.key === 'Enter' && event.repeat) {
            event.preventDefault();
            return;
          }
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            if (!open) toggle();
          }
        }}
      >
        <span style={current?.style}>{current?.label ?? '—'}</span>
        <ChevronDown aria-hidden="true" />
      </button>
      <Popover open={open && !native.current} anchor={trigger} onClose={() => setOpen(false)} role="listbox" label={label} side={side} className="select-menu" minWidth="anchor">
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
            <span style={option.style}>{option.label}</span>
          </button>
        ))}
      </Popover>
    </>
  );
}

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { dialogs, useDialogRequest, useToasts } from '../../state/overlay';
import { Dialog } from './Dialog';
import { Select } from './Select';

/** Окно ввода и подтверждения, которое открывают inputDialog() и confirmDialog(). */
export function DialogHost() {
  const request = useDialogRequest();
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const form = useRef<HTMLFormElement>(null);
  const last = useRef(request);
  if (request) last.current = request;
  const shown = request ?? last.current;

  useEffect(() => {
    if (!request) {
      setOpen(false);
      return;
    }
    setValues(Object.fromEntries(request.fields.map((field) => [field.name, String(field.value ?? '')])));
    setError('');
    setOpen(true);
    const timer = window.setTimeout(() => form.current?.querySelector<HTMLElement>('input,textarea,button.select-trigger')?.focus(), 60);
    return () => window.clearTimeout(timer);
  }, [request]);

  const finish = (result: Record<string, string> | null) => {
    request?.resolve(result);
    dialogs.set({ current: null });
  };

  const submit = () => {
    if (!request) return;
    // в окне выбора Enter выбирает рекомендованный, а не опасный вариант
    if (request.alternative && request.recommended === 'alternative') {
      finish({ __choice: 'alternative' });
      return;
    }
    const missing = request.fields.find((field) => field.required && !values[field.name]?.trim());
    if (missing) {
      setError(`Проверьте поле «${missing.label}».`);
      return;
    }
    finish(values);
  };

  return (
    <Dialog open={open} onClose={() => finish(null)} labelledBy="dialog-title">
      {shown && (
        <form ref={form} className="dialog-form" noValidate onSubmit={(event) => { event.preventDefault(); submit(); }}>
          <h2 id="dialog-title">{shown.title}</h2>
          {shown.text?.split('\n\n').map((paragraph, index) => <p key={index}>{paragraph}</p>)}
          {shown.fields.map((field) => (
            <label key={field.name}>
              {field.label}
              {field.type === 'select' ? (
                <Select
                  value={values[field.name] ?? ''}
                  label={field.label}
                  options={(field.options ?? []).map((option) => ({ value: String(option.value), label: option.label }))}
                  onChange={(value) => setValues((state) => ({ ...state, [field.name]: value }))}
                />
              ) : field.type === 'textarea' ? (
                <textarea className="field" name={field.name} value={values[field.name] ?? ''} placeholder={field.placeholder}
                  onChange={(event) => setValues((state) => ({ ...state, [field.name]: event.target.value }))} />
              ) : (
                <input className="field" name={field.name} value={values[field.name] ?? ''} placeholder={field.placeholder}
                  onChange={(event) => setValues((state) => ({ ...state, [field.name]: event.target.value }))} />
              )}
            </label>
          ))}
          {error && <p className="form-error" role="alert">{error}</p>}
          {shown.alternative ? (
            <div className="actions choice">
              {(shown.recommended === 'alternative' ? ['alternative', 'accept'] : ['accept', 'alternative']).map((choice, index) => (
                <button key={choice} type="button" className={`btn${index === 0 ? ' btn-primary' : ''}`}
                  onClick={() => finish(choice === 'alternative' ? { __choice: 'alternative' } : values)}>
                  {choice === 'alternative' ? shown.alternative : shown.accept}
                </button>
              ))}
              <button type="button" className="btn" onClick={() => finish(null)}>Отмена</button>
            </div>
          ) : (
            <div className="actions">
              <button type="button" className="btn" onClick={() => finish(null)}>Отмена</button>
              <button type="submit" className="btn btn-primary">{shown.accept}</button>
            </div>
          )}
        </form>
      )}
    </Dialog>
  );
}

export function ToastHost() {
  const items = useToasts();
  const stack = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const node = stack.current;
    if (!node || !items.length) return;
    const composer = document.getElementById('composer');
    const place = () => {
      const field = composer?.querySelector<HTMLElement>('.composer-field') ?? composer;
      const rect = field?.getBoundingClientRect();
      const visible = rect && rect.width > 0 && rect.height > 0;
      node.style.left = `${visible ? rect.left + rect.width / 2 : innerWidth / 2}px`;
      node.style.bottom = `${visible ? Math.max(16, innerHeight - rect.top + 12) : 24}px`;
      node.style.maxWidth = `${visible ? Math.min(520, rect.width) : Math.min(520, innerWidth - 32)}px`;
      node.style.maxHeight = `${visible ? Math.max(60, rect.top - 24) : innerHeight - 48}px`;
    };
    place();
    try { if (!node.matches(':popover-open')) node.showPopover(); } catch { /* обычный fixed в старом движке */ }
    const resize = new ResizeObserver(place);
    if (composer) resize.observe(composer);
    const main = document.getElementById('main');
    if (main) resize.observe(main);
    window.addEventListener('resize', place);
    return () => { resize.disconnect(); window.removeEventListener('resize', place); };
  }, [items]);
  if (!items.length) return null;
  return (
    <div ref={stack} className="toast-stack" popover="manual" aria-label="Уведомления">
      {items.map((item) => (
        <div key={item.id} className={`toast${item.error ? ' error' : ''}`} role={item.error ? 'alert' : 'status'} data-state={item.closing ? 'closed' : 'open'}>{item.text}</div>
      ))}
    </div>
  );
}

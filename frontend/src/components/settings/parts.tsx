import { useEffect, useState, type ReactNode } from 'react';
import { api } from '../../lib/api';
import { Select } from '../ui/Select';

export type Say = (text: string) => void;

/** Заголовок группы настроек и сама группа строк. */
export function Section({ title, hint, children }: { title?: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <section className="pref-section">
      {title && <h3>{title}</h3>}
      {hint && <p className="pref-hint">{hint}</p>}
      <div className="pref-group">{children}</div>
    </section>
  );
}

/** Строка настройки: название и пояснение слева, элемент управления справа. */
export function Row({ title, detail, children, stack }: { title: string; detail?: ReactNode; children?: ReactNode; stack?: boolean }) {
  return (
    <div className={`pref-row${stack ? ' stack' : ''}`} data-setting={title}>
      <div className="pref-label">
        <strong>{title}</strong>
        {detail && <span>{detail}</span>}
      </div>
      {children !== undefined && <div className="pref-control">{children}</div>}
    </div>
  );
}

/** Сохранение с откатом: значение меняется сразу, при ошибке возвращается прежнее. */
function useSaved<T>(value: T, save: (value: T) => Promise<unknown>, say: Say) {
  const [current, setCurrent] = useState(value);
  const [busy, setBusy] = useState(false);
  useEffect(() => setCurrent(value), [value]);
  const commit = (next: T) => {
    const previous = current;
    setCurrent(next);
    setBusy(true);
    save(next)
      .then(() => say('Сохранено'))
      .catch((error: Error) => { setCurrent(previous); say(error.message); })
      .finally(() => setBusy(false));
  };
  return { current, busy, commit };
}

export function Pick<T extends string | number>({ title, detail, options, value, save, say }: {
  title: string; detail?: ReactNode; options: Array<[T, string]>; value: T; save: (value: T) => Promise<unknown>; say: Say;
}) {
  const { current, busy, commit } = useSaved(value, save, say);
  return (
    <Row title={title} detail={detail}>
      <Select label={title} value={current} disabled={busy} options={options.map(([id, label]) => ({ value: id, label }))} onChange={commit} />
    </Row>
  );
}

/** Небольшой набор вариантов кнопками (оформление, ширина чата). */
export function Segmented<T extends string>({ title, detail, options, value, save, say }: {
  title: string; detail?: ReactNode; options: Array<[T, string]>; value: T; save: (value: T) => Promise<unknown>; say: Say;
}) {
  const { current, busy, commit } = useSaved(value, save, say);
  return (
    <Row title={title} detail={detail}>
      <div className="pref-segment" role="group" aria-label={title}>
        {options.map(([id, label]) => (
          <button key={id} type="button" aria-pressed={current === id} disabled={busy} onClick={() => { if (id !== current) commit(id); }}>{label}</button>
        ))}
      </div>
    </Row>
  );
}

export function Toggle({ title, detail, checked, save, say, disabled }: {
  title: string; detail?: ReactNode; checked: boolean; save: (value: boolean) => Promise<unknown>; say: Say; disabled?: boolean;
}) {
  const { current, busy, commit } = useSaved(checked, save, say);
  return (
    <Row title={title} detail={detail}>
      <button type="button" role="switch" className="pref-switch" aria-label={title} aria-checked={current} disabled={busy || disabled} onClick={() => commit(!current)} />
    </Row>
  );
}

/** Короткое текстовое значение: сохраняется по Enter и при уходе из поля. */
export function TextRow({ title, detail, value, placeholder, save, say, maxLength = 80, width }: {
  title: string; detail?: ReactNode; value: string; placeholder?: string; save: (value: string) => Promise<unknown>; say: Say; maxLength?: number; width?: number;
}) {
  const { current, busy, commit } = useSaved(value, save, say);
  const [draft, setDraft] = useState(current);
  useEffect(() => setDraft(current), [current]);
  const finish = () => {
    const next = draft.trim();
    if (next && next !== current) commit(next);
    else setDraft(current);
  };
  return (
    <Row title={title} detail={detail}>
      <input
        className="pref-input"
        style={width ? { width } : undefined}
        value={draft}
        placeholder={placeholder}
        maxLength={maxLength}
        disabled={busy}
        aria-label={title}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={finish}
        onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); (event.target as HTMLInputElement).blur(); } }}
      />
    </Row>
  );
}

/** Длинный текст (личные инструкции): сохраняется кнопкой, видно несохранённые изменения и счётчик. */
export function Block({ title, detail, value, placeholder, save, say, max, rows = 9 }: {
  title: string; detail?: ReactNode; value: string; placeholder?: string; save: (value: string) => Promise<unknown>; say: Say; max: number; rows?: number;
}) {
  const { current, busy, commit } = useSaved(value, save, say);
  const [draft, setDraft] = useState(current);
  useEffect(() => setDraft(current), [current]);
  const dirty = draft.trim() !== current.trim();
  return (
    <Row title={title} detail={detail} stack>
      <textarea
        className="pref-textarea"
        rows={rows}
        value={draft}
        placeholder={placeholder}
        aria-label={title}
        maxLength={max}
        disabled={busy}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && dirty) { event.preventDefault(); commit(draft.trim()); } }}
      />
      <div className="pref-block-bar">
        <span className="pref-count" data-over={draft.length > max * 0.95 ? true : undefined}>{draft.length} / {max}</span>
        <span className="pref-block-actions">
          <button type="button" className="secondary" disabled={!dirty || busy} onClick={() => setDraft(current)}>Отменить</button>
          <button type="button" className="primary" disabled={!dirty || busy} onClick={() => commit(draft.trim())}>Сохранить</button>
        </span>
      </div>
    </Row>
  );
}

export function Action({ title, detail, label, onClick, danger, disabled, icon }: {
  title: string; detail?: ReactNode; label: string; onClick: () => void; danger?: boolean; disabled?: boolean; icon?: ReactNode;
}) {
  return (
    <Row title={title} detail={detail}>
      <button type="button" className={`pref-button${danger ? ' danger' : ''}`} disabled={disabled} onClick={onClick}>{icon}<span>{label}</span></button>
    </Row>
  );
}

export function Meter({ percent, low }: { percent: number | null; low?: boolean }) {
  const value = percent == null ? 0 : Math.max(0, Math.min(100, percent));
  return <i className="pref-meter" data-low={low ? true : undefined} aria-hidden="true"><b style={{ width: `${value}%` }} /></i>;
}

/** Данные с сервера: загрузка, ошибка с повтором, готовый результат. */
export function Remote<T>({ path, children, retryKey }: { path: string; retryKey: string; children: (value: T) => ReactNode }) {
  const [state, setState] = useState<{ state: 'loading' } | { state: 'ready'; value: T } | { state: 'failed'; error: string }>({ state: 'loading' });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setState({ state: 'loading' });
    api<T>(path, { signal: controller.signal })
      .then((value) => setState({ state: 'ready', value }))
      .catch((error: Error) => { if (error.name !== 'AbortError') setState({ state: 'failed', error: error.message }); });
    return () => controller.abort();
  }, [path, attempt, retryKey]);
  if (state.state === 'loading') return <div className="settings-loading skeleton" aria-label="Загрузка настроек" />;
  if (state.state === 'failed') {
    return (
      <Section>
        <Row title="Не удалось загрузить настройки" detail={state.error}>
          <button type="button" className="pref-button" onClick={() => setAttempt((value) => value + 1)}>Повторить</button>
        </Row>
      </Section>
    );
  }
  return <>{children(state.value)}</>;
}

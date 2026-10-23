import { FolderOpen, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';

export type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'failed'; error: string };

/** Данные страницы: загрузка с отменой при уходе, повтором при ошибке и тихим обновлением (без мигания скелетона). */
export function usePage<T>(load: (signal: AbortSignal) => Promise<T>, key: string, refreshMs = 0): [Load<T>, () => void] {
  const [result, setResult] = useState<Load<T>>({ state: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const loader = useRef(load);
  loader.current = load;
  useEffect(() => {
    const controller = new AbortController();
    setResult((previous) => (previous.state === 'ready' && attempt > 0 ? previous : { state: 'loading' }));
    loader.current(controller.signal)
      .then((value) => { if (!controller.signal.aborted) setResult({ state: 'ready', value }); })
      .catch((error: Error) => { if (!controller.signal.aborted && error.name !== 'AbortError') setResult((previous) => (previous.state === 'ready' ? previous : { state: 'failed', error: error.message })); });
    return () => controller.abort();
  }, [key, attempt]);
  useEffect(() => {
    if (!refreshMs) return undefined;
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') setAttempt((value) => value + 1); }, refreshMs);
    return () => window.clearInterval(timer);
  }, [refreshMs]);
  return [result, useCallback(() => setAttempt((value) => value + 1), [])];
}

export function Empty({ title, detail, icon, children }: { title: string; detail?: string; icon?: ReactNode; children?: ReactNode }) {
  return (
    <div className="empty-list">
      {icon ?? <FolderOpen size={24} aria-hidden="true" />}
      <h3>{title}</h3>
      {detail && <p>{detail}</p>}
      {children}
    </div>
  );
}

export function PageRoot({ title, subtitle, commands, children }: { title: string; subtitle?: string; commands?: ReactNode; children: ReactNode }) {
  return (
    <section className="page-content">
      <div className="page-heading">
        <div>
          <h1>{title}</h1>
          {subtitle && <p className="muted">{subtitle}</p>}
        </div>
        <div className="actions">{commands}</div>
      </div>
      <div className="page-body">{children}</div>
    </section>
  );
}

export function Frame<T>({ title, load, retry, children }: { title: string; load: Load<T>; retry: () => void; children: (value: T) => ReactNode }) {
  if (load.state === 'loading') {
    return (
      <div className="chat-skeleton" role="status" aria-label="Загрузка">
        <span className="sk sk-line medium" />
        <span className="sk-block"><span className="sk sk-line" /><span className="sk sk-line" /><span className="sk sk-line short" /></span>
        <span className="sk-block"><span className="sk sk-line" /><span className="sk sk-line medium" /></span>
      </div>
    );
  }
  if (load.state === 'failed') {
    return (
      <PageRoot title={title}>
        <div className="error-state">
          <h3>Не удалось загрузить</h3>
          <p>{load.error}</p>
          <button type="button" className="secondary" onClick={retry}><RefreshCw aria-hidden="true" /><span>Повторить</span></button>
        </div>
      </PageRoot>
    );
  }
  return <>{children(load.value)}</>;
}

/** Окно с формой для страниц: заголовок, поля и кнопки. */
export function Hint({ children }: { children: ReactNode }) {
  return <p className="page-hint">{children}</p>;
}

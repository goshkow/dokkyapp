import { ChevronRight, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api } from '../../lib/api';
import { numberText } from '../../lib/format';
import { useChat } from '../../state/chatCore';
import { useView } from '../../state/view';

interface Row { key: string; name: string; tokens: number; items: { name: string; tokens: number }[] }
interface Breakdown { window: number; used: number; compact_at: number | null; exact: number | null; rows: Row[] }

const short = (value: number): string => (value >= 1000 ? `${(value / 1000).toFixed(1).replace(/\.0$/, '')}k` : String(value));
const share = (value: number, total: number): string => {
  const percent = (value / total) * 100;
  return `${percent >= 10 ? Math.round(percent) : percent.toFixed(1)}%`;
};

/** Куда уходит окно контекста: разбивка по категориям (оценка по длине текста). */
export function ContextBreakdown({ active }: { active: boolean }) {
  const chatId = useView((s) => s.chat?.id);
  const running = useChat((s) => s.running);
  const [value, setValue] = useState<Breakdown | null>(null);
  const [failed, setFailed] = useState(false);
  const [stamp, setStamp] = useState('');
  const [open, setOpen] = useState<string>('');

  const load = useCallback(async () => {
    if (!chatId) { setValue(null); return; }
    try {
      const next = await api<Breakdown>(`/api/chats/${chatId}/context-breakdown`);
      if (!Array.isArray(next?.rows)) throw new Error('bad breakdown');
      setValue(next);
      setFailed(false);
      setStamp(new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
    } catch {
      setFailed(true);
    }
  }, [chatId]);

  // обновление при открытии, смене чата, конце ответа и раз в несколько секунд, пока агент работает
  useEffect(() => { if (active) void load(); }, [active, load, running]);
  useEffect(() => {
    if (!active || !running) return undefined;
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void load(); }, 6000);
    return () => window.clearInterval(timer);
  }, [active, running, load]);

  if (!chatId) return <p className="muted">Откройте чат, чтобы увидеть, куда уходит контекст.</p>;
  if (!value) return <p className="muted">{failed ? 'Не удалось получить разбивку контекста.' : 'Считаю…'}</p>;
  const total = value.window;
  return (
    <div className="context-breakdown">
      <div className="context-total">
        <span>Использовано: <b>{Math.round((value.used / total) * 100)}%</b></span>
        <small>≈ {short(value.used)} / {short(total)}</small>
      </div>
      <div className="context-bar" role="img" aria-label={`Использовано ${numberText(value.used)} из ${numberText(total)} токенов`}>
        {value.rows.map((row) => row.tokens > 0 && <i key={row.key} data-kind={row.key} style={{ width: `${(row.tokens / total) * 100}%` }} title={row.name} />)}
      </div>
      <ul className="context-rows">
        {value.rows.map((row) => {
          const expandable = row.items.length > 0;
          const body = (
            <>
              <i className="context-dot" data-kind={row.key} aria-hidden="true" />
              <span className="context-name">{row.name}</span>
              <span className="context-tokens">{numberText(row.tokens)}</span>
              <small>{share(row.tokens, total)}</small>
              {expandable && <ChevronRight className="context-chevron" size={13} aria-hidden="true" />}
            </>
          );
          return (
            <li key={row.key} data-open={open === row.key || undefined}>
              {expandable ? (
                <button type="button" className="context-row" aria-expanded={open === row.key} onClick={() => setOpen(open === row.key ? '' : row.key)}>{body}</button>
              ) : <div className="context-row">{body}</div>}
              {expandable && open === row.key && (
                <ul className="context-items">
                  {row.items.map((item) => (
                    <li key={item.name}><span title={item.name}>{item.name}</span><span>{numberText(item.tokens)}</span></li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
      <p className="muted context-note">
        Оценка по длине текста{value.exact ? `; по данным последнего ответа модели: ${numberText(value.exact)}` : ''}.
        {value.compact_at ? ` Историю Докки сожмёт сам при ${numberText(value.compact_at)} токенов.` : ''}
      </p>
      <div className="context-foot">
        <small className="muted">{stamp && `Обновлено в ${stamp}`}</small>
        <button type="button" className="icon-button" title="Обновить" aria-label="Обновить разбивку контекста" onClick={() => void load()}><RefreshCw aria-hidden="true" /></button>
      </div>
    </div>
  );
}

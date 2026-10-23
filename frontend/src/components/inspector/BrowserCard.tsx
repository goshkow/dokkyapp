import { ArrowLeft, ArrowRight, Globe, RotateCw, X } from 'lucide-react';
import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type WheelEvent } from 'react';
import { closeBrowser, pickBrowser, sendBrowserInput, useBrowser, watchBrowser } from '../../state/browser';

const SPECIAL_KEYS = new Set(['Enter', 'Backspace', 'Delete', 'Tab', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown']);

/** Живое окно страницы, которую открыл агент: те же клики и ввод, что и у агента, плюс адресная строка. */
export function BrowserView({ active }: { active: boolean }) {
  const sessions = useBrowser((s) => s.sessions);
  const current = useBrowser((s) => s.active);
  const frame = useBrowser((s) => s.frame);
  const error = useBrowser((s) => s.error);
  const [address, setAddress] = useState('');
  const editing = useRef(false);

  useEffect(() => (active ? watchBrowser() : undefined), [active]);
  useEffect(() => { if (frame && !editing.current) setAddress(frame.url); }, [frame?.url]);

  if (!sessions.length) {
    return (
      <div className="empty-list browser-empty">
        <Globe size={24} aria-hidden="true" />
        <h3>Страница не открыта</h3>
        <p>Когда агент откроет сайт или локальный сервер, он появится здесь.</p>
      </div>
    );
  }

  const place = (event: MouseEvent<HTMLImageElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    return { x: (event.clientX - box.left) / box.width, y: (event.clientY - box.top) / box.height };
  };
  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key.length === 1) void sendBrowserInput({ kind: 'type', text: event.key });
    else if (SPECIAL_KEYS.has(event.key)) void sendBrowserInput({ kind: 'key', key: event.key });
    else return;
    event.preventDefault();
  };
  const onWheel = (event: WheelEvent<HTMLImageElement>) => {
    void sendBrowserInput({ kind: 'wheel', ...place(event), dx: event.deltaX, dy: event.deltaY });
  };

  return (
    <div className="browser-view">
      {sessions.length > 1 && (
        <div className="browser-tabs" role="tablist" aria-label="Открытые страницы">
          {sessions.map((item) => (
            <button key={item.session_id} type="button" role="tab" aria-selected={item.session_id === current} className="browser-tab" title={item.url} onClick={() => pickBrowser(item.session_id)}>
              <span>{item.title || item.url}</span>
            </button>
          ))}
        </div>
      )}
      <form
        className="browser-bar"
        onSubmit={(event) => {
          event.preventDefault();
          editing.current = false;
          if (address.trim()) void sendBrowserInput({ kind: 'navigate', url: address.trim() });
        }}
      >
        <button type="button" className="icon-button" title="Назад" aria-label="Назад" onClick={() => void sendBrowserInput({ kind: 'back' })}><ArrowLeft aria-hidden="true" /></button>
        <button type="button" className="icon-button" title="Вперёд" aria-label="Вперёд" onClick={() => void sendBrowserInput({ kind: 'forward' })}><ArrowRight aria-hidden="true" /></button>
        <button type="button" className="icon-button" title="Обновить" aria-label="Обновить" onClick={() => void sendBrowserInput({ kind: 'reload' })}><RotateCw aria-hidden="true" /></button>
        <input
          className="browser-address"
          aria-label="Адрес страницы"
          spellCheck={false}
          value={address}
          onFocus={() => { editing.current = true; }}
          onBlur={() => { editing.current = false; }}
          onChange={(event) => setAddress(event.target.value)}
        />
        {current !== null && <button type="button" className="icon-button" title="Закрыть страницу" aria-label="Закрыть страницу" onClick={() => void closeBrowser(current)}><X aria-hidden="true" /></button>}
      </form>
      <div className="browser-stage" tabIndex={0} role="application" aria-label="Страница в браузере агента" onKeyDown={onKey}>
        {frame ? (
          <img
            className="browser-frame"
            src={frame.src}
            alt={frame.title || frame.url}
            draggable={false}
            style={{ aspectRatio: `${frame.width} / ${frame.height}` }}
            onClick={(event) => { event.currentTarget.parentElement?.focus(); void sendBrowserInput({ kind: 'click', ...place(event) }); }}
            onDoubleClick={(event) => void sendBrowserInput({ kind: 'dblclick', ...place(event) })}
            onWheel={onWheel}
          />
        ) : (
          <div className="skeleton browser-skeleton" />
        )}
      </div>
      {error && <p className="muted browser-error">{error}</p>}
    </div>
  );
}

import { ArrowLeft, ArrowRight, ExternalLink, Globe, RotateCw, X } from 'lucide-react';
import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type WheelEvent } from 'react';
import { openExternal } from '../../lib/external';
import { closeBrowser, fitBrowser, pickBrowser, sendBrowserInput, useBrowser, watchBrowser } from '../../state/browser';

const SPECIAL_KEYS = new Set(['Enter', 'Backspace', 'Delete', 'Tab', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown']);
const BUTTONS = ['left', 'middle', 'right'] as const;
// имя клавиши для Playwright: буквы, цифры и именованные клавиши; остальное (знаки) идёт текстом
const KEY_NAME = /^(?:[A-Za-z0-9]|F\d{1,2}|Enter|Backspace|Delete|Tab|Escape|Arrow(?:Up|Down|Left|Right)|Home|End|PageUp|PageDown)$/;

/** Живое окно страницы, которую открыл агент или пользователь: размер страницы повторяет панель, мышь и клавиатура идут в страницу. */
export function BrowserView({ active }: { active: boolean }) {
  const sessions = useBrowser((s) => s.sessions);
  const current = useBrowser((s) => s.active);
  const frame = useBrowser((s) => s.frame);
  const error = useBrowser((s) => s.error);
  const [address, setAddress] = useState('');
  const editing = useRef(false);
  const stage = useRef<HTMLDivElement>(null);
  const lastMove = useRef(0);
  const wheel = useRef({ dx: 0, dy: 0, x: 0.5, y: 0.5, timer: 0 });

  useEffect(() => (active ? watchBrowser() : undefined), [active]);
  useEffect(() => { if (frame && !editing.current) setAddress(frame.url); }, [frame?.url]);

  // страница подстраивается под размер окна в панели, как обычный браузер
  useEffect(() => {
    const node = stage.current;
    if (!active || !node || current === null) return;
    let timer = 0;
    const fit = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        const { width, height } = node.getBoundingClientRect();
        if (width >= 200 && height >= 200) fitBrowser(width, height);
      }, 200);
    };
    const observer = new ResizeObserver(fit);
    observer.observe(node);
    fit();
    return () => { observer.disconnect(); window.clearTimeout(timer); };
  }, [active, current, sessions.length > 0]);

  if (!sessions.length) {
    return (
      <div className="empty-list browser-empty">
        <Globe size={24} aria-hidden="true" />
        <h3>Страница не открыта</h3>
        <p>Когда агент откроет сайт или вы откроете ссылку из чата, страница появится здесь.</p>
      </div>
    );
  }

  const place = (event: MouseEvent<HTMLElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    return { x: (event.clientX - box.left) / box.width, y: (event.clientY - box.top) / box.height };
  };
  const button = (event: MouseEvent<HTMLElement>) => BUTTONS[event.button] ?? 'left';
  const onKey = async (event: KeyboardEvent<HTMLDivElement>) => {
    const command = event.metaKey || event.ctrlKey;
    if (command && event.key.toLowerCase() === 'v') {
      event.preventDefault();
      try {
        const text = await navigator.clipboard.readText();
        if (text) void sendBrowserInput({ kind: 'type', text: text.slice(0, 2000) });
      } catch {
        /* буфер недоступен */
      }
      return;
    }
    if (!command && !event.altKey && event.key.length === 1) {
      event.preventDefault();
      void sendBrowserInput({ kind: 'type', text: event.key });
      return;
    }
    if (!KEY_NAME.test(event.key) || (!command && !event.altKey && !SPECIAL_KEYS.has(event.key))) return;
    event.preventDefault();
    const mods = [event.ctrlKey && 'Control', event.altKey && 'Alt', event.metaKey && 'Meta', event.shiftKey && event.key.length > 1 && 'Shift'].filter(Boolean);
    void sendBrowserInput({ kind: 'key', key: [...mods, event.key].join('+') });
  };
  const onMove = (event: MouseEvent<HTMLImageElement>) => {
    // наведение не чаще 20 раз в секунду: каждый шаг - запрос к странице
    const now = performance.now();
    if (now - lastMove.current < 50) return;
    lastMove.current = now;
    void sendBrowserInput({ kind: 'move', ...place(event) }, { quiet: true });
  };
  const onWheel = (event: WheelEvent<HTMLImageElement>) => {
    // прокрутка трекпадом - десятки событий в секунду: копим и отправляем пачкой
    const state = wheel.current;
    Object.assign(state, place(event), { dx: state.dx + event.deltaX, dy: state.dy + event.deltaY });
    if (state.timer) return;
    state.timer = window.setTimeout(() => {
      const { dx, dy, x, y } = state;
      Object.assign(state, { dx: 0, dy: 0, timer: 0 });
      void sendBrowserInput({ kind: 'wheel', x, y, dx, dy }, { quiet: true });
    }, 40);
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
        {/^https?:\/\//i.test(frame?.url ?? '') && !/^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])[:/]/i.test(frame?.url ?? '') && (
          <button type="button" className="icon-button" title="Открыть в браузере по умолчанию" aria-label="Открыть в браузере по умолчанию" onClick={() => openExternal(frame!.url)}><ExternalLink aria-hidden="true" /></button>
        )}
        {current !== null && <button type="button" className="icon-button" title="Закрыть страницу" aria-label="Закрыть страницу" onClick={() => void closeBrowser(current)}><X aria-hidden="true" /></button>}
      </form>
      <div ref={stage} className="browser-stage" tabIndex={0} role="application" aria-label="Страница в браузере" onKeyDown={(event) => void onKey(event)}>
        {frame ? (
          <img
            className="browser-frame"
            src={frame.src}
            alt={frame.title || frame.url}
            draggable={false}
            onMouseDown={(event) => {
              event.currentTarget.parentElement?.focus();
              void sendBrowserInput({ kind: 'down', button: button(event), count: Math.min(event.detail || 1, 3), ...place(event) });
            }}
            onMouseUp={(event) => void sendBrowserInput({ kind: 'up', button: button(event), count: Math.min(event.detail || 1, 3), ...place(event) })}
            onMouseMove={onMove}
            onContextMenu={(event) => event.preventDefault()}
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

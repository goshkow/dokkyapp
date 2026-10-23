import { Clock, Search, Square, X } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { attachCitations } from '../../lib/citations';
import { safeSource, sourceDomain, sourceLetter, type CloudSource } from '../../lib/cloud';
import { MARK } from '../../lib/assets';
import { renderMarkdown } from '../../lib/markdown';
import { cancelStream, reconcileStreamNodes, renderStream } from '../../lib/streamMarkdown';
import { switchScope } from '../../state/actions';
import {
  ANSWER_STREAM, continueInChat, currentAnswer, loadHistory, openStoredSearch, resetSearch, runSearch, search, stopSearch, useSearch, type SearchSource,
} from '../../state/search';
import { guarded } from '../../state/overlay';
import { onStream } from '../../state/stream';
import { openSources } from '../../state/viewers';
import { CloudSources } from '../chat/CloudExtras';

function ResultCard({ source, index }: { source: SearchSource; index: number }) {
  const safe = safeSource(source as CloudSource);
  if (!safe) return null;
  let path = '';
  try {
    path = decodeURIComponent(new URL(source.url).pathname)
      .split('/')
      .filter((part) => part && !/^[a-f0-9]{16,}$/i.test(part))
      .slice(0, 3)
      .map((part) => (part.length > 60 ? `${part.slice(0, 57)}…` : part))
      .join(' › ');
  } catch {
    /* адрес без пути */
  }
  const open = (event: React.MouseEvent) => {
    if (!window.koddiDesktop) return;
    event.preventDefault();
    window.location.href = `koddi://open-url?url=${encodeURIComponent(safe.href)}`;
  };
  return (
    <article className="ai-search-result" style={{ animationDelay: `${Math.min(index, 9) * 35}ms` }} onAnimationEnd={(event) => { event.currentTarget.style.animation = 'none'; }}>
      <div className="ai-search-result-copy">
        <div className="ai-search-result-meta">
          <span className="ai-source-icon" aria-hidden="true">{sourceLetter(source as CloudSource)}</span>
          <span>{sourceDomain(source as CloudSource)}</span>
        </div>
        <a className="ai-search-result-title" href={safe.href} target="_blank" rel="noopener noreferrer" onClick={open}>{source.title || safe.domain}</a>
        <span className="ai-search-result-url">{path}</span>
        {source.snippet && <p>{source.snippet.replace(/\s+/g, ' ').trim()}</p>}
      </div>
    </article>
  );
}

export function CloudSearch() {
  const state = useSearch((s) => s);
  const input = useRef<HTMLTextAreaElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const [text, setText] = useState(state.query);
  const [focused, setFocused] = useState(false);
  const [show, setShow] = useState(false);
  // режим правки: поле уезжает в центр, выдача прячется (как в оригинальном Докки)
  const [editing, setEditing] = useState(false);
  const [relocating, setRelocating] = useState(false);
  const start = useRef<HTMLDivElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const motion = useRef<Animation | null>(null);
  const layout = useRef(0);
  const blurTimer = useRef(0);

  // меняет раскладку и плавно ведёт поле из прежнего места в новое
  const relocate = (change: () => void, options: { hideOutput?: boolean; animate?: boolean; delay?: number } = {}) => {
    const { hideOutput = false, animate = true, delay = 0 } = options;
    const token = ++layout.current;
    const node = start.current;
    const before = form.current?.getBoundingClientRect();
    motion.current?.cancel();
    flushSync(() => {
      setRelocating(hideOutput);
      change();
    });
    const after = form.current?.getBoundingClientRect();
    const reduced = !animate || window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const done = () => {
      if (token !== layout.current) return;
      setRelocating(false);
      motion.current?.cancel();
      motion.current = null;
    };
    if (reduced || !node || !before || !after || typeof node.animate !== 'function') return done();
    motion.current = node.animate(
      [{ translate: `${before.left - after.left}px ${before.top - after.top}px` }, { translate: '0px 0px' }],
      { duration: 300, delay, easing: 'cubic-bezier(.22,1,.36,1)', fill: 'both' },
    );
    motion.current.finished.then(done, () => undefined);
  };
  const editRef = useRef(false);
  const beginEdit = () => {
    window.clearTimeout(blurTimer.current);
    if (editRef.current) return;
    editRef.current = true;
    relocate(() => setEditing(true), { delay: search.get().hasQuery ? 110 : 0 });
  };
  const endEdit = () => {
    if (!editRef.current) return;
    editRef.current = false;
    setShow(false);
    const query = search.get().hasQuery;
    relocate(() => {
      setEditing(false);
      if (query) setText(search.get().query);
    }, { hideOutput: query, animate: query });
  };
  useEffect(() => () => { window.clearTimeout(blurTimer.current); motion.current?.cancel(); }, []);
  // клик мимо поля и Escape закрывают правку
  useEffect(() => {
    const away = (event: Event) => {
      if (editRef.current && !start.current?.contains(event.target as Node)) { input.current?.blur(); endEdit(); }
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented && editRef.current) { input.current?.blur(); endEdit(); }
    };
    document.addEventListener('pointerdown', away);
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('pointerdown', away); document.removeEventListener('keydown', key); };
  }, []);

  useEffect(() => { setText(state.query); }, [state.query]);
  useLayoutEffect(() => {
    const node = input.current;
    if (!node) return;
    node.style.height = 'auto';
    node.style.height = `${Math.min(150, node.scrollHeight)}px`;
  }, [text]);

  // ответ печатается прямо в узел: порции не проходят через React
  useEffect(() => {
    const node = body.current;
    if (!node) return undefined;
    const apply = (value: string) => {
      if (!value) {
        cancelStream(node);
        node.replaceChildren();
        return;
      }
      if (!search.get().final) renderStream(node, value);
    };
    apply(currentAnswer());
    return onStream(ANSWER_STREAM, apply);
  }, []);

  // готовый ответ заменяет печатаемый в том же узле и получает чипы источников
  const message = state.final;
  useEffect(() => {
    const node = body.current;
    if (!node || !message) return;
    cancelStream(node);
    const draft = document.createElement('div');
    renderMarkdown(draft, message.content, true, { message });
    if (node.firstChild) reconcileStreamNodes(node, draft);
    else node.replaceChildren(...draft.childNodes);
    attachCitations(node, message, { open: openSources });
  }, [message]);

  const history = state.history.filter((chat) => chat.title && (!text.trim() || chat.title.toLocaleLowerCase('ru-RU').includes(text.trim().toLocaleLowerCase('ru-RU')))).slice(0, 6);
  const open = show && focused && history.length > 0;
  const canStop = state.busy && !editing;
  const canClear = !state.busy && !editing && state.hasQuery;
  const ready = Boolean(text.trim()) && !canStop && !canClear;
  const submit = () => {
    setShow(false);
    if (canStop) return void stopSearch();
    if (canClear) {
      input.current?.blur();
      return relocate(() => { resetSearch(); setText(''); }, { animate: false });
    }
    const query = text.trim();
    if (!query) return;
    if (state.busy && query === state.query) { endEdit(); input.current?.blur(); return; }
    editRef.current = false;
    relocate(() => { setEditing(false); void guarded(() => runSearch(text))(); }, { hideOutput: true });
    input.current?.blur();
  };
  // правка запроса поверх готового поиска сбрасывает выдачу
  const change = (value: string) => {
    setText(value);
    setShow(true);
    const current = search.get();
    if (current.hasQuery && value !== current.query) {
      if (current.busy) void stopSearch();
      resetSearch();
    }
  };
  return (
    <section
      id="ai-search-surface"
      className={`ai-search-surface${state.hasQuery ? ' has-query' : ''}${state.hasAnswer ? ' has-ai-answer' : ''}${editing ? ' is-editing' : ''}${relocating ? ' is-relocating' : ''}`}
      role="tabpanel"
      aria-labelledby="product-search"
    >
      <div className="ai-search-start" ref={start}>
        <div className="ai-search-intro" hidden={state.hasQuery && !editing}>
          <img className="ai-search-logo" src={MARK} alt="Докки" />
          <h1>Что хотите найти?</h1>
          <p>Проверим источники и найдём ответ.</p>
        </div>
        <form ref={form} className={`composer ai-search-form${open ? ' suggestions-open' : ''}`} data-glass="search-field" role="search" onSubmit={(event) => { event.preventDefault(); submit(); }}>
          <div className="ai-search-line">
            <textarea
              id="ai-search-query"
              ref={input}
              rows={1}
              maxLength={400}
              value={text}
              placeholder="Что вас интересует?"
              aria-label="Поисковый запрос"
              role="combobox"
              aria-autocomplete="list"
              aria-expanded={open}
              aria-controls="ai-search-suggestions"
              onChange={(event) => change(event.target.value)}
              onFocus={() => { setFocused(true); setShow(true); void loadHistory(); beginEdit(); }}
              onBlur={() => { window.setTimeout(() => setFocused(false), 120); blurTimer.current = window.setTimeout(() => { if (editRef.current && !form.current?.contains(document.activeElement)) endEdit(); }, 0); }}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  submit();
                }
                if (event.key === 'Escape' && open) setShow(false);
              }}
            />
            <button
              className={`send-button${ready ? ' ready' : ''}${canStop ? ' busy' : ''}${canClear ? ' clear-search' : ''}`}
              type={canClear ? 'button' : 'submit'}
              aria-label={canStop ? 'Остановить поиск' : canClear ? 'Очистить поиск' : 'Найти'}
              disabled={!canStop && !canClear && !text.trim()}
              onPointerDown={(event) => event.preventDefault()}
              onClick={canClear ? submit : undefined}
            >
              {canStop ? <Square aria-hidden="true" /> : canClear ? <X aria-hidden="true" /> : <Search aria-hidden="true" />}
              <span className="ai-search-submit-label">{canStop ? 'Стоп' : 'Найти'}</span>
            </button>
          </div>
          <div className="ai-suggestions-wrap" inert={!open}>
            <div className="ai-suggestions" id="ai-search-suggestions" role="listbox" aria-label="Недавние запросы">
              {history.map((chat) => (
                <div key={String(chat.id)} className="ai-suggestion-row" role="option" aria-selected="false">
                  <button type="button" className="ai-suggestion-pick" tabIndex={-1} onPointerDown={(event) => event.preventDefault()} onClick={guarded(async () => { setShow(false); await openStoredSearch(chat); })}>
                    <Clock aria-hidden="true" />
                    <span>{chat.title}</span>
                  </button>
                </div>
              ))}
            </div>
          </div>
        </form>
      </div>
      <div className="ai-search-output" hidden={!state.hasQuery} inert={editing}>
        <div className="ai-response-column">
          <div className="ai-response-pane">
            <div className="ai-response-heading"><img className="ai-response-logo" src={MARK} alt="" /><span>Докки</span></div>
            <div className="ai-search-status" role="status" aria-live="polite" hidden={!state.status}>
              <span className="ai-search-progress" aria-hidden="true" />
              <span className="ai-search-status-copy">{state.status}</span>
              <span className="ai-reading-sources" aria-hidden="true">
                {state.reading.map((source) => <span key={source.url} className="ai-source-icon">{sourceLetter(source as CloudSource)}</span>)}
              </span>
            </div>
            {state.error && <p className="ai-search-error">{state.error}</p>}
            <article className="ai-search-answer"><div className="message-body" ref={body} /></article>
            {message && <section className="ai-search-source-cards"><CloudSources citations={message.citations} /></section>}
          </div>
          {message && <button type="button" className="ai-search-followup" onClick={guarded(() => continueInChat(() => switchScope('chat')))}>Продолжить диалог</button>}
        </div>
        <div className="ai-results-pane">
          <div className="ai-results-heading" hidden={!state.sources.length}><h2>Источники и результаты</h2></div>
          <div className="ai-search-results">{state.sources.map((source, index) => <ResultCard key={source.url} source={source} index={index} />)}</div>
        </div>
      </div>
    </section>
  );
}

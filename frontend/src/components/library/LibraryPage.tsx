import { Download, FileText, ImageOff, Images, Star } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, cloudPath } from '../../lib/api';
import { cloudFileUrl, type CloudFile } from '../../lib/cloud';
import { serverTime } from '../../lib/format';
import { notify } from '../../state/overlay';
import { openArtifact, openImage } from '../../state/viewers';

type Tab = 'recent' | 'favorites' | 'images' | 'files';
const tabs: Array<[Tab, string]> = [['recent', 'Недавние'], ['favorites', 'Избранное'], ['images', 'Изображения'], ['files', 'Файлы']];
const empty: Record<Tab, string> = {
  recent: 'Здесь появятся изображения и файлы из ваших чатов.',
  favorites: 'Отмечайте звёздочкой то, что хочется сохранить: оно будет здесь.',
  images: 'Изображений пока нет.',
  files: 'Файлов пока нет.',
};

interface Item extends CloudFile {
  id: string;
  name: string;
  kind?: string;
  favorite?: boolean;
}

const extension = (name: string): string => (/\.([A-Za-z0-9]{1,5})$/.exec(name)?.[1] ?? 'файл').toUpperCase();
const day = (value?: string): string => {
  const moment = serverTime(value);
  return Number.isFinite(moment) ? new Date(moment).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' }) : '';
};

function Card({ item, busy, onFavorite }: { item: Item; busy: boolean; onFavorite: () => void }) {
  const url = cloudFileUrl(item) as string;
  const image = item.kind === 'image';
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>(image ? 'loading' : 'ready');
  const origin = [item.chat, day(item.created_at)].filter(Boolean).join(' · ');
  return (
    <article className="lib-card">
      <button
        type="button"
        className="lib-open"
        aria-label={`Открыть: ${item.name}`}
        onClick={(event) => {
          if (!image) return void openArtifact(item);
          if (state === 'failed') return notify('Не удалось загрузить изображение', true);
          openImage(item, url, event.currentTarget.querySelector('img'));
        }}
      >
        <span className={`lib-preview${state === 'loading' ? ' is-loading' : ''}${image ? '' : ' is-file'}`}>
          {image ? (
            state === 'failed'
              ? <ImageOff aria-hidden="true" />
              : <img alt="" loading="lazy" decoding="async" draggable={false} src={url} onLoad={() => setState('ready')} onError={() => setState('failed')} />
          ) : (
            <>
              <FileText aria-hidden="true" />
              <b>{extension(item.name)}</b>
            </>
          )}
        </span>
        <span className="lib-meta">
          <b title={item.name}>{item.name}</b>
          <small title={origin}>{origin || item.source}</small>
        </span>
      </button>
      <span className="lib-actions">
        <button type="button" className="lib-action" title={item.favorite ? 'Убрать из избранного' : 'В избранное'} aria-label={item.favorite ? 'Убрать из избранного' : 'В избранное'} aria-pressed={Boolean(item.favorite)} disabled={busy} onClick={onFavorite}>
          <Star aria-hidden="true" />
        </button>
        <a className="lib-action" href={url} download={item.name} title="Скачать" aria-label={`Скачать: ${item.name}`}><Download aria-hidden="true" /></a>
      </span>
    </article>
  );
}

/** Библиотека: вкладка вместо ленты чата, сетка вложений с превью, подгрузка при прокрутке. */
export function LibraryPage() {
  const [tab, setTab] = useState<Tab>('recent');
  const [items, setItems] = useState<Item[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [days, setDays] = useState<number | null>(null);
  const [pending, setPending] = useState<Set<string>>(new Set());
  const request = useRef(0);
  const abort = useRef<AbortController | null>(null);
  const sentinel = useRef<HTMLDivElement>(null);
  const state = useRef({ cursor: null as string | null, loading: false, tab: 'recent' as Tab });
  state.current.cursor = cursor;
  state.current.loading = loading;
  state.current.tab = tab;

  const load = useCallback(async (reset: boolean, after: string | null, which: Tab) => {
    abort.current?.abort();
    const controller = (abort.current = new AbortController());
    const mine = ++request.current;
    if (reset) {
      setItems([]);
      setCursor(null);
    }
    setLoading(true);
    setError('');
    try {
      const query = new URLSearchParams({ tab: which, limit: '48' });
      if (!reset && after) query.set('cursor', after);
      const page = await api<{ items?: Item[]; next_cursor?: string | null; retention_days?: number }>(cloudPath(`library?${query}`), { signal: controller.signal });
      if (mine !== request.current) return;
      const fresh = (page.items ?? []).filter((item) => cloudFileUrl(item));
      setItems((previous) => {
        const map = new Map((reset ? [] : previous).map((item) => [item.id, item]));
        for (const item of fresh) map.set(item.id, item);
        return [...map.values()];
      });
      setCursor(page.next_cursor ?? null);
      if (Number.isInteger(page.retention_days)) setDays(page.retention_days as number);
    } catch (reason) {
      if (mine === request.current && (reason as Error).name !== 'AbortError') setError((reason as Error).message);
    } finally {
      if (mine === request.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(true, null, 'recent');
    return () => { request.current += 1; abort.current?.abort(); };
  }, [load]);

  // следующая порция подгружается сама, когда конец списка близко
  useEffect(() => {
    const node = sentinel.current;
    const root = document.getElementById('feed');
    if (!node || !root) return undefined;
    const watcher = new IntersectionObserver((entries) => {
      const now = state.current;
      if (entries.some((entry) => entry.isIntersecting) && now.cursor && !now.loading) void load(false, now.cursor, now.tab);
    }, { root, rootMargin: '0px 0px 480px 0px' });
    watcher.observe(node);
    return () => watcher.disconnect();
  }, [load]);

  const favorite = async (item: Item) => {
    if (pending.has(item.id)) return;
    setPending((value) => new Set(value).add(item.id));
    try {
      const saved = await api<{ favorite?: boolean }>(cloudPath(`library/${encodeURIComponent(item.id)}/favorite`), { method: 'PUT', body: JSON.stringify({ favorite: !item.favorite }) });
      const next = Boolean(saved.favorite);
      setItems((previous) => (tab === 'favorites' && !next ? previous.filter((value) => value.id !== item.id) : previous.map((value) => (value.id === item.id ? { ...value, favorite: next } : value))));
    } catch (reason) {
      notify((reason as Error).message, true);
    } finally {
      setPending((value) => { const copy = new Set(value); copy.delete(item.id); return copy; });
    }
  };

  const choose = (which: Tab) => {
    if (which === tab) return;
    setTab(which);
    void load(true, null, which);
  };

  return (
    <section className="page-content library-page" aria-label="Библиотека">
      <div className="page-heading">
        <div>
          <h1>Библиотека</h1>
          <p className="muted">{days ? `Изображения и файлы из ваших чатов хранятся ${days} дн.` : 'Изображения и файлы из ваших чатов'}</p>
        </div>
      </div>
      <div className="lib-tabs" role="tablist" aria-label="Разделы библиотеки">
        {tabs.map(([id, label]) => (
          <button key={id} type="button" role="tab" data-library-tab={id} aria-selected={id === tab} onClick={() => choose(id)}>{label}</button>
        ))}
      </div>
      <div className="lib-grid" aria-busy={loading}>
        {items.map((item) => <Card key={item.id} item={item} busy={pending.has(item.id)} onFavorite={() => void favorite(item)} />)}
        {loading && Array.from({ length: items.length ? 4 : 10 }, (_, index) => (
          <div key={`s${index}`} className="lib-card lib-skeleton" aria-hidden="true">
            <span className="lib-preview"><span className="sk" /></span>
            <span className="lib-meta"><i className="sk" style={{ width: index % 2 ? '64%' : '80%' }} /><i className="sk" style={{ width: '42%' }} /></span>
          </div>
        ))}
      </div>
      {!loading && !items.length && !error && (
        <div className="lib-empty" role="status">
          <Images aria-hidden="true" />
          <p>{empty[tab]}</p>
        </div>
      )}
      {error && (
        <div className="lib-empty" role="status">
          <p>{error}</p>
          <button type="button" className="secondary" onClick={() => void load(!items.length, cursor, tab)}>Повторить</button>
        </div>
      )}
      <div ref={sentinel} className="lib-sentinel" aria-hidden="true" />
    </section>
  );
}

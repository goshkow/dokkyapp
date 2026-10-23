import { ArrowDown } from 'lucide-react';
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { cloudFileUrl, isImageFile, safeSource, shownInline, sourceDomain, sourceLetter, type CloudFile, type CloudSource } from '../../lib/cloud';
import { duration, type Message } from '../../lib/journal';
import { EASE, reducedMotion } from '../../lib/motion';
import { followFeed } from '../../state/chatCore';
import {
  PAST_LABELS, restoreSnapshot, savedDurations, setExpanded, snapshotFor, useProgress,
  type LiveProgress, type ProgressEntry, type ProgressSnapshot,
} from '../../state/cloudProgress';
import { openImage } from '../../state/viewers';
import { FileTile } from './FileTile';
import { Markdown } from './Markdown';

/* источники в конце ответа */
export function CloudSources({ citations }: { citations: unknown[] | undefined }) {
  const [all, setAll] = useState(false);
  const cards = useMemo(() => {
    const seen = new Set<string>();
    const result: Array<{ source: CloudSource; href: string; domain: string }> = [];
    for (const raw of (citations ?? []) as CloudSource[]) {
      const safe = safeSource(raw);
      if (!safe || seen.has(raw.url)) continue;
      seen.add(raw.url);
      result.push({ source: raw, href: safe.href, domain: safe.domain });
    }
    return result;
  }, [citations]);
  if (!cards.length) return null;
  const limited = cards.length > 3 && !all;
  return (
    <section className="cloud-sources">
      <h3>Источники ответа</h3>
      <div className={`ai-source-card-list${cards.length > 3 && limited ? ' is-limited' : ''}`}>
        {cards.map(({ source, href, domain }) => (
          <a
            key={href}
            className="ai-source-card"
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(event) => {
              if (!window.koddiDesktop) return;
              event.preventDefault();
              window.location.href = `koddi://open-url?url=${encodeURIComponent(href)}`;
            }}
          >
            <div className="ai-source-card-copy">
              <div className="ai-source-card-site">
                <span className="ai-source-icon" aria-hidden="true">{sourceLetter(source)}</span>
                <span>{domain}</span>
              </div>
              <div className="ai-source-card-title">{source.title || sourceDomain(source)}</div>
              <p className="ai-source-card-description">{source.snippet || ''}</p>
            </div>
          </a>
        ))}
      </div>
      {cards.length > 3 && (
        <button type="button" className="ai-source-show-all" aria-expanded={all} onClick={() => setAll((value) => !value)}>
          {all ? 'Свернуть' : `Показать все (${cards.length})`}
        </button>
      )}
    </section>
  );
}

/* файлы и изображения сообщения */
export function MessageAttachments({ message, files }: { message: Message; files?: CloudFile[] }) {
  const items = useMemo(() => {
    const list = files ?? [...((message.attachments ?? []) as CloudFile[]), ...((message.artifacts ?? []) as CloudFile[])];
    const seen = new Set<string>();
    const result: Array<{ file: CloudFile; url: string; image: boolean }> = [];
    for (const file of list) {
      const url = cloudFileUrl(file);
      if (!url || seen.has(url)) continue;
      // картинка из ответа уже стоит в тексте (по номеру медиа): второй раз её не повторяем
      if (message.role !== 'user' && shownInline(file, message.content)) continue;
      seen.add(url);
      result.push({ file, url, image: isImageFile(file) });
    }
    return result;
  }, [message, files]);
  if (!items.length) return null;
  const user = message.role === 'user';
  const pictures = items.filter((item) => item.image);
  const documents = items.filter((item) => !item.image);
  const open = (file: CloudFile, url: string, node: HTMLElement | null) => openImage(file, url, node?.querySelector('img') ?? null);
  return (
    <>
      {pictures.length > 0 && (user ? (
        // у пользователя вложения - маленькие плитки над сообщением
        <div className="message-attachments user-attachments">
          {pictures.map(({ file, url }) => {
            const name = file.name || 'Изображение';
            return (
              <button key={url} type="button" className="message-attachment is-image" title={name} aria-label={`Посмотреть ${name}`} onClick={(event) => open(file, url, event.currentTarget)}>
                <img alt={name} loading="lazy" decoding="async" src={url} onLoad={followFeed} />
              </button>
            );
          })}
        </div>
      ) : (
        // у Докки созданная картинка стоит в сообщении крупно, как на сайте
        pictures.map(({ file, url }) => {
          const name = file.name || 'Изображение';
          return (
            <button key={url} type="button" className="message-media" title={name} aria-label={`Посмотреть ${name}`} onClick={(event) => open(file, url, event.currentTarget)}>
              <img alt={name} loading="lazy" decoding="async" src={url} onLoad={followFeed} />
            </button>
          );
        })
      ))}
      {documents.length > 0 && (
        <div className={`message-files${user ? ' user-files' : ''}`}>
          {documents.map(({ file, url }) => <FileTile key={url} file={file} />)}
        </div>
      )}
    </>
  );
}

/* ход работы */
function Entries({ entries }: { entries: ProgressEntry[] }) {
  return (
    <div className="work-progress-history">
      {entries.map((entry, index) => (
        <div key={index} className={`work-progress-entry${entry.type === 'commentary' ? ' is-commentary' : ''}`} title={entry.type === 'commentary' ? undefined : entry.text}>
          {entry.type === 'commentary' ? <Markdown text={entry.text} className="" /> : PAST_LABELS[entry.node] || entry.text}
        </div>
      ))}
    </div>
  );
}

function Toggle({ label, expanded, onToggle, panel }: { label: string; expanded: boolean; onToggle: () => void; panel: string }) {
  return (
    <button type="button" className="work-progress-toggle" aria-expanded={expanded} aria-controls={panel} onClick={onToggle}>
      <span>{label}</span>
      <span className="work-progress-chevron"><ArrowDown aria-hidden="true" /></span>
    </button>
  );
}

/** Меняющаяся подпись статуса: прежний текст уходит вверх, новый поднимается снизу. */
function StatusLine({ text }: { text: string }) {
  const label = useRef<HTMLSpanElement>(null);
  const previous = useRef(text);
  const [outgoing, setOutgoing] = useState<{ text: string; left: number; top: number; key: number } | null>(null);
  useLayoutEffect(() => {
    const node = label.current;
    if (!node || previous.current === text) return;
    const old = previous.current;
    previous.current = text;
    for (const animation of node.getAnimations()) animation.cancel();
    if (reducedMotion()) return;
    setOutgoing({ text: old, left: node.offsetLeft, top: node.offsetTop, key: Date.now() });
    node.animate([{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'translateY(0)' }], { duration: 340, easing: EASE });
  }, [text]);
  useEffect(() => {
    if (!outgoing) return undefined;
    const timer = window.setTimeout(() => setOutgoing(null), 260);
    return () => window.clearTimeout(timer);
  }, [outgoing]);
  return (
    <div className="status-line" role="status" aria-live="polite" style={{ position: 'relative' }}>
      <span className="spinner" aria-hidden="true" />
      {outgoing && <div key={outgoing.key} className="status-outgoing" aria-hidden="true" style={{ position: 'absolute', left: outgoing.left, top: outgoing.top, pointerEvents: 'none', animation: 'status-out 240ms ease-in forwards' }}>{outgoing.text}</div>}
      <span ref={label}>{text}</span>
    </div>
  );
}

let panelSequence = 0;
const usePanelId = () => useRef(`work-progress-${++panelSequence}`).current;

/** Живой ход работы Докки в чате: статус, комментарии, свёртывание при начале ответа. */
function LiveWork({ live }: { live: LiveProgress }) {
  const liveBox = useRef<HTMLDivElement>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [expanded, setOpen] = useState(false);
  const panel = usePanelId();
  useLayoutEffect(() => {
    const node = liveBox.current;
    if (!live.answerStarted || !node || collapsed) return;
    if (reducedMotion()) {
      setCollapsed(true);
      return;
    }
    const height = node.getBoundingClientRect().height;
    node.style.overflow = 'hidden';
    node.inert = true;
    node.animate([{ height: `${height}px`, opacity: 1, transform: 'scale(1)' }, { height: '0px', opacity: 0, transform: 'scale(.98)' }], { duration: 360, easing: EASE, fill: 'forwards' })
      .finished.catch(() => undefined).then(() => setCollapsed(true));
  }, [live.answerStarted, collapsed]);
  const label = `Работал ${duration(live.elapsed ?? Date.now() - live.started)}`;
  return (
    <section className={`work-progress${expanded ? ' is-expanded' : ''}`}>
      <div className="work-progress-live" ref={liveBox} hidden={collapsed}>
        <div className="work-progress-comments">
          {live.blocks.map((block) => block.kind === 'commentary' ? (
            <div key={block.key} className="work-progress-commentary"><Markdown text={block.text} className="" /></div>
          ) : (
            <details key={block.key} className="work-progress-phase">
              <summary className="work-progress-toggle"><span>{block.label}</span><span className="work-progress-chevron"><ArrowDown aria-hidden="true" /></span></summary>
              <div>{block.rows.map((row, index) => <div key={index}>{row}</div>)}</div>
            </details>
          ))}
        </div>
        <StatusLine text={live.status.text} />
        {live.preview && (
          <figure className="browser-preview">
            <img alt="Страница в браузере Докки" src={live.preview.image} />
            <figcaption>{live.preview.title}</figcaption>
          </figure>
        )}
      </div>
      {live.answerStarted && <Toggle label={label} expanded={expanded} panel={panel} onToggle={() => setOpen((value) => !value)} />}
      <div className="work-progress-panel" id={panel} inert={!expanded}>
        {expanded && <Entries entries={live.entries} />}
      </div>
    </section>
  );
}

export function CloudWork() {
  const live = useProgress((state) => state.live);
  if (!live) return null;
  return (
    <section className="cloud-work turn-message assistant">
      <LiveWork live={live} />
    </section>
  );
}

/** Ход работы под готовым ответом: «Работал N сек.» с раскрывающимся перечнем этапов. */
export function MessageProgress({ message }: { message: Message }) {
  const id = String(message.id);
  const activity = (message.activity ?? []) as Array<{ kind?: string; text?: string }>;
  const snapshot = useMemo<ProgressSnapshot | null>(() => {
    let cached = snapshotFor(id) ?? null;
    if (activity.length) {
      cached = {
        started: cached?.started ?? 0,
        expanded: cached?.expanded ?? false,
        elapsed: cached?.elapsed ?? savedDurations()[id] ?? null,
        messageId: id,
        entries: activity
          .filter((item) => item.text && item.kind !== 'title')
          .map((item) => ({ type: item.kind === 'commentary' ? 'commentary' : 'status', node: item.kind ?? 'status', text: item.text as string })),
      };
    }
    if (cached?.entries.length) return cached;
    return restoreSnapshot(id);
  }, [id, activity]);
  const [expanded, setOpen] = useState(snapshot?.expanded ?? false);
  const panel = usePanelId();
  if (!snapshot || String(id).startsWith('pending_')) return null;
  const label = snapshot.elapsed != null && Number.isFinite(snapshot.elapsed) ? `Работал ${duration(snapshot.elapsed)}` : 'Время работы не сохранено';
  return (
    <section className={`work-progress is-complete${expanded ? ' is-expanded' : ''}`}>
      <Toggle
        label={label}
        expanded={expanded}
        panel={panel}
        onToggle={() => {
          setOpen((value) => !value);
          setExpanded(id, !expanded);
        }}
      />
      <div className="work-progress-panel" id={panel} inert={!expanded}>
        {expanded && <Entries entries={snapshot.entries} />}
      </div>
    </section>
  );
}

import hljs from 'highlight.js/lib/common';
import { Check, Copy, Download, ExternalLink, FileText, Heart, Pencil, Presentation, Sheet, FileArchive, SendHorizontal, X } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { api, cloudPath } from '../../lib/api';
import { cloudFileUrl, fileExtension, formatBytes, isImageFile, previewableText, safeSource } from '../../lib/cloud';
import { languageFor } from '../../lib/diff';
import { renderMarkdown } from '../../lib/markdown';
import { EASE, reducedMotion } from '../../lib/motion';
import { copyImage, editWithPrompt, fileId, isFavorite, setFavorite } from '../../state/mediaActions';
import { guarded } from '../../state/overlay';
import { useUi } from '../../state/ui';
import { closeArtifact, closeImage, useViewers } from '../../state/viewers';
import { PlainDialog } from '../ui/PlainDialog';

interface ArtifactInfo {
  content?: unknown;
  size?: number;
  media_type?: string;
}

/** Значок по расширению: презентации, таблицы, архивы и документы выглядят по-разному. */
function glyph(name: string | undefined): ReactNode {
  const ext = fileExtension(name).toLowerCase();
  if (['ppt', 'pptx', 'key'].includes(ext)) return <Presentation aria-hidden="true" />;
  if (['xls', 'xlsx', 'csv', 'ods'].includes(ext)) return <Sheet aria-hidden="true" />;
  if (['zip', '7z', 'rar', 'tar', 'gz'].includes(ext)) return <FileArchive aria-hidden="true" />;
  return <FileText aria-hidden="true" />;
}

/** Окно просмотра как на сайте: картинка или файл по центру, под ними действия, правка по описанию раскрывается снизу. */
export function MediaViewer() {
  const image = useViewers((state) => state.image);
  const artifact = useViewers((state) => state.artifact);
  const state = image ?? (artifact ? { file: artifact, from: null, src: '' } : null);
  const last = useRef(state);
  if (state) last.current = state;
  const shown = state ?? last.current;
  const file = shown?.file;
  const picture = Boolean(file && isImageFile(file));
  const development = useUi((s) => s.scope === 'development');
  const search = useUi((s) => s.scope === 'search');
  const [zoomed, setZoomed] = useState(false);
  const [favorite, setFavoriteState] = useState<boolean | null>(null);
  const [editing, setEditing] = useState(false);
  const [prompt, setPrompt] = useState('');
  const [sending, setSending] = useState(false);
  const [info, setInfo] = useState<ArtifactInfo | null>(null);
  const [preview, setPreview] = useState<'loading' | 'ready' | 'none' | 'failed'>('loading');
  const [attempt, setAttempt] = useState(0);
  const img = useRef<HTMLImageElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const open = Boolean(state);
  const close = () => { closeImage(); closeArtifact(); };

  // при каждом открытии сбрасываются масштаб, правка и состояние избранного
  useEffect(() => {
    if (!state) return;
    setZoomed(false);
    setEditing(false);
    setPrompt('');
    setSending(false);
    setFavoriteState(null);
    void isFavorite(state.file).then((value) => setFavoriteState(value));
  }, [image, artifact]);

  // сведения и предпросмотр файла (текст показывается, остальное - карточка)
  useEffect(() => {
    if (!artifact || picture) return undefined;
    const controller = new AbortController();
    setPreview('loading');
    setInfo(null);
    const id = fileId(artifact);
    if (!id) {
      setPreview('none');
      return undefined;
    }
    api<ArtifactInfo>(cloudPath(`artifacts/art_${id.slice(5)}`), { signal: controller.signal })
      .then((data) => {
        if (controller.signal.aborted) return;
        setInfo(data);
        if (typeof data.content !== 'string') return setPreview('none');
        setPreview('ready');
        requestAnimationFrame(() => {
          const target = body.current;
          if (!target) return;
          target.replaceChildren();
          const text = data.content as string;
          if (/\.(md|markdown)$/i.test(artifact.name ?? '')) {
            target.classList.add('message-body');
            renderMarkdown(target, text, true);
            return;
          }
          const pre = document.createElement('pre');
          const code = document.createElement('code');
          code.textContent = text;
          const language = languageFor(artifact.name ?? '');
          if (language && hljs.getLanguage(language) && text.length <= 100_000) code.innerHTML = hljs.highlight(text, { language, ignoreIllegals: true }).value;
          pre.append(code);
          target.append(pre);
        });
      })
      .catch(() => { if (!controller.signal.aborted) setPreview('failed'); });
    return () => controller.abort();
  }, [artifact, picture, attempt]);

  // миниатюра вылетает на своё место в окне просмотра
  const fly = () => {
    const node = img.current;
    const from = image?.from;
    if (!node || !from || reducedMotion()) return;
    const target = node.getBoundingClientRect();
    if (!target.width || !target.height) return;
    const x = from.left + from.width / 2 - target.left - target.width / 2;
    const y = from.top + from.height / 2 - target.top - target.height / 2;
    node.animate([
      { transform: `translate(${x}px,${y}px) scale(${from.width / target.width},${from.height / target.height})`, opacity: 0.6 },
      { transform: 'none', opacity: 1 },
    ], { duration: 280, easing: EASE });
  };

  const name = file?.name || (picture ? 'Изображение' : 'Файл');
  const url = file ? cloudFileUrl(file) : null;
  const external = file?.source_url ? safeSource({ url: file.source_url }) : null;
  const canEdit = Boolean(file && fileId(file) && !development && !search);
  const meta = [fileExtension(name), formatBytes(info?.size ?? file?.size)].filter(Boolean).join(' · ');
  const toggleFavorite = guarded(async () => {
    if (!file) return;
    setFavoriteState(await setFavorite(file, !favorite));
  });
  const submit = guarded(async () => {
    if (!file || sending || !prompt.trim()) return;
    setSending(true);
    try {
      await editWithPrompt(file, prompt, picture ? (image?.src ?? url) : null);
    } finally {
      setSending(false);
    }
  });

  return (
    <PlainDialog open={open} onClose={close} className="media-viewer" label={picture ? 'Просмотр изображения' : `Просмотр файла: ${name}`} closeDelay={160}>
      <button type="button" className="mv-close" title="Закрыть" aria-label="Закрыть" onClick={close}><X aria-hidden="true" /></button>
      <div className="mv-stage" data-kind={picture ? 'image' : 'file'} onClick={(event) => { if (event.target === event.currentTarget) close(); }}>
        {picture && shown ? (
          <img
            ref={img}
            className={`mv-image${zoomed ? ' is-zoomed' : ''}`}
            alt={name}
            src={shown.src || url || ''}
            draggable={false}
            title={zoomed ? 'Уменьшить' : 'Увеличить'}
            onClick={() => setZoomed((value) => !value)}
            onLoad={fly}
          />
        ) : (
          <article className="mv-file">
            <header>
              <span className="mv-file-icon">{glyph(name)}</span>
              <div>
                <h2>{name}</h2>
                {meta && <p>{meta}</p>}
              </div>
            </header>
            {preview === 'loading' && <div className="mv-file-body skeleton" aria-busy="true" />}
            {preview === 'ready' && <div className="mv-file-body" ref={body} />}
            {preview === 'none' && (
              <div className="mv-file-empty">
                {glyph(name)}
                <p>{previewableText(file ?? {}) ? 'Содержимое недоступно для просмотра' : 'Предпросмотр этого формата недоступен'}</p>
                <span>Скачайте файл, чтобы открыть его на компьютере.</span>
              </div>
            )}
            {preview === 'failed' && (
              <div className="mv-file-empty">
                <p>Не удалось загрузить файл</p>
                <button type="button" className="secondary" onClick={() => setAttempt((value) => value + 1)}>Повторить</button>
              </div>
            )}
          </article>
        )}
      </div>
      <div className="mv-actions" role="toolbar" aria-label="Действия с файлом">
        {file && fileId(file) && favorite !== null && (
          <button type="button" aria-pressed={favorite} onClick={toggleFavorite}>
            <Heart aria-hidden="true" className={favorite ? 'is-filled' : ''} /><span>{favorite ? 'В избранном' : 'В избранное'}</span>
          </button>
        )}
        {url && (
          <a href={url} download={name}><Download aria-hidden="true" /><span>Скачать</span></a>
        )}
        {picture && (shown?.src || url) && (
          <button type="button" onClick={(event) => void copyImage(shown?.src || url || '', event.nativeEvent)}><Copy aria-hidden="true" /><span>Копировать</span></button>
        )}
        {canEdit && (
          <button type="button" aria-expanded={editing} onClick={() => setEditing((value) => !value)}>
            {editing ? <Check aria-hidden="true" /> : <Pencil aria-hidden="true" />}<span>Редактировать</span>
          </button>
        )}
        {external && (
          <a
            href={external.href}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(event) => {
              if (!window.koddiDesktop) return;
              event.preventDefault();
              window.location.href = `koddi://open-url?url=${encodeURIComponent(external.href)}`;
            }}
          ><ExternalLink aria-hidden="true" /><span>Источник</span></a>
        )}
      </div>
      <div className={`mv-edit${editing ? ' is-open' : ''}`} aria-hidden={!editing}>
        <form
          onSubmit={(event) => { event.preventDefault(); void submit(); }}
        >
          <textarea
            aria-label={picture ? 'Как изменить изображение' : 'Как изменить файл'}
            placeholder={picture ? 'Что изменить в этом изображении?' : 'Что изменить в этом файле?'}
            value={prompt}
            tabIndex={editing ? 0 : -1}
            onChange={(event) => setPrompt(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && (event.ctrlKey || event.metaKey || !event.shiftKey)) {
                event.preventDefault();
                void submit();
              }
            }}
          />
          <button type="submit" disabled={sending || !prompt.trim()} tabIndex={editing ? 0 : -1}><SendHorizontal aria-hidden="true" /><span>Отправить на правку</span></button>
        </form>
      </div>
    </PlainDialog>
  );
}

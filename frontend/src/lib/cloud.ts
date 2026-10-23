/* Облачные сообщения и файлы Докки: приведение к виду ленты и ссылки. */
import { cloudPath } from './api';
import type { Message } from './journal';

/* медиа сообщений (формат app/1): картинки в тексте приходят как ![подпись](dokky:media/ID) */
export interface CloudMedia {
  id: string;
  kind?: string;
  file_id?: string;
  artifact_id?: string;
  url?: string;
  thumbnail_url?: string;
  media_type?: string;
  alt?: string;
  provider?: string;
  video_id?: string;
  title?: string;
  author?: string;
  name?: string;
}

const mediaRegistry = new Map<string, CloudMedia>();

export function registerMedia(list: CloudMedia[] | CloudMedia | null | undefined): void {
  for (const item of Array.isArray(list) ? list : list ? [list] : []) {
    if (item && typeof item.id === 'string') mediaRegistry.set(item.id, { ...mediaRegistry.get(item.id), ...item });
  }
}

export const findMedia = (id: string | undefined): CloudMedia | undefined => (id ? mediaRegistry.get(id) : undefined);

/** Адрес медиа на сервере Докки → адрес через локальный прокси приложения. */
export function mediaUrl(media: CloudMedia | undefined): string | null {
  const raw = media?.url ?? '';
  if (!raw) return media?.file_id ? cloudPath(`files/${encodeURIComponent(media.file_id)}/content`) : null;
  if (/^https?:\/\//i.test(raw)) return raw;
  const proxy = /^\/api\/v1\/media\/proxy\?(.+)$/.exec(raw);
  if (proxy) return `/api/dokky/media?${proxy[1]}`;
  const path = /^\/api\/v1\/(.+)$/.exec(raw);
  return path ? cloudPath(path[1] as string) : null;
}

/** Подставляет в текст адреса картинок; пока медиа не пришло, при печати ссылка скрыта, в готовом тексте остаётся подпись. */
export function resolveMedia(text: string, streaming = false): string {
  if (!text.includes('dokky:media/')) return text;
  return text.replace(/(!?)\[([^\]\n]*)\]\(dokky:media\/([A-Za-z0-9_.:-]+)\)/g, (_all, bang: string, label: string, id: string) => {
    const media = mediaRegistry.get(id);
    const name = label || media?.alt || media?.title || media?.name || 'Изображение';
    const url = mediaUrl(media);
    if (media?.provider === 'youtube' && media.video_id) return `![${name}](https://www.youtube.com/watch?v=${media.video_id})`;
    if (!media || !url) return streaming ? '' : name;
    // картинка встаёт на место в richHydrate (по номеру медиа), файлы и звук показывают вложения сообщения
    if (bang && (media.kind === 'image' || media.kind === 'generated_image' || String(media.media_type ?? '').startsWith('image/'))) return `![${name}](/__rich__/${id})`;
    if (bang && media.kind === 'video') return `![${name}](${url})`;
    return name;
  });
}

/** Сообщение API v1 → сообщение ленты: служебные поля уходят в metadata. */
export function cloudMessage(raw: Message): Message {
  registerMedia((raw as unknown as { media?: CloudMedia[] }).media);
  return {
    ...raw,
    metadata: {
      cloud_blocks: (raw as unknown as { blocks?: unknown[] }).blocks || [],
      status: raw.status || 'completed',
      events: [],
      cloud_activity: raw.activity || [],
      cloud_citations: raw.citations || [],
      cloud_artifacts: raw.artifacts || [],
      cloud_attachments: raw.attachments || [],
      cloud_version: raw.version || 1,
    },
  };
}

export interface CloudFile {
  id?: string;
  file_id?: string;
  name?: string;
  media_type?: string;
  type?: string;
  kind?: string;
  viewer_url?: string;
  favorite?: boolean;
  created_at?: string;
  source?: string;
  chat?: string;
  size?: number;
  /** Вложение локального режима: открыть его просмотром нельзя, только показать плитку. */
  local?: boolean;
  /** Медиа, на которое ссылается текст ответа (`dokky:media/ID`): такое вложение дублирует картинку в тексте. */
  media_id?: string;
  source_url?: string;
  thumbnail_url?: string;
  width?: number;
  height?: number;
}

const IMAGE_KINDS = new Set(['image', 'generated_image']);

/** Картинка по типу артефакта (`image`, `generated_image`) или по MIME-типу. */
export const isImageFile = (file: Partial<CloudFile> | null | undefined): boolean =>
  Boolean(file) && (IMAGE_KINDS.has(String(file?.type ?? '')) || IMAGE_KINDS.has(String(file?.kind ?? '')) || String(file?.media_type ?? '').startsWith('image/'));

/** Номера медиа, на которые ссылается текст: такие картинки уже стоят в ответе и вложениями не повторяются. */
export function inlineMediaIds(content: string | undefined): Set<string> {
  const ids = new Set<string>();
  for (const match of (content ?? '').matchAll(/dokky:media\/([A-Za-z0-9_.:-]+)/g)) ids.add(match[1] as string);
  return ids;
}

/** Файл показан в тексте ответа: по номеру медиа или по файлу, на который указывает это медиа. */
export function shownInline(file: CloudFile, content: string | undefined): boolean {
  const ids = inlineMediaIds(content);
  if (!ids.size) return false;
  if (file.media_id && ids.has(file.media_id)) return true;
  const own = file.file_id || file.id;
  for (const id of ids) {
    const media = mediaRegistry.get(id);
    if (media && own && (media.file_id === own || media.artifact_id === file.id)) return true;
  }
  return false;
}

const TEXT_PREVIEW = /\.(md|markdown|txt|csv|json|html?|xml|ya?ml|log|py|js|ts|tsx|jsx|css|sql|sh|toml|ini)$/i;
export const previewableText = (file: Partial<CloudFile>): boolean => TEXT_PREVIEW.test(file.name ?? '');

export const fileExtension = (name: string | undefined): string => (/\.([A-Za-z0-9]{1,8})$/.exec(name ?? '')?.[1] ?? '').toUpperCase();

export function formatBytes(size: number | undefined): string {
  if (!Number.isFinite(size) || !size || size < 0) return '';
  if (size < 1024) return `${size} Б`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(size < 10240 ? 1 : 0)} КБ`.replace('.', ',');
  return `${(size / 1024 / 1024).toFixed(size < 10485760 ? 1 : 0)} МБ`.replace('.', ',');
}

export function cloudFileUrl(file: CloudFile): string | null {
  if (file.viewer_url) return file.viewer_url;
  const id = file.file_id || file.id;
  return /^file_[A-Za-z0-9_-]{1,128}$/.test(id || '') ? cloudPath(`files/${encodeURIComponent(id as string)}/content`) : null;
}

export interface CloudSource {
  id?: string;
  url: string;
  title?: string;
  snippet?: string;
  spans?: Array<{ start: number; end: number }>;
}

export interface SafeSource {
  url: string;
  href: string;
  domain: string;
  title: string;
  snippet: string;
}

/** Ссылка источника допустима только http(s) без логина и пароля. */
export function safeSource(source: Partial<CloudSource> | null | undefined): SafeSource | null {
  let url: URL;
  try {
    url = new URL(String(source?.url ?? ''));
  } catch {
    return null;
  }
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return null;
  const domain = url.hostname.replace(/^www\./, '');
  return { url: String(source?.url), href: url.href, domain, title: source?.title ?? '', snippet: source?.snippet ?? '' };
}

export const sourceDomain = (source: Partial<CloudSource>): string => safeSource(source)?.domain ?? 'Источник';
export const sourceLetter = (source: Partial<CloudSource>): string => (Array.from(sourceDomain(source))[0] ?? '?').toUpperCase();

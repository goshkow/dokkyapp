/* Разметка ответов Докки: то, что сайт показывает в чате (картинки, видео, файлы, таблицы,
   цитаты и спойлеры), приведено к тому же виду. Правила разбора перенесены из md() сайта
   (mini_app/assets/app.js), показ картинок идёт через вложения API и защищённый прокси. */
import { findMedia, mediaUrl, resolveMedia } from './cloud';

export interface RichFile {
  id?: string;
  file_id?: string;
  name?: string;
  type?: string;
  media_type?: string;
  viewer_url?: string;
  source_url?: string;
}

export interface RichHooks {
  /** Адрес файла Докки для показа в окне. */
  fileUrl?: (file: RichFile) => string | null;
  openImage?: (file: RichFile, image: HTMLImageElement) => void;
}
export const richHooks: RichHooks = {};

const FILE_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/></svg>';

const richVideoHosts = /^(?:www\.|m\.)?(?:youtube\.com|youtu\.be|vimeo\.com|rutube\.ru|vkvideo\.ru|tiktok\.com)$/;

export function richUrl(value: string): URL | null {
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url : null;
  } catch {
    return null;
  }
}

export function richIsVideo(value: string): boolean {
  const url = richUrl(value);
  return Boolean(url && (richVideoHosts.test(url.hostname.toLowerCase()) || /\.(?:mp4|webm|mov|m4v)$/i.test(url.pathname)));
}

export function richYouTubeId(value: string): string {
  const url = richUrl(value);
  if (!url) return '';
  let id = '';
  if (/^(?:www\.|m\.)?youtube\.com$/i.test(url.hostname)) id = url.searchParams.get('v') || url.pathname.split('/')[2] || '';
  else if (/^youtu\.be$/i.test(url.hostname)) id = url.pathname.slice(1).split('/')[0] ?? '';
  return /^[A-Za-z0-9_-]{11}$/.test(id) ? id : '';
}

/** Адрес картинки для окна приложения: только через локальный прокси с проверкой адреса. */
export const richMediaURL = (value: string) => `/api/dokky/media?url=${encodeURIComponent(value)}`;

/** Преобразования применяются только вне кода. */
function outsideCode(text: string, transform: (part: string) => string): string {
  return text
    .split(/(```[\s\S]*?(?:```|$)|`[^`\n]*`)/g)
    .map((part, index) => (index % 2 ? part : transform(part)))
    .join('');
}
const strip = (value: string | undefined) => String(value ?? '').replace(/<[^>]*>/g, '').trim();
const escape = (value: string) => value.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] ?? c);

export function richPrepare(source: string, { streaming = false }: { streaming?: boolean } = {}): string {
  let text = resolveMedia(String(source ?? '').replace(/\r/g, ''), streaming);
  if (streaming) {
    // недописанная картинка не должна мелькать сырым текстом
    text = text.replace(/!\[[^\]\n]*$/, '').replace(/!\[[^\]\n]*\]\([^)\n]*$/, '');
  }
  return outsideCode(text, (part) =>
    part
      .replace(/(\[[^\]]+\]\((https?:\/\/[^)\s]+)\))\s*\[↗\]\(\2\)/g, '$1')
      .replace(/<(tg-table|table)\b[^>]*>([\s\S]*?)<\/\1>/gi, (_m, _kind: string, body: string) => {
        const rows = [...body.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/gi)].map(([, row]) =>
          [...(row ?? '').matchAll(/<t[dh]\b[^>]*>([\s\S]*?)<\/t[dh]>/gi)].map(([, cell]) => strip(cell)),
        );
        const head = rows[0];
        if (!head) return body;
        return `\n\n| ${head.join(' | ')} |\n|${head.map(() => ' --- ').join('|')}|\n${rows.slice(1).map((row) => `| ${row.join(' | ')} |`).join('\n')}\n\n`;
      })
      .replace(/<figure>([\s\S]*?)<\/figure>/gi, (_m, body: string) => {
        const media = /<(img|video|audio|tg-document)\b[^>]*src=["'](https?:\/\/[^"']+|tg:\/\/photo\?id=[A-Za-z0-9_-]+)["'][^>]*\/?\s*>/i.exec(body);
        const caption = /<figcaption>([\s\S]*?)<\/figcaption>/i.exec(body);
        if (!media) return body;
        return `![${strip(caption?.[1]) || 'Изображение'}](${media[2]})`;
      })
      .replace(/<\/?tg-(?:slideshow|collage|table)\b[^>]*>/gi, '')
      .replace(/<tg-quote\b[^>]*>([\s\S]*?)<\/tg-quote>/gi, (_m, body: string) =>
        body.split(/<br\s*\/?\s*>|\n/i).map((line) => `> ${line}`).join('\n'),
      )
      .replace(/<tg-thinking\b[^>]*>[\s\S]*?<\/tg-thinking>/gi, '')
      .replace(/<img\b([^>]*)\/?>/gi, (_m, attrs: string) => {
        const url = /\bsrc=["']([^"']+)["']/i.exec(attrs)?.[1];
        const alt = /\balt=["']([^"']*)["']/i.exec(attrs)?.[1] || 'Изображение';
        return url && /^(?:https?:\/\/|tg:\/\/photo\?id=)/i.test(url) ? `![${alt}](${url})` : alt;
      })
      .replace(/<(video|audio)\b[^>]*src=["'](https?:\/\/[^"']+)["'][^>]*>([\s\S]*?)<\/\1>/gi, (_m, kind: string, url: string, label: string) =>
        `![${strip(label) || (kind.toLowerCase() === 'video' ? 'Видео' : 'Аудио')}](${url})`,
      )
      .replace(/<tg-document\b[^>]*src=["'](https?:\/\/[^"']+)["'][^>]*>[\s\S]*?<\/tg-document>/gi, '![Файл]($1)')
      .replace(/<tg-map\b[^>]*lat=["'](-?\d+(?:\.\d+)?)["'][^>]*long=["'](-?\d+(?:\.\d+)?)["'][^>]*\/?>/gi, (_m, lat: string, lon: string) =>
        `[Открыть карту](https://maps.google.com/?q=${lat},${lon})`,
      )
      .replace(/<a\s+href=["']((?:https?:\/\/|mailto:|tel:)[^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi, (_m, url: string, label: string) => `[${label}](${url})`)
      .replace(/<h([1-6])>([\s\S]*?)<\/h\1>/gi, (_m, level: string, label: string) => `\n\n${'#'.repeat(Number(level))} ${label}\n\n`)
      .replace(/<hr\s*\/?>/gi, '\n\n---\n\n')
      .replace(/<p>([\s\S]*?)<\/p>/gi, '$1\n\n')
      .replace(/<tg-time\b[^>]*>([\s\S]*?)<\/tg-time>/gi, '$1')
      .replace(/<tg-emoji\b[^>]*>([\s\S]*?)<\/tg-emoji>/gi, '$1')
      .replace(/<blockquote expandable>([\s\S]*?)<\/blockquote>/gi, (_m, body: string) => `<details><summary>Цитата</summary>${body}</details>`)
      .replace(/<blockquote>([\s\S]*?)<\/blockquote>/gi, (_m, body: string) =>
        body.split(/<br\s*\/?\s*>/i).map((line) => `> ${line}`).join('\n'),
      )
      .replace(/<tg-button-row[^>]*>([\s\S]*?)<\/tg-button-row>/gi, (_m, row: string) =>
        [...row.matchAll(/<tg-button\b([^>]*)>([\s\S]*?)<\/tg-button>/gi)]
          .map(([, attrs, label]) => {
            const url = /\burl=["'](https?:\/\/[^"']+|mailto:[^"']+|tel:[^"']+)["']/i.exec(attrs ?? '')?.[1];
            return url ? `[${strip(label)}](${url})` : strip(label);
          })
          .filter(Boolean)
          .join(' · '),
      )
      // вложенный markdown внутри details разбирается как обычный
      .replace(/<details(\s+open)?>\s*(<summary>[\s\S]*?<\/summary>)?([\s\S]*?)<\/details>/gi, (_m, open: string | undefined, summary: string | undefined, body: string) =>
        `\n<details${open ? ' open' : ''}>${summary || '<summary>Подробнее</summary>'}\n\n${body.trim()}\n\n</details>\n`,
      )
      .replace(/<tg-spoiler>([\s\S]*?)<\/tg-spoiler>/gi, '<span class="rich-spoiler">$1</span>')
      .replace(/<tg-reference>([\s\S]*?)<\/tg-reference>/gi, '<span class="rich-reference">$1</span>')
      .replace(/<(\/?)(?:ins)>/gi, '<$1u>')
      .replace(/<(\/?)(?:strike|s)>/gi, '<$1del>')
      .replace(/\|\|([^|\n]+)\|\|/g, '<span class="rich-spoiler">$1</span>')
      .replace(/(?<!=)==([^=\n]+)==(?!=)/g, '<mark>$1</mark>')
      .replace(/\]\(<(https?:\/\/[^<>\s]+)>\)/g, (_m, url: string) => `](${url.replaceAll('(', '%28').replaceAll(')', '%29')})`)
      // картинки: свои - по вложениям, видео и файлы - ссылками, чужие остаются картинками
      .replace(/!\[([^\]]*)\]\(tg:\/\/photo\?id=([A-Za-z0-9_-]+)\)/g, (_m, alt: string, id: string) => (streaming ? '' : `![${alt}](/__rich__/${id})`))
      .replace(/!\[([^\]]*)\]\(tg:\/\/(?:emoji|time)\?[^\s)]*\)/g, '$1')
      .replace(/!\[([^\]]*)\]\(((?:https?:\/\/)[^\s)]+)(?:\s+"([^"]*)")?\)/g, (match, alt: string, url: string, title: string | undefined) => {
        const label = title || alt;
        const path = (url.split(/[?#]/)[0] || '').toLowerCase();
        if (richIsVideo(url)) return `[${label || 'Видео'}](${url})`;
        if (/\.(mp3|m4a|wav|ogg|oga|opus)$/.test(path)) return `[${label || 'Аудио'}](${url})`;
        if (/\.(zip|pdf|docx?|pptx?|xlsx?|csv|txt|epub)$/.test(path)) return `[${label || path.split('/').pop() || 'Файл'}](${url} "rich:file")`;
        return match;
      })
      // формулы остаются текстом, пока KaTeX не отрисует их (lib/math.ts)
      .replace(/\$\$([\s\S]+?)\$\$/g, (_m, formula: string) => `\n\n<div class="rich-math" role="math">${escape(formula.trim())}</div>\n\n`)
      .replace(/\\\[([\s\S]+?)\\\]/g, (_m, formula: string) => `\n\n<div class="rich-math" role="math">${escape(formula.trim())}</div>\n\n`)
      .replace(/\\\(([^\n]+?)\\\)/g, (_m, formula: string) => `<span class="rich-math-inline" role="math">${escape(formula)}</span>`)
      // $формула$ в строке; суммы вроде «$5» и «$10 и $20» формулой не считаются
      .replace(/(?<![\w$\\])\$([^\n$]+)\$(?!\d|\$)/g, (match, formula: string) => (/^\s|\s$/.test(formula) || /^\d/.test(formula) ? match : `<span class="rich-math-inline" role="math">${escape(formula)}</span>`)),
  );
}

export const richChecklist = (html: string): string =>
  html.replace(/<input\b[^>]*type="checkbox"[^>]*>/gi, (tag) => (/\bchecked\b/i.test(tag) ? '☑ ' : '☐ '));

function openExternal(link: HTMLAnchorElement): void {
  if (!window.koddiDesktop) return;
  link.onclick = (event) => {
    event.preventDefault();
    window.location.href = `koddi://open-url?url=${encodeURIComponent(link.href)}`;
  };
}

function videoCard(url: string, label: string): HTMLAnchorElement {
  const id = richYouTubeId(url);
  const card = document.createElement('a');
  card.href = url;
  card.target = '_blank';
  card.rel = 'noopener noreferrer';
  card.className = id ? 'rich-video-card' : 'rich-video-link';
  const text = !label || label === url || label === '↗' ? (id ? 'Видео на YouTube' : 'Открыть видео') : label;
  if (!id) {
    card.textContent = text;
    openExternal(card);
    return card;
  }
  card.dataset.videoId = id;
  const thumb = document.createElement('span');
  thumb.className = 'rich-video-thumbnail';
  const image = document.createElement('img');
  image.alt = '';
  image.loading = 'lazy';
  image.decoding = 'async';
  image.referrerPolicy = 'no-referrer';
  image.src = richMediaURL(`https://i.ytimg.com/vi/${id}/hqdefault.jpg`);
  image.onerror = () => image.remove();
  const play = document.createElement('span');
  play.className = 'rich-video-play';
  play.setAttribute('aria-hidden', 'true');
  play.textContent = '▶';
  thumb.append(image, play);
  const copy = document.createElement('span');
  copy.className = 'rich-video-copy';
  const strong = document.createElement('strong');
  const title = document.createElement('span');
  title.className = 'link-label rich-video-title';
  title.textContent = text;
  strong.append(title);
  const site = document.createElement('small');
  site.textContent = 'YouTube';
  copy.append(strong, site);
  card.append(thumb, copy);
  openExternal(card);
  return card;
}

interface MessageLike {
  artifacts?: RichFile[];
  _richUsed?: Set<string>;
}

/** Какое вложение сообщения соответствует картинке в тексте. */
function artifactFor(message: MessageLike | null, id: string, alt: string, used: Set<string>): RichFile | null {
  const images = (message?.artifacts ?? []).filter(
    (item) => (item.type === 'image' || String(item.media_type ?? '').startsWith('image/')) && !used.has(item.file_id ?? item.id ?? ''),
  );
  return (
    images.find((item) => [item.id, item.file_id].includes(id)) ??
    images.find((item) => alt && item.name === alt) ??
    images[0] ??
    null
  );
}

function setImage(image: HTMLImageElement, source: string, file: RichFile, alt: string): void {
  image.className = 'message-image rich-inline-image';
  image.alt = alt || 'Изображение';
  image.loading = 'lazy';
  image.decoding = 'async';
  image.referrerPolicy = 'no-referrer';
  image.src = source;
  image.removeAttribute('width');
  image.removeAttribute('height');
  image.onclick = () => richHooks.openImage?.(file, image);
}

export function richHydrate(root: HTMLElement, { message = null, streaming = false }: { message?: unknown; streaming?: boolean } = {}): void {
  const used = new Set<string>();
  const owner = message as MessageLike | null;
  if (owner) owner._richUsed = used;
  for (const image of [...root.querySelectorAll('img')]) {
    const src = image.getAttribute('src') ?? '';
    const alt = image.getAttribute('alt') ?? '';
    if (src.startsWith('/__rich__/')) {
      const media = findMedia(src.slice('/__rich__/'.length));
      const mediaSrc = media && mediaUrl(media);
      if (media && mediaSrc) {
        used.add(media.file_id ?? media.id);
        setImage(image, mediaSrc, { id: media.file_id ?? media.id, file_id: media.file_id, name: media.alt || alt, type: 'image', media_type: media.media_type, viewer_url: mediaSrc }, alt || media.alt || '');
        continue;
      }
      const file = artifactFor(owner, src.slice('/__rich__/'.length), alt, used);
      const url = file && richHooks.fileUrl?.(file);
      if (!file || !url) {
        if (!streaming) image.replaceWith(document.createTextNode(alt || 'Изображение'));
        else image.remove();
        continue;
      }
      used.add(file.file_id ?? file.id ?? '');
      setImage(image, url, file, alt || file.name || '');
    } else if (/^https?:\/\//i.test(src) && richUrl(src)) {
      const viewer = richMediaURL(src);
      setImage(image, viewer, { name: alt || 'Изображение', viewer_url: viewer, source_url: src }, alt);
      image.dataset.sourceUrl = src;
      // если картинка не загрузилась, остаётся понятная ссылка на источник
      image.onerror = () => {
        const link = document.createElement('a');
        link.href = src;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = alt || 'Открыть изображение';
        openExternal(link);
        image.replaceWith(link);
      };
    }
  }
  for (const link of [...root.querySelectorAll('a[href]')] as HTMLAnchorElement[]) {
    if (link.closest('.code-head,.ai-source-chip') || link.classList.contains('rich-video-card')) continue;
    const href = link.getAttribute('href') ?? '';
    if (!/^https?:\/\//i.test(href)) continue;
    const label = (link.textContent ?? '').trim();
    if (richIsVideo(href)) {
      const card = videoCard(href, label);
      const parent = link.parentElement;
      if (parent?.matches('p') && (parent.textContent ?? '').trim() === label) parent.replaceWith(card);
      else link.replaceWith(card);
      continue;
    }
    if (link.getAttribute('title') === 'rich:file') {
      link.removeAttribute('title');
      link.classList.add('message-file');
      link.insertAdjacentHTML('afterbegin', FILE_ICON);
      continue;
    }
    if (label === '↗') link.textContent = 'Ссылка';
    else if (/\s*↗\s*$/.test(label) && !link.children.length) link.textContent = label.replace(/\s*↗\s*$/, '');
    if (!link.querySelector('.link-label')) {
      const span = document.createElement('span');
      span.className = 'link-label';
      span.append(...link.childNodes);
      link.append(span);
    }
  }
}

document.addEventListener('click', (event) => {
  const spoiler = (event.target as Element | null)?.closest?.('.rich-spoiler');
  if (spoiler) spoiler.classList.toggle('revealed');
});

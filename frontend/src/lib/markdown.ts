import DOMPurify from 'dompurify';
import hljs from 'highlight.js/lib/common';
import { marked } from 'marked';
import { renderMath } from './math';
import { richChecklist, richHydrate, richPrepare } from './rich';

/** Что нужно разметке от приложения: открыть файл проекта, ссылку и копирование. Подключается при старте. */
export interface MarkdownHooks {
  openProjectFile?: (path: string) => void;
  hasProject?: () => boolean;
  copy?: (text: string, event: MouseEvent, success: string) => void;
  /** Режим Докки (чат/поиск): картинки, видео и файлы сайта. */
  richEnabled?: () => boolean;
  /** Ссылка в браузерной панели рабочей области; false - панели нет, ссылка уходит в системный браузер. */
  openLink?: (href: string) => boolean;
}

export const markdownHooks: MarkdownHooks = {};

export const COPY_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>';

const desktopHost = () => Boolean(window.koddiDesktop);

export interface RenderOptions {
  message?: unknown;
  streaming?: boolean;
}

/** Разбирает Markdown в безопасные узлы: картинки только вложения проекта (и сайта Докки в чате), код с подсветкой и кнопкой «Копировать». */
export function renderMarkdown(target: HTMLElement, source: string, highlight = true, options: RenderOptions = {}): void {
  const rich = Boolean(markdownHooks.richEnabled?.());
  const parsed = marked.parse(rich ? richPrepare(source, { streaming: options.streaming }) : String(source ?? ''), { gfm: true, breaks: false, async: false }) as string;
  const fragment = DOMPurify.sanitize(rich ? richChecklist(parsed) : parsed, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['style', 'iframe', 'form', 'input'],
    FORBID_ATTR: ['style'],
    RETURN_DOM_FRAGMENT: true,
  }) as unknown as DocumentFragment;

  fragment.querySelectorAll('img').forEach((image) => {
    const src = image.getAttribute('src') ?? '';
    // картинки сайта и внешние адреса обрабатывает richHydrate, остальное не показывается
    if (rich && (src.startsWith('/__rich__/') || /^https?:\/\//i.test(src))) return;
    if (!/^\/api\/attachments\/\d+\/content$/.test(src)) image.remove();
  });
  target.replaceChildren(fragment);

  target.querySelectorAll('a').forEach((link) => {
    const href = link.getAttribute('href') ?? '';
    if (markdownHooks.hasProject?.() && href && !/^[a-z]+:|^\/\/|^#|^\/api\//i.test(href)) {
      link.onclick = (event) => {
        event.preventDefault();
        markdownHooks.openProjectFile?.(decodeURIComponent(href));
      };
      link.title = 'Открыть файл в проекте';
    } else if (desktopHost() && /^https?:\/\//i.test(href)) {
      link.onclick = (event) => {
        event.preventDefault();
        // в режиме кода ссылка открывается во вкладке браузера рабочей области, с ⌘/Ctrl - в системном браузере
        if (!(event.metaKey || event.ctrlKey) && markdownHooks.openLink?.(href)) return;
        window.location.href = `koddi://open-url?url=${encodeURIComponent(href)}`;
      };
    }
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
  });

  target.querySelectorAll('pre').forEach((pre) => {
    const code = pre.querySelector('code');
    if (!code) return;
    const language = /language-([^\s]+)/.exec(code.className)?.[1] ?? 'Код';
    if (highlight && hljs.getLanguage(language) && (code.textContent ?? '').length < 50_000) {
      try {
        code.innerHTML = hljs.highlight(code.textContent ?? '', { language, ignoreIllegals: true }).value;
      } catch {
        /* без подсветки */
      }
    }
    const container = document.createElement('div');
    container.className = 'code-block';
    const head = document.createElement('div');
    head.className = 'code-head';
    const label = document.createElement('span');
    label.textContent = language;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'icon-btn';
    button.title = 'Скопировать код';
    button.setAttribute('aria-label', 'Скопировать код');
    button.innerHTML = COPY_ICON;
    button.onclick = (event) => markdownHooks.copy?.(button.closest('.code-block')?.querySelector('pre code')?.textContent ?? '', event, 'Код скопирован');
    head.append(label, button);
    pre.replaceWith(container);
    container.append(head, pre);
  });

  target.querySelectorAll('table').forEach((table) => {
    const wrapper = document.createElement('div');
    wrapper.className = 'table-scroll';
    table.replaceWith(wrapper);
    wrapper.append(table);
  });
  if (rich) {
    richHydrate(target, options);
    void renderMath(target);
  }
}

/* Разметка ответов Докки: то, что сайт показывает в чате (картинки, видео, файлы, таблицы,
   цитаты и спойлеры), приведено к тому же виду. Правила разбора перенесены из md() сайта
   (mini_app/assets/app.js), показ картинок идёт через вложения API и защищённый прокси. */
const richVideoHosts = /^(?:www\.|m\.)?(?:youtube\.com|youtu\.be|vimeo\.com|rutube\.ru|vkvideo\.ru|tiktok\.com)$/;
function richUrl(value) {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url : null;
  } catch {
    return null;
  }
}
function richIsVideo(value) {
  const url = richUrl(value);
  return Boolean(url && (richVideoHosts.test(url.hostname.toLowerCase()) || /\.(?:mp4|webm|mov|m4v)$/i.test(url.pathname)));
}
function richYouTubeId(value) {
  const url = richUrl(value);
  if (!url) return "";
  let id = "";
  if (/^(?:www\.|m\.)?youtube\.com$/i.test(url.hostname)) id = url.searchParams.get("v") || url.pathname.split("/")[2] || "";
  else if (/^youtu\.be$/i.test(url.hostname)) id = url.pathname.slice(1).split("/")[0];
  return /^[A-Za-z0-9_-]{11}$/.test(id) ? id : "";
}
// адрес картинки для окна приложения: только через локальный прокси с проверкой адреса
const richMediaURL = (value) => `/api/dokky/media?url=${encodeURIComponent(value)}`;

// преобразования применяются только вне кода
function richOutsideCode(text, transform) {
  return text
    .split(/(```[\s\S]*?(?:```|$)|`[^`\n]*`)/g)
    .map((part, index) => (index % 2 ? part : transform(part)))
    .join("");
}
const richStrip = (value) => String(value || "").replace(/<[^>]*>/g, "").trim();

function richPrepare(source, { streaming = false } = {}) {
  let text = String(source || "").replace(/\r/g, "");
  if (streaming)
    // недописанная картинка не должна мелькать сырым текстом
    text = text.replace(/!\[[^\]\n]*$/, "").replace(/!\[[^\]\n]*\]\([^)\n]*$/, "");
  return richOutsideCode(text, (part) =>
    part
      .replace(/(\[[^\]]+\]\((https?:\/\/[^)\s]+)\))\s*\[↗\]\(\2\)/g, "$1")
      .replace(/<(tg-table|table)\b[^>]*>([\s\S]*?)<\/\1>/gi, (_, kind, body) => {
        const rows = [...body.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/gi)].map(([, row]) =>
          [...row.matchAll(/<t[dh]\b[^>]*>([\s\S]*?)<\/t[dh]>/gi)].map(([, cell]) => richStrip(cell)),
        );
        if (!rows.length) return body;
        return `\n\n| ${rows[0].join(" | ")} |\n|${rows[0].map(() => " --- ").join("|")}|\n${rows.slice(1).map((row) => `| ${row.join(" | ")} |`).join("\n")}\n\n`;
      })
      .replace(/<figure>([\s\S]*?)<\/figure>/gi, (_, body) => {
        const media = body.match(/<(img|video|audio|tg-document)\b[^>]*src=["'](https?:\/\/[^"']+|tg:\/\/photo\?id=[A-Za-z0-9_-]+)["'][^>]*\/?\s*>/i);
        const caption = body.match(/<figcaption>([\s\S]*?)<\/figcaption>/i);
        if (!media) return body;
        return `![${richStrip(caption?.[1]) || "Изображение"}](${media[2]})`;
      })
      .replace(/<\/?tg-(?:slideshow|collage|table)\b[^>]*>/gi, "")
      .replace(/<tg-quote\b[^>]*>([\s\S]*?)<\/tg-quote>/gi, (_, body) =>
        body.split(/<br\s*\/?\s*>|\n/i).map((line) => `> ${line}`).join("\n"),
      )
      .replace(/<tg-thinking\b[^>]*>[\s\S]*?<\/tg-thinking>/gi, "")
      .replace(/<img\b([^>]*)\/?>/gi, (_, attrs) => {
        const url = attrs.match(/\bsrc=["']([^"']+)["']/i)?.[1];
        const alt = attrs.match(/\balt=["']([^"']*)["']/i)?.[1] || "Изображение";
        return url && /^(?:https?:\/\/|tg:\/\/photo\?id=)/i.test(url) ? `![${alt}](${url})` : alt;
      })
      .replace(/<(video|audio)\b[^>]*src=["'](https?:\/\/[^"']+)["'][^>]*>([\s\S]*?)<\/\1>/gi, (_, kind, url, label) =>
        `![${richStrip(label) || (kind.toLowerCase() === "video" ? "Видео" : "Аудио")}](${url})`,
      )
      .replace(/<tg-document\b[^>]*src=["'](https?:\/\/[^"']+)["'][^>]*>[\s\S]*?<\/tg-document>/gi, "![Файл]($1)")
      .replace(/<tg-map\b[^>]*lat=["'](-?\d+(?:\.\d+)?)["'][^>]*long=["'](-?\d+(?:\.\d+)?)["'][^>]*\/?>/gi, (_, lat, lon) =>
        `[Открыть карту](https://maps.google.com/?q=${lat},${lon})`,
      )
      .replace(/<a\s+href=["']((?:https?:\/\/|mailto:|tel:)[^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi, (_, url, label) => `[${label}](${url})`)
      .replace(/<h([1-6])>([\s\S]*?)<\/h\1>/gi, (_, level, label) => `\n\n${"#".repeat(Number(level))} ${label}\n\n`)
      .replace(/<hr\s*\/?>/gi, "\n\n---\n\n")
      .replace(/<p>([\s\S]*?)<\/p>/gi, "$1\n\n")
      .replace(/<tg-time\b[^>]*>([\s\S]*?)<\/tg-time>/gi, "$1")
      .replace(/<tg-emoji\b[^>]*>([\s\S]*?)<\/tg-emoji>/gi, "$1")
      .replace(/<blockquote expandable>([\s\S]*?)<\/blockquote>/gi, (_, body) => `<details><summary>Цитата</summary>${body}</details>`)
      .replace(/<blockquote>([\s\S]*?)<\/blockquote>/gi, (_, body) =>
        body.split(/<br\s*\/?\s*>/i).map((line) => `> ${line}`).join("\n"),
      )
      .replace(/<tg-button-row[^>]*>([\s\S]*?)<\/tg-button-row>/gi, (_, row) =>
        [...row.matchAll(/<tg-button\b([^>]*)>([\s\S]*?)<\/tg-button>/gi)]
          .map(([, attrs, label]) => {
            const url = attrs.match(/\burl=["'](https?:\/\/[^"']+|mailto:[^"']+|tel:[^"']+)["']/i)?.[1];
            return url ? `[${richStrip(label)}](${url})` : richStrip(label);
          })
          .filter(Boolean)
          .join(" · "),
      )
      // вложенный markdown внутри details разбирается как обычный
      .replace(/<details(\s+open)?>\s*(<summary>[\s\S]*?<\/summary>)?([\s\S]*?)<\/details>/gi, (_, open, summary, body) =>
        `\n<details${open ? " open" : ""}>${summary || "<summary>Подробнее</summary>"}\n\n${body.trim()}\n\n</details>\n`,
      )
      .replace(/<tg-spoiler>([\s\S]*?)<\/tg-spoiler>/gi, '<span class="rich-spoiler">$1</span>')
      .replace(/<tg-reference>([\s\S]*?)<\/tg-reference>/gi, '<span class="rich-reference">$1</span>')
      .replace(/<(\/?)(?:ins)>/gi, "<$1u>")
      .replace(/<(\/?)(?:strike|s)>/gi, "<$1del>")
      .replace(/\|\|([^|\n]+)\|\|/g, '<span class="rich-spoiler">$1</span>')
      .replace(/(?<!=)==([^=\n]+)==(?!=)/g, "<mark>$1</mark>")
      .replace(/\]\(<(https?:\/\/[^<>\s]+)>\)/g, (_, url) => `](${url.replaceAll("(", "%28").replaceAll(")", "%29")})`)
      // картинки: свои - по вложениям, видео и файлы - ссылками, чужие остаются картинками
      .replace(/!\[([^\]]*)\]\(tg:\/\/photo\?id=([A-Za-z0-9_-]+)\)/g, (_, alt, id) =>
        streaming ? "" : `![${alt}](/__rich__/${id})`,
      )
      .replace(/!\[([^\]]*)\]\(tg:\/\/(?:emoji|time)\?[^\s)]*\)/g, "$1")
      .replace(/!\[([^\]]*)\]\(((?:https?:\/\/)[^\s)]+)(?:\s+"([^"]*)")?\)/g, (match, alt, url, title) => {
        const label = title || alt;
        const path = (url.split(/[?#]/)[0] || "").toLowerCase();
        if (richIsVideo(url)) return `[${label || "Видео"}](${url})`;
        if (/\.(mp3|m4a|wav|ogg|oga|opus)$/.test(path)) return `[${label || "Аудио"}](${url})`;
        if (/\.(zip|pdf|docx?|pptx?|xlsx?|csv|txt|epub)$/.test(path))
          return `[${label || path.split("/").pop() || "Файл"}](${url} "rich:file")`;
        return match;
      })
      // формулы показываются как текст формулы: набор KaTeX в приложение не входит
      .replace(/\$\$([\s\S]+?)\$\$/g, (_, formula) => `\n\n<div class="rich-math" role="math">${richEscape(formula.trim())}</div>\n\n`)
      .replace(/\\\[([\s\S]+?)\\\]/g, (_, formula) => `\n\n<div class="rich-math" role="math">${richEscape(formula.trim())}</div>\n\n`)
      .replace(/\\\(([^\n]+?)\\\)/g, (_, formula) => `<span class="rich-math-inline" role="math">${richEscape(formula)}</span>`),
  );
}
const richEscape = (value) =>
  String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function richChecklist(html) {
  return html.replace(/<input\b[^>]*type="checkbox"[^>]*>/gi, (tag) => (/\bchecked\b/i.test(tag) ? "☑ " : "☐ "));
}

function richOpen(link) {
  if (!window.koddiDesktop) return;
  link.onclick = (event) => {
    event.preventDefault();
    window.location.href = `koddi://open-url?url=${encodeURIComponent(link.href)}`;
  };
}

function richVideoCard(url, label) {
  const id = richYouTubeId(url);
  const card = document.createElement("a");
  card.href = url;
  card.target = "_blank";
  card.rel = "noopener noreferrer";
  card.className = id ? "rich-video-card" : "rich-video-link";
  const text = !label || label === url || label === "↗" ? (id ? "Видео на YouTube" : "Открыть видео") : label;
  if (!id) {
    card.textContent = text;
    richOpen(card);
    return card;
  }
  card.dataset.videoId = id;
  const thumb = document.createElement("span");
  thumb.className = "rich-video-thumbnail";
  const image = document.createElement("img");
  image.alt = "";
  image.loading = "lazy";
  image.decoding = "async";
  image.referrerPolicy = "no-referrer";
  image.src = richMediaURL(`https://i.ytimg.com/vi/${id}/hqdefault.jpg`);
  image.onerror = () => image.remove();
  const play = document.createElement("span");
  play.className = "rich-video-play";
  play.setAttribute("aria-hidden", "true");
  play.textContent = "▶";
  thumb.append(image, play);
  const copy = document.createElement("span");
  copy.className = "rich-video-copy";
  const strong = document.createElement("strong");
  const title = document.createElement("span");
  title.className = "link-label rich-video-title";
  title.textContent = text;
  strong.append(title);
  const site = document.createElement("small");
  site.textContent = "YouTube";
  copy.append(strong, site);
  card.append(thumb, copy);
  richOpen(card);
  return card;
}

// какое вложение сообщения соответствует картинке в тексте
function richArtifactFor(message, id, alt, used) {
  const images = (message?.artifacts || []).filter(
    (item) => (item.type === "image" || String(item.media_type || "").startsWith("image/")) && !used.has(item.file_id || item.id),
  );
  return (
    images.find((item) => [item.id, item.file_id].includes(id)) ||
    images.find((item) => alt && item.name === alt) ||
    images[0] ||
    null
  );
}

function richImage(image, source, file, alt) {
  image.className = "message-image rich-inline-image";
  image.alt = alt || "Изображение";
  image.loading = "lazy";
  image.decoding = "async";
  image.referrerPolicy = "no-referrer";
  image.src = source;
  image.removeAttribute("width");
  image.removeAttribute("height");
  image.onclick = () => openCloudImage(file, image);
}

function richHydrate(root, { message = null, streaming = false } = {}) {
  const used = new Set();
  if (message) message._richUsed = used;
  for (const image of [...root.querySelectorAll("img")]) {
    const src = image.getAttribute("src") || "";
    const alt = image.getAttribute("alt") || "";
    if (src.startsWith("/__rich__/")) {
      const file = richArtifactFor(message, src.slice("/__rich__/".length), alt, used);
      const url = file && cloudFileURL(file);
      if (!url) {
        if (!streaming) image.replaceWith(document.createTextNode(alt || "Изображение"));
        else image.remove();
        continue;
      }
      used.add(file.file_id || file.id);
      richImage(image, url, file, alt || file.name);
    } else if (/^https?:\/\//i.test(src) && richUrl(src)) {
      const viewer = richMediaURL(src);
      richImage(image, viewer, { name: alt || "Изображение", viewer_url: viewer, source_url: src }, alt);
      image.dataset.sourceUrl = src;
      // если картинка не загрузилась, остаётся понятная ссылка на источник
      image.onerror = () => {
        const link = document.createElement("a");
        link.href = src;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = alt || "Открыть изображение";
        richOpen(link);
        image.replaceWith(link);
      };
    }
  }
  for (const link of [...root.querySelectorAll("a[href]")]) {
    if (link.closest(".code-head,.ai-source-chip") || link.classList.contains("rich-video-card")) continue;
    const href = link.getAttribute("href") || "";
    if (!/^https?:\/\//i.test(href)) continue;
    const label = link.textContent.trim();
    if (richIsVideo(href)) {
      const card = richVideoCard(href, label);
      const parent = link.parentElement;
      if (parent?.matches("p") && parent.textContent.trim() === label) parent.replaceWith(card);
      else link.replaceWith(card);
      continue;
    }
    if (link.getAttribute("title") === "rich:file") {
      link.removeAttribute("title");
      link.classList.add("message-file");
      link.insertAdjacentHTML("afterbegin", icon("file"));
      continue;
    }
    if (label === "↗") {
      link.textContent = "Ссылка";
    } else if (/\s*↗\s*$/.test(label) && !link.children.length) link.textContent = label.replace(/\s*↗\s*$/, "");
    if (!link.querySelector(".link-label")) {
      const span = document.createElement("span");
      span.className = "link-label";
      span.append(...link.childNodes);
      link.append(span);
    }
  }
}
document.addEventListener("click", (event) => {
  const spoiler = event.target.closest?.(".rich-spoiler");
  if (spoiler) spoiler.classList.toggle("revealed");
});

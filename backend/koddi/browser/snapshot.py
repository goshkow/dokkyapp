"""Скрипты, которые выполняются внутри страницы: текстовый снимок с ссылками на элементы и разбор вёрстки.

Модель не видит картинку надёжно, поэтому страница описывается текстом: у каждого интерактивного элемента есть ref,
по нему же потом идут click/type/hover. Ссылки лежат в window.__koddiRefs и живут до следующей навигации.
"""

from __future__ import annotations

SNAPSHOT_JS = r"""
(options) => {
  const maxChars = options.maxChars || 20000;
  const refs = [];
  window.__koddiRefs = refs;
  const lines = [];
  let size = 0;
  let truncated = false;
  const push = (line) => {
    if (truncated) return;
    if (size + line.length > maxChars) { truncated = true; return; }
    lines.push(line); size += line.length + 1;
  };
  const clip = (text, n) => {
    text = (text || '').replace(/\s+/g, ' ').trim();
    return text.length > n ? text.slice(0, n - 1) + '…' : text;
  };
  const vw = window.innerWidth, vh = window.innerHeight;
  const visible = (el) => {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return false;
    if (el.hidden || el.getAttribute('aria-hidden') === 'true') return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 || rect.height > 0;
  };
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'HEAD', 'META', 'LINK', 'TITLE', 'SVG', 'PATH', 'CANVAS']);
  const nameOf = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return clip(aria, 80);
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const text = by.split(/\s+/).map((id) => { const n = document.getElementById(id); return n ? n.textContent : ''; }).join(' ');
      if (text.trim()) return clip(text, 80);
    }
    if (el.labels && el.labels.length) return clip(Array.from(el.labels).map((l) => l.textContent).join(' '), 80);
    const tag = el.tagName.toLowerCase();
    if (tag === 'input' && ['button', 'submit', 'reset'].includes(el.type)) return clip(el.value, 80);
    if (tag === 'img') return clip(el.alt, 80);
    const inner = clip(el.innerText || el.textContent, 80);
    if (inner) return inner;
    const img = el.querySelector && el.querySelector('img[alt], svg title');
    if (img) return clip(img.alt || img.textContent, 80);
    return clip(el.title || el.getAttribute('placeholder') || el.getAttribute('name') || '', 80);
  };
  const roleOf = (el, parentPointer) => {
    const tag = el.tagName.toLowerCase();
    const explicit = el.getAttribute('role');
    if (explicit && ['button', 'link', 'tab', 'menuitem', 'checkbox', 'radio', 'switch', 'textbox', 'combobox', 'slider', 'option', 'searchbox'].includes(explicit)) return explicit;
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'button' || tag === 'summary') return 'button';
    if (tag === 'input') {
      const t = (el.type || 'text').toLowerCase();
      if (t === 'hidden') return null;
      if (['button', 'submit', 'reset', 'image'].includes(t)) return 'button';
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      if (t === 'range') return 'slider';
      return 'textbox';
    }
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return 'combobox';
    if (el.isContentEditable && el.parentElement && !el.parentElement.isContentEditable) return 'textbox';
    if (el.hasAttribute('onclick') || (el.tabIndex >= 0 && tag !== 'body')) return 'clickable';
    if (!parentPointer && getComputedStyle(el).cursor === 'pointer') return 'clickable';
    return null;
  };
  const attrsOf = (el, role) => {
    const out = [];
    const tag = el.tagName.toLowerCase();
    if (role === 'link') out.push('href=' + clip(el.getAttribute('href'), 100));
    if (role === 'textbox' || role === 'combobox' || role === 'slider') {
      let value = el.value !== undefined ? el.value : el.textContent;
      if (el.type === 'password') value = value ? '•••' : '';
      out.push('value="' + clip(value, 60) + '"');
    }
    if (tag === 'select') out.push('selected="' + clip(el.selectedOptions && el.selectedOptions[0] ? el.selectedOptions[0].textContent : '', 40) + '"');
    if (tag === 'input' && el.placeholder) out.push('placeholder="' + clip(el.placeholder, 40) + '"');
    if (tag === 'input' && el.type && !['text', 'submit', 'button'].includes(el.type)) out.push('type=' + el.type);
    if (role === 'checkbox' || role === 'radio' || role === 'switch') out.push(el.checked || el.getAttribute('aria-checked') === 'true' ? 'checked' : 'unchecked');
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') out.push('disabled');
    if (el.required) out.push('required');
    if (el.getAttribute('aria-expanded')) out.push('expanded=' + el.getAttribute('aria-expanded'));
    if (el.getAttribute('aria-selected') === 'true' || el.getAttribute('aria-current')) out.push('current');
    const rect = el.getBoundingClientRect();
    if (rect.bottom < 0 || rect.top > vh || rect.right < 0 || rect.left > vw) out.push('offscreen');
    return out.join(' ');
  };
  const walk = (node, depth, parentPointer) => {
    if (truncated || depth > 40) return;
    if (node.nodeType === Node.TEXT_NODE) {
      const text = clip(node.textContent, 200);
      if (text) push('  '.repeat(Math.min(depth, 6)) + text);
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const el = node;
    if (SKIP.has(el.tagName.toUpperCase())) return;
    if (!visible(el)) return;
    const pad = '  '.repeat(Math.min(depth, 6));
    const tag = el.tagName.toLowerCase();
    const pointer = getComputedStyle(el).cursor === 'pointer';
    const role = roleOf(el, parentPointer);
    if (role) {
      refs.push(el);
      const attrs = attrsOf(el, role);
      push(pad + '[e' + (refs.length - 1) + '] ' + role + ' "' + nameOf(el) + '"' + (attrs ? ' ' + attrs : ''));
      return;
    }
    if (/^h[1-6]$/.test(tag)) {
      push(pad + 'heading' + tag[1] + ' "' + clip(el.innerText || el.textContent, 120) + '"');
      return;
    }
    if (tag === 'img') {
      push(pad + 'image "' + clip(el.alt || el.getAttribute('src') || '', 80) + '"');
      return;
    }
    if (tag === 'iframe') {
      let inner = null;
      try { inner = el.contentDocument; } catch (e) { inner = null; }
      push(pad + 'iframe src=' + clip(el.getAttribute('src'), 100) + (inner ? '' : ' (недоступен)'));
      if (inner && inner.body) walk(inner.body, depth + 1, false);
      return;
    }
    const landmark = { nav: 'navigation', main: 'main', header: 'header', footer: 'footer', aside: 'aside', form: 'form', dialog: 'dialog', table: 'table', ul: 'list', ol: 'list' }[tag];
    let next = depth;
    if (landmark) { push(pad + landmark + ':'); next = depth + 1; }
    if (el.shadowRoot) Array.from(el.shadowRoot.childNodes).forEach((child) => walk(child, next, pointer));
    Array.from(el.childNodes).forEach((child) => walk(child, next, pointer));
  };
  walk(document.body || document.documentElement, 0, false);
  const doc = document.scrollingElement || document.documentElement;
  return {
    url: location.href,
    title: document.title,
    viewport: { width: vw, height: vh },
    scroll: { y: Math.round(doc.scrollTop), height: doc.scrollHeight },
    refs: refs.length,
    truncated,
    text: lines.join('\n'),
  };
}
"""

INSPECT_JS = r"""
(el) => {
  const rect = el.getBoundingClientRect();
  const style = getComputedStyle(el);
  const names = ['display', 'position', 'width', 'height', 'margin', 'padding', 'border', 'color', 'background-color',
    'font-family', 'font-size', 'font-weight', 'line-height', 'text-align', 'overflow', 'z-index', 'opacity',
    'flex-direction', 'justify-content', 'align-items', 'gap', 'grid-template-columns', 'transform', 'visibility', 'cursor'];
  const styles = {};
  for (const name of names) {
    const value = style.getPropertyValue(name);
    if (value && value !== 'auto' && value !== 'normal' && value !== 'none' && value !== '0px' && value !== 'static') styles[name] = value;
  }
  const attributes = {};
  for (const attr of el.attributes) attributes[attr.name] = attr.value.length > 200 ? attr.value.slice(0, 200) + '…' : attr.value;
  const parent = el.parentElement;
  const parentRect = parent ? parent.getBoundingClientRect() : null;
  return {
    tag: el.tagName.toLowerCase(),
    attributes,
    text: (el.innerText || '').slice(0, 300),
    box: { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) },
    clipped: !!parentRect && (rect.right > parentRect.right + 1 || rect.bottom > parentRect.bottom + 1 || rect.left < parentRect.left - 1),
    children: el.children.length,
    scroll: { top: el.scrollTop, height: el.scrollHeight, clientHeight: el.clientHeight, width: el.scrollWidth, clientWidth: el.clientWidth },
    styles,
  };
}
"""

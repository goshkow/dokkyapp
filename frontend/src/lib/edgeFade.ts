/**
 * Широкие блоки (таблицы, код, формулы) прокручиваются внутри колонки чата. Вместо резкой обрезки край, за который
 * уходит содержимое, плавно растворяется: сила затухания растёт вместе со сдвигом, а у самого края без «лишнего»
 * содержимого градиента нет (стили - styles/blocks.css, [data-edge-fade]).
 */
const SELECTOR = '.table-scroll, .code-block pre, .rich-math, .message-body > table';
const MAX_FADE = 36;

function update(node: HTMLElement): void {
  const room = node.scrollWidth - node.clientWidth;
  if (room < 2) {
    if (node.hasAttribute('data-edge-fade')) {
      node.removeAttribute('data-edge-fade');
      node.style.removeProperty('--fade-l');
      node.style.removeProperty('--fade-r');
    }
    return;
  }
  const left = Math.min(MAX_FADE, Math.max(0, node.scrollLeft));
  const right = Math.min(MAX_FADE, Math.max(0, room - node.scrollLeft));
  node.setAttribute('data-edge-fade', '');
  node.style.setProperty('--fade-l', `${left.toFixed(1)}px`);
  node.style.setProperty('--fade-r', `${right.toFixed(1)}px`);
}

export function initEdgeFade(): void {
  let frame = 0;
  const scan = () => {
    frame = 0;
    document.querySelectorAll<HTMLElement>(SELECTOR).forEach(update);
  };
  const schedule = () => { if (!frame) frame = requestAnimationFrame(scan); };
  // scroll не всплывает, но ловится на стадии перехвата
  document.addEventListener('scroll', (event) => {
    const node = event.target;
    if (node instanceof HTMLElement && node.matches(SELECTOR)) update(node);
  }, { capture: true, passive: true });
  window.addEventListener('resize', schedule);
  new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
  new ResizeObserver(schedule).observe(document.body);
  schedule();
}

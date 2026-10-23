/* Своя полоса прокрутки поверх содержимого: не занимает места, не накрывается затемнением краёв ленты,
   появляется при прокрутке и наведении. Элементу достаточно атрибута data-overlay-scroll. */

interface Instance {
  element: HTMLElement;
  thumb: HTMLDivElement;
  update(): void;
  destroy(): void;
}

const instances = new Map<HTMLElement, Instance>();
let scheduled = 0;

/** Рабочая область полосы: у ленты чата верх и низ закрыты шапкой и полем ввода. */
function track(element: HTMLElement): { top: number; bottom: number; right: number } {
  const box = element.getBoundingClientRect();
  let top = box.top;
  let bottom = box.bottom;
  if (element.dataset.overlayScroll === 'feed') {
    const main = element.closest('main');
    const read = (name: string) => parseFloat(getComputedStyle(main ?? element).getPropertyValue(name)) || 0;
    top += Math.max(0, read('--feed-top') - 20);
    bottom -= Math.max(0, read('--feed-bottom') - 20);
  }
  // полоса ленты стоит у правого края окна: с открытой правой панелью она остаётся правее неё
  const right = element.dataset.overlayScroll === 'feed' ? Math.max(box.right, document.getElementById('shell')?.getBoundingClientRect().right ?? box.right) : box.right;
  return { top: top + 4, bottom: bottom - 4, right };
}

function attach(element: HTMLElement): Instance {
  const thumb = document.createElement('div');
  thumb.className = 'overlay-thumb';
  thumb.setAttribute('aria-hidden', 'true');
  document.body.append(thumb);
  let hideTimer = 0;
  let dragging = false;
  let hover = false;

  const show = () => {
    thumb.classList.add('visible');
    clearTimeout(hideTimer);
    if (!dragging && !hover) hideTimer = window.setTimeout(() => thumb.classList.remove('visible'), 1000);
  };

  const update = () => {
    const overflow = element.scrollHeight - element.clientHeight;
    const area = track(element);
    const length = area.bottom - area.top;
    if (overflow <= 1 || length <= 24 || !element.isConnected || element.offsetParent === null) {
      thumb.style.display = 'none';
      return;
    }
    const size = Math.max(32, (length * element.clientHeight) / element.scrollHeight);
    const offset = (element.scrollTop / overflow) * (length - size);
    thumb.style.display = 'block';
    thumb.style.height = `${size}px`;
    thumb.style.transform = `translateY(${area.top + offset}px)`;
    thumb.style.left = `${area.right - 7}px`;
  };

  const onScroll = () => {
    update();
    show();
  };
  const onEnter = () => show();
  const onLeave = () => {
    if (!dragging) hideTimer = window.setTimeout(() => thumb.classList.remove('visible'), 600);
  };
  // у правого края ползунок показывается и без прокрутки
  const onMove = (event: PointerEvent) => {
    const edge = track(element).right;
    if (edge - event.clientX < 22 && event.clientY > element.getBoundingClientRect().top) show();
  };
  element.addEventListener('scroll', onScroll, { passive: true });
  element.addEventListener('pointerenter', onEnter);
  element.addEventListener('pointerleave', onLeave);
  const moveTarget = element.dataset.overlayScroll === 'feed' ? document : element;
  moveTarget.addEventListener('pointermove', onMove as EventListener, { passive: true });

  thumb.addEventListener('pointerenter', () => { hover = true; thumb.classList.add('visible', 'hover'); clearTimeout(hideTimer); });
  thumb.addEventListener('pointerleave', () => { hover = false; thumb.classList.remove('hover'); if (!dragging) show(); });
  thumb.addEventListener('pointerdown', (event) => {
    event.preventDefault();
    dragging = true;
    thumb.setPointerCapture(event.pointerId);
    thumb.classList.add('dragging', 'visible');
    const startY = event.clientY;
    const startTop = element.scrollTop;
    const area = track(element);
    const size = thumb.offsetHeight;
    const ratio = (element.scrollHeight - element.clientHeight) / Math.max(1, area.bottom - area.top - size);
    const move = (next: PointerEvent) => { element.scrollTop = startTop + (next.clientY - startY) * ratio; };
    const up = () => {
      dragging = false;
      thumb.classList.remove('dragging');
      thumb.removeEventListener('pointermove', move);
      thumb.removeEventListener('pointerup', up);
      thumb.removeEventListener('pointercancel', up);
      show();
    };
    thumb.addEventListener('pointermove', move);
    thumb.addEventListener('pointerup', up);
    thumb.addEventListener('pointercancel', up);
  });

  const resize = new ResizeObserver(update);
  resize.observe(element);
  for (const child of element.children) resize.observe(child);
  const children = new MutationObserver(() => {
    for (const child of element.children) resize.observe(child);
    update();
  });
  children.observe(element, { childList: true });
  update();
  return {
    element,
    thumb,
    update,
    destroy() {
      element.removeEventListener('scroll', onScroll);
      element.removeEventListener('pointerenter', onEnter);
      element.removeEventListener('pointerleave', onLeave);
      moveTarget.removeEventListener('pointermove', onMove as EventListener);
      resize.disconnect();
      children.disconnect();
      clearTimeout(hideTimer);
      thumb.remove();
    },
  };
}

function sync(): void {
  scheduled = 0;
  const wanted = new Set(document.querySelectorAll<HTMLElement>('[data-overlay-scroll]'));
  for (const [element, instance] of instances) {
    if (!wanted.has(element) || !element.isConnected) {
      instance.destroy();
      instances.delete(element);
    }
  }
  for (const element of wanted) if (!instances.has(element)) instances.set(element, attach(element));
  for (const instance of instances.values()) instance.update();
}

export function initOverlayScroll(): void {
  const schedule = () => { if (!scheduled) scheduled = requestAnimationFrame(sync); };
  new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-overlay-scroll', 'class', 'hidden'] });
  window.addEventListener('resize', schedule, { passive: true });
  schedule();
}

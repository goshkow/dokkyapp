import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { nearest, onKeyDown } from './keyNav';
import { ui } from './ui';

const box = (left: number, top: number, width = 40, height = 30) => ({ left, top, width, height, right: left + width, bottom: top + height });

describe('ближайший пункт по стрелке', () => {
  // ряд плиток разделов 3 x 2 и список чатов под ним
  const items = [
    { item: 'a1', box: box(0, 0) }, { item: 'a2', box: box(50, 0) }, { item: 'a3', box: box(100, 0) },
    { item: 'b1', box: box(0, 40) }, { item: 'b2', box: box(50, 40) },
    { item: 'chat1', box: box(0, 90, 200, 32) }, { item: 'more1', box: box(205, 92, 24, 24) }, { item: 'chat2', box: box(0, 124, 200, 32) },
  ];
  const from = (name: string) => items.find((entry) => entry.item === name)!.box;
  const go = (name: string, direction: Parameters<typeof nearest>[2]) => nearest(from(name), items.filter((entry) => entry.item !== name), direction);

  it('в сетке вниз и вбок - соседняя плитка', () => {
    expect(go('a2', 'down')).toBe('b2');
    expect(go('a2', 'right')).toBe('a3');
    expect(go('b1', 'up')).toBe('a1');
    expect(go('a1', 'left')).toBeUndefined();
  });

  it('из сетки вниз - список, в списке - следующая строка, вправо - кнопка строки', () => {
    expect(go('b2', 'down')).toBe('chat1');
    expect(go('chat1', 'down')).toBe('chat2');
    expect(go('chat1', 'right')).toBe('more1');
    expect(go('chat2', 'up')).toBe('chat1');
  });
});

describe('Esc и стрелки', () => {
  let rects: Map<Element, ReturnType<typeof box>>;
  beforeEach(() => {
    rects = new Map();
    vi.spyOn(HTMLElement.prototype, 'getClientRects').mockImplementation(function (this: HTMLElement) {
      return (rects.has(this) ? [rects.get(this)] : []) as unknown as DOMRectList;
    });
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return (rects.get(this) ?? box(0, 0, 0, 0)) as DOMRect;
    });
    ui.set({ leftOpen: true });
    document.body.innerHTML = `
      <aside id="left"><div class="sidebar-panel">
        <button id="nav">Поиск</button>
        <div class="chat-row"><button class="chat-item" id="c1">Первый</button></div>
        <div class="chat-row" data-selected><button class="chat-item" id="c2">Открытый</button></div>
      </div></aside>
      <main id="main"><button id="page">Кнопка страницы</button><dialog id="dlg"></dialog></main>`;
    const place = (id: string, value: ReturnType<typeof box>) => rects.set(document.getElementById(id)!, value);
    place('nav', box(0, 0)); place('c1', box(0, 40, 200, 30)); place('c2', box(0, 72, 200, 30)); place('page', box(300, 0));
  });
  afterEach(() => { vi.restoreAllMocks(); document.body.innerHTML = ''; delete document.documentElement.dataset.input; });

  const press = (target: Element, key: string) => {
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
    target.dispatchEvent(event);
    onKeyDown(Object.defineProperty(event, 'target', { value: target }) as KeyboardEvent);
    return event;
  };

  it('Esc уводит в меню на открытый чат и включает кольцо фокуса', () => {
    press(document.body, 'Escape');
    expect(document.activeElement?.id).toBe('c2');
    expect(document.documentElement.dataset.input).toBe('keyboard');
  });

  it('стрелки ходят по меню, Home и End - к краям', () => {
    const c2 = document.getElementById('c2')!;
    c2.focus();
    expect(press(c2, 'ArrowUp').defaultPrevented).toBe(true);
    expect(document.activeElement?.id).toBe('c1');
    press(document.getElementById('c1')!, 'Home');
    expect(document.activeElement?.id).toBe('nav');
    press(document.getElementById('nav')!, 'End');
    expect(document.activeElement?.id).toBe('c2');
  });

  it('Esc при открытом окне его не перехватывает, вне меню стрелки не трогаются', () => {
    document.getElementById('dlg')!.setAttribute('open', '');
    expect(press(document.body, 'Escape').defaultPrevented).toBe(false);
    expect(document.activeElement).toBe(document.body);
    const page = document.getElementById('page')!;
    expect(press(page, 'ArrowDown').defaultPrevented).toBe(false);
  });
});

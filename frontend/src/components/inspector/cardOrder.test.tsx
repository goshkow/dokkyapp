import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { data } from '../../state/data';
import { defaultSettings } from '../../lib/types';
import { ui } from '../../state/ui';
import { CARD_ORDER, normalizeOrder, placeCard, useCardOrder, type CardName } from './cardOrder';

const save = vi.hoisted(() => vi.fn());
vi.mock('../../state/remote', () => ({ saveInspectorOrder: save }));

function Panel({ hidden = () => false }: { hidden?: (name: CardName) => boolean }) {
  const reorder = useCardOrder(hidden);
  return (
    <div ref={reorder.box} className="inspector-cards" data-testid="box" {...reorder.handlers}>
      {reorder.order.map((name) => (
        <section key={name} className="inspector-card" data-card={name}>
          <button type="button" className="card-head">{name}</button>
        </section>
      ))}
    </div>
  );
}

const shown = () => screen.getAllByRole('button').map((node) => node.textContent);

beforeEach(() => {
  save.mockReset().mockImplementation(async (order: string[]) => {
    data.set({ settings: { ...data.get().settings, inspector_order: order } });
  });
  data.set({ settings: { ...defaultSettings } });
  // в jsdom нет element.animate
  ui.set({ motion: 'reduced' });
});
afterEach(cleanup);

describe('порядок блоков правой панели', () => {
  it('отбрасывает неизвестное и повторы, новые блоки ставит рядом с соседями', () => {
    expect(normalizeOrder(undefined)).toEqual([...CARD_ORDER]);
    expect(normalizeOrder(['plan', 'plan', 'nope', 3, 'diff'])).toEqual(['environment', 'context', 'plan', 'diff', 'files', 'map', 'quality', 'browser']);
  });

  it('ставит блок среди видимых, скрытые остаются на своих местах', () => {
    const order = normalizeOrder([]);
    const visible = (name: CardName) => name !== 'environment' && name !== 'context';
    expect(placeCard(order, 'plan', 0, visible)).toEqual(['environment', 'context', 'plan', 'diff', 'files', 'map', 'quality', 'browser']);
    expect(placeCard(order, 'diff', 99, visible)).toEqual(['environment', 'context', 'files', 'map', 'quality', 'browser', 'plan', 'diff']);
  });

  it('Alt+стрелка двигает блок, сохраняет порядок и оставляет фокус на заголовке', async () => {
    render(<Panel />);
    const head = screen.getByText('files');
    head.focus();
    await act(async () => { fireEvent.keyDown(head, { key: 'ArrowUp', altKey: true }); });
    expect(shown()).toEqual(['environment', 'context', 'files', 'diff', 'map', 'quality', 'browser', 'plan']);
    expect(save).toHaveBeenCalledWith(['environment', 'context', 'files', 'diff', 'map', 'quality', 'browser', 'plan']);
    expect(document.activeElement).toBe(screen.getByText('files'));
    // первый видимый блок выше не поднимается, стрелка без Alt ничего не меняет
    fireEvent.keyDown(screen.getByText('environment'), { key: 'ArrowUp', altKey: true });
    fireEvent.keyDown(screen.getByText('plan'), { key: 'ArrowUp' });
    expect(save).toHaveBeenCalledTimes(1);
  });

  it('при ошибке сохранения порядок возвращается к сохранённому', async () => {
    save.mockRejectedValueOnce(new Error('нет связи'));
    render(<Panel />);
    await act(async () => { fireEvent.keyDown(screen.getByText('plan'), { key: 'ArrowUp', altKey: true }); });
    expect(shown()).toEqual([...CARD_ORDER]);
  });

  it('перетаскивание за заголовок переставляет блок, а клик после него не срабатывает', async () => {
    render(<Panel />);
    const box = screen.getByTestId('box');
    box.setPointerCapture = vi.fn();
    // в jsdom нет раскладки: задаём каждому блоку высоту 40 и шаг 48
    for (const node of box.querySelectorAll<HTMLElement>('.inspector-card')) {
      Object.defineProperty(node, 'offsetHeight', { configurable: true, value: 40 });
      Object.defineProperty(node, 'offsetTop', { configurable: true, get: () => [...box.children].indexOf(node) * 48 });
      node.getBoundingClientRect = () => ({ top: node.offsetTop } as DOMRect);
    }
    const click = vi.fn();
    box.addEventListener('click', click);
    const diff = screen.getByText('diff');
    fireEvent.pointerDown(diff, { button: 0, clientY: 2 * 48 + 10, pointerId: 1 });
    fireEvent.pointerMove(box, { clientY: 2 * 48 + 12, pointerId: 1 });
    expect(shown()).toEqual([...CARD_ORDER]);
    fireEvent.pointerMove(box, { clientY: 4 * 48 + 20, pointerId: 1 });
    expect(shown()).toEqual(['environment', 'context', 'files', 'map', 'diff', 'quality', 'browser', 'plan']);
    await act(async () => { fireEvent.pointerUp(box, { pointerId: 1 }); });
    fireEvent.click(diff);
    expect(click).not.toHaveBeenCalled();
    expect(save).toHaveBeenCalledWith(['environment', 'context', 'files', 'map', 'diff', 'quality', 'browser', 'plan']);
  });

  it('Esc отменяет перетаскивание без сохранения', async () => {
    render(<Panel />);
    const box = screen.getByTestId('box');
    box.setPointerCapture = vi.fn();
    for (const node of box.querySelectorAll<HTMLElement>('.inspector-card')) {
      Object.defineProperty(node, 'offsetHeight', { configurable: true, value: 40 });
      Object.defineProperty(node, 'offsetTop', { configurable: true, get: () => [...box.children].indexOf(node) * 48 });
      node.getBoundingClientRect = () => ({ top: node.offsetTop } as DOMRect);
    }
    fireEvent.pointerDown(screen.getByText('plan'), { button: 0, clientY: 7 * 48 + 10, pointerId: 1 });
    fireEvent.pointerMove(box, { clientY: 10, pointerId: 1 });
    expect(shown()[0]).toBe('plan');
    await act(async () => { fireEvent.keyDown(window, { key: 'Escape' }); });
    expect(shown()).toEqual([...CARD_ORDER]);
    expect(save).not.toHaveBeenCalled();
  });
});

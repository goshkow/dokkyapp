import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { parseCatalog } from '../../state/plans';
import { PlansView } from './PlansView';

vi.mock('../../state/payment', () => ({ startPayment: vi.fn() }));
vi.mock('../../state/overlay', () => ({ guarded: (callback: unknown) => callback }));
afterEach(cleanup);

const catalog = parseCatalog({
  current: { plan: 'starter', plan_name: 'Старт' },
  plans: [
    { id: 'starter', name: 'Старт', action: 'current', features: [{ id: 'one', label: 'Чаты' }] },
    { id: 'advanced', name: 'Расширенный', action: 'upgrade', price: { amount: 123 }, features: [{ id: 'two', label: 'Разработка' }] },
  ],
})!;

describe('каталог подписок', () => {
  it('переключает серверные планы клавиатурой и связывает панель с вкладкой', () => {
    render(<PlansView catalog={catalog} />);
    const initial = screen.getByRole('tabpanel');
    const first = screen.getByRole('tab', { name: 'Старт' });
    const next = screen.getByRole('tab', { name: 'Расширенный' });
    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(next);
    expect(next.getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('tabpanel').getAttribute('aria-labelledby')).toBe(next.id);
    expect(screen.getByRole('tabpanel')).not.toBe(initial);
    expect(screen.getByText('Разработка')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Улучшить до Расширенный' })).toBeTruthy();
    fireEvent.keyDown(next, { key: 'Home' });
    expect(screen.getByRole('tabpanel').getAttribute('aria-labelledby')).toBe(first.id);
    expect(screen.getByRole('button', { name: 'Активно' }).hasAttribute('disabled')).toBe(true);
  });

  it('галочки стоят только на планах до выбранного', () => {
    const three = parseCatalog({
      current: { plan: 'a', plan_name: 'A' },
      plans: ['a', 'b', 'c'].map((id) => ({ id, name: id.toUpperCase(), action: id === 'a' ? 'current' : 'upgrade', features: [] })),
    })!;
    const { container } = render(<PlansView catalog={three} />);
    const done = () => [...container.querySelectorAll('.plan-dot')].map((dot) => dot.hasAttribute('data-done'));
    expect(done()).toEqual([false, false, false]);
    fireEvent.click(screen.getByRole('tab', { name: 'C' }));
    expect(done()).toEqual([true, true, false]);
    fireEvent.click(screen.getByRole('tab', { name: 'A' }));
    expect(done()).toEqual([false, false, false]);
  });

  it('подсказка функции внутри модального окна остаётся в нём, иначе её не видно', () => {
    const detailed = parseCatalog({
      current: { plan: 'a', plan_name: 'A' },
      plans: [{ id: 'a', name: 'A', action: 'current', features: [{ id: 'one', label: 'Чаты', description: 'Все ваши чаты' }] }],
    })!;
    render(<dialog open><PlansView catalog={detailed} /></dialog>);
    fireEvent.click(screen.getByRole('button', { name: 'Чаты: Все ваши чаты' }));
    // jsdom не умеет Popover API: подсказка для него скрыта, в приложении она в верхнем слое
    const tip = screen.getByRole('tooltip', { hidden: true });
    expect(tip.closest('dialog')).toBeTruthy();
    expect(tip.textContent).toBe('Все ваши чаты');
  });

  it('подсказка у края окна не выходит за него: при нехватке места сверху встаёт под строку', () => {
    const detailed = parseCatalog({
      current: { plan: 'a', plan_name: 'A' },
      plans: [{ id: 'a', name: 'A', action: 'current', features: [{ id: 'one', label: 'Ранний доступ', description: 'Новые функции раньше всех' }] }],
    })!;
    render(<PlansView catalog={detailed} />);
    const button = screen.getByRole('button', { name: 'Ранний доступ: Новые функции раньше всех' });
    button.getBoundingClientRect = () => ({ left: window.innerWidth - 20, top: 4, right: window.innerWidth, bottom: 24, width: 20, height: 20, x: window.innerWidth - 20, y: 4, toJSON: () => ({}) });
    const original = HTMLElement.prototype.getBoundingClientRect;
    HTMLElement.prototype.getBoundingClientRect = function box(this: HTMLElement) {
      if (this.getAttribute('role') === 'tooltip') return { left: 0, top: 0, right: 200, bottom: 60, width: 200, height: 60, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
      return original.call(this);
    };
    try {
      fireEvent.click(button);
      const tip = screen.getByRole('tooltip', { hidden: true });
      expect(parseFloat(tip.style.left) + 200).toBeLessThanOrEqual(window.innerWidth - 10);
      expect(parseFloat(tip.style.top)).toBe(32);
    } finally {
      HTMLElement.prototype.getBoundingClientRect = original;
    }
  });
});

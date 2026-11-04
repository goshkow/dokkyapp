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
    expect(screen.getByRole('tooltip').closest('dialog')).toBeTruthy();
  });
});

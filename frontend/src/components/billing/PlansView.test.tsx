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
});

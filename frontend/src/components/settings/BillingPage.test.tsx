import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BillingPage } from './BillingPage';

const state = vi.hoisted(() => ({
  limits: { value: null as unknown, loading: false, error: false },
  plans: { catalog: null as unknown, loading: false, error: '' },
  loadLimits: vi.fn(), loadPlans: vi.fn(),
}));
vi.mock('../../state/limits', () => ({ useLimits: (select: (value: unknown) => unknown) => select(state.limits), loadLimits: state.loadLimits }));
vi.mock('../../state/plans', () => ({
  usePlans: (select: (value: unknown) => unknown) => select(state.plans), loadPlans: state.loadPlans,
  planName: (id: string) => id, resetFromCatalog: () => null,
}));
vi.mock('../../state/chatCore', () => ({ useChat: () => undefined }));
vi.mock('../../state/settingsUi', () => ({ useSettingsUi: () => '' }));
vi.mock('../../lib/external', () => ({ openExternal: vi.fn(), SITE: 'https://example.com' }));
vi.mock('../billing/PlansView', () => ({ PlansView: () => <div>Каталог сервера</div> }));
vi.mock('../billing/UsageResetPanel', () => ({ UsageResetPanel: () => null }));
vi.mock('../billing/planUi', () => ({ PlanName: ({ name }: { name: string }) => <span>{name}</span> }));

beforeEach(() => {
  state.limits = { value: { windowHours: 5, windowRemaining: 0, remaining: 72, developer: { message: '' } }, loading: false, error: false };
  state.plans = { catalog: { current: { plan: 'plus', plan_name: 'Plus' }, catalog_version: 'v1' }, loading: false, error: '' };
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('подписка и использование на одной странице', () => {
  it('показывает сначала окно разработки, затем общий остаток и каталог', () => {
    render(<BillingPage say={() => undefined} />);
    expect(state.loadLimits).toHaveBeenCalledOnce();
    expect(state.loadPlans).toHaveBeenCalledOnce();
    const summary = screen.getByLabelText('Использование подписки');
    expect(summary.textContent).toBe('Разработка · 5 чОсталось 0%Общее использованиеОсталось 72%');
    expect(summary.compareDocumentPosition(screen.getByText('Каталог сервера')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
  it('ошибка каталога не скрывает использование и позволяет повторить загрузку', () => {
    state.plans = { catalog: null, loading: false, error: 'Нет связи' };
    render(<BillingPage say={() => undefined} />);
    expect(screen.getByText('Осталось 72%')).toBeTruthy();
    expect(screen.getByText('Не удалось загрузить планы')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeTruthy();
  });
  it('отсутствующий лимит показывает явно, а не как нулевой остаток', () => {
    state.limits.value = { windowHours: 5, windowRemaining: null, remaining: null, developer: { message: '' } };
    render(<BillingPage say={() => undefined} />);
    expect(screen.getAllByText('Нет данных')).toHaveLength(2);
    expect(screen.queryByText('Осталось 0%')).toBeNull();
  });
});

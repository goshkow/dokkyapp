import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { defaultSettings } from '../../lib/types';
import { GeneralPage } from './GeneralPage';

vi.mock('../../state/data', () => ({ useData: (select: (value: unknown) => unknown) => select({ settings: defaultSettings }), saveSettings: vi.fn() }));
vi.mock('../../lib/glass', () => ({ glassActive: () => true, glassCapable: () => document.documentElement.classList.contains('glass-capable'), setGlassEnabled: vi.fn() }));
afterEach(() => { cleanup(); document.documentElement.className = ''; });

describe('Liquid Glass в общих настройках', () => {
  it('показывает выключатель на совместимом Mac', () => {
    document.documentElement.classList.add('platform-mac', 'glass-capable');
    render(<GeneralPage say={() => undefined} />);
    expect(screen.getByRole('switch', { name: 'Liquid Glass' }).getAttribute('aria-checked')).toBe('true');
  });
  it('не предлагает стекло в неподдерживаемой системе', () => {
    render(<GeneralPage say={() => undefined} />);
    expect(screen.queryByRole('switch', { name: 'Liquid Glass' })).toBeNull();
  });
});

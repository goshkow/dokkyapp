import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SystemPage } from './SystemPages';

vi.mock('../../state/data', () => ({ useData: () => ({ render_mode: 'gpu' }), saveSettings: vi.fn(), APPEARANCE_DEFAULTS: {} }));
vi.mock('../../state/overlay', () => ({ confirmDialog: vi.fn(), guarded: (fn: unknown) => fn }));
vi.mock('../../lib/external', () => ({ openExternal: vi.fn(), SITE: 'https://example.com' }));
vi.mock('../../lib/glass', () => ({ glassActive: () => false, glassCapable: () => document.documentElement.classList.contains('glass-capable'), setGlassEnabled: vi.fn() }));
vi.mock('./GeneralPage', () => ({ saveKey: vi.fn() }));

afterEach(() => { cleanup(); document.documentElement.className = ''; });

const show = () => render(<SystemPage say={() => undefined} />);

describe('настройки системы показывают только то, что есть в этой ОС', () => {
  it('в Windows есть аппаратное ускорение и нет Liquid Glass', () => {
    document.documentElement.classList.add('platform-win');
    show();
    expect(screen.queryByText('Аппаратное ускорение')).not.toBeNull();
    expect(screen.queryByText('Liquid Glass')).toBeNull();
  });

  it('в macOS настройки стекла и управления вынесены из системы', () => {
    document.documentElement.classList.add('platform-mac', 'glass-capable');
    show();
    expect(screen.queryByText('Liquid Glass')).toBeNull();
    expect(screen.queryByText('Разрешить Computer Use')).toBeNull();
    expect(screen.queryByText('Аппаратное ускорение')).toBeNull();
  });

  it('в macOS без стекла раздела «Отрисовка» нет совсем', () => {
    document.documentElement.classList.add('platform-mac');
    show();
    expect(screen.queryByText('Отрисовка')).toBeNull();
  });
});

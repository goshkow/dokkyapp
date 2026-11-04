import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const sections = vi.hoisted(() => ({ list: [] as { key: string; label: string; icon: string; active: boolean; badge: boolean }[] }));
vi.mock('../../state/surface', () => ({
  useSurface: (select: (state: unknown) => unknown) => select({ status: 'ready', sections: sections.list }),
  openSection: vi.fn(),
}));
vi.mock('../../state/ui', () => ({ useUi: (select: (state: unknown) => unknown) => select({ scope: 'chat' }) }));
vi.mock('../../state/view', () => ({ useView: (select: (state: unknown) => unknown) => select({ page: 'chat' }) }));
vi.mock('../../state/remote', () => ({ openLibrary: vi.fn(), openPage: vi.fn(), switchScope: vi.fn() }));
vi.mock('../../state/overlay', () => ({ guarded: (run: () => void) => run }));

import { Nav } from './Nav';

const section = (key: string, icon = '') => ({ key, label: key, icon, active: false, badge: false });
const glyph = (container: HTMLElement, key: string) => container.querySelector(`#site-section-${key} svg`)?.getAttribute('class') ?? '';

afterEach(cleanup);

it('без иконки от сайта известные разделы различаются, неизвестный получает глобус', () => {
  sections.list = [section('library'), section('cloud'), section('food'), section('voices'), section('unknown')];
  const { container } = render(<Nav />);
  const known = ['library', 'cloud', 'food', 'voices'].map((key) => glyph(container, key));
  expect(new Set(known).size).toBe(4);
  expect(known.some((name) => name.includes('globe'))).toBe(false);
  expect(glyph(container, 'unknown')).toContain('globe');
});

it('иконка от сайта важнее своей', () => {
  sections.list = [section('cloud', 'data:image/svg+xml;charset=utf-8,%3Csvg%3E%3C%2Fsvg%3E')];
  const { container } = render(<Nav />);
  expect(container.querySelector('#site-section-cloud .surface-icon')).not.toBeNull();
  expect(container.querySelector('#site-section-cloud svg')).toBeNull();
});

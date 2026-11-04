import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FadingSlot, HomeTabs } from './SiteSurface';
import { pressSurfaceTab, type SiteTab, type SiteTabs } from '../../state/surface';

vi.mock('../../state/surface', async (original) => ({ ...(await original<typeof import('../../state/surface')>()), pressSurfaceTab: vi.fn() }));

const box = { x: 10, y: 8, w: 200, h: 30, r: 15, o: 1 };
const slot = (root: HTMLElement) => root.querySelector<HTMLElement>('[data-glass="product"]');
const fade = (root: HTMLElement) => JSON.parse(slot(root)!.dataset.glassFade!) as { from: number; to: number };

let reduced = false;
beforeEach(() => {
  reduced = false;
  vi.useFakeTimers();
  vi.stubGlobal('matchMedia', () => ({ matches: reduced }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('плитка над блоком страницы', () => {
  it('при возврате на экран стоит сразу, без проявления', () => {
    const { container } = render(<FadingSlot id="product" box={box} />);
    expect(slot(container)!.dataset.glassFade).toBeUndefined();
  });

  it('гаснет на последнем месте и снимается после угасания', () => {
    const { container, rerender } = render(<FadingSlot id="product" box={box} />);
    rerender(<FadingSlot id="product" box={null} />);
    expect(slot(container)!.style.width).toBe('200px');
    expect(fade(container)).toMatchObject({ from: 1, to: 0 });
    act(() => { vi.advanceTimersByTime(299); });
    expect(slot(container)).not.toBeNull();
    act(() => { vi.advanceTimersByTime(1); });
    expect(slot(container)).toBeNull();
  });

  it('проявляется, а затем держится без угасания', () => {
    const { container, rerender } = render(<FadingSlot id="product" box={null} />);
    expect(slot(container)).toBeNull();
    rerender(<FadingSlot id="product" box={box} />);
    expect(fade(container)).toMatchObject({ from: 0, to: 1 });
    act(() => { vi.advanceTimersByTime(300); });
    expect(slot(container)!.dataset.glassFade).toBeUndefined();
  });

  it('разворот посреди угасания не снимает плитку', () => {
    const { container, rerender } = render(<FadingSlot id="product" box={box} />);
    rerender(<FadingSlot id="product" box={null} />);
    act(() => { vi.advanceTimersByTime(120); });
    rerender(<FadingSlot id="product" box={box} />);
    expect(fade(container).to).toBe(1);
    expect(fade(container).from).toBeGreaterThan(0);
    act(() => { vi.advanceTimersByTime(300); });
    expect(slot(container)).not.toBeNull();
    expect(slot(container)!.dataset.glassFade).toBeUndefined();
  });

  it('с уменьшением движения снимается сразу', () => {
    reduced = true;
    const { container, rerender } = render(<FadingSlot id="product" box={box} />);
    rerender(<FadingSlot id="product" box={null} />);
    expect(slot(container)).toBeNull();
  });
});

describe('капсула режимов', () => {
  const tab = (id: string, x: number, selected = false): SiteTab => ({ id, label: id, text: id, x, y: 5, w: 80, h: 34, o: 1, font: 14, gap: 6, selected, open: false, icon: '', iconW: 0, iconO: 0 });
  const tabs = (chosen: string): SiteTabs => {
    const items = [tab('chat', 5, chosen === 'chat'), tab('search', 85, chosen === 'search'), tab('more', 165)];
    const at = items.find((item) => item.selected)!;
    return { items, indicator: { x: at.x, y: 5, w: at.w, h: at.h }, morph: false };
  };
  const place = (root: HTMLElement) => root.querySelector<HTMLElement>('.tile-capsule')!.style.transform;

  beforeEach(() => { reduced = true; vi.mocked(pressSurfaceTab).mockClear(); });

  it('по нажатию едет сразу, не дожидаясь ответа страницы', () => {
    const { container } = render(<HomeTabs tabs={tabs('chat')} />);
    expect(place(container)).toContain('translate3d(5px');
    fireEvent.click(screen.getByRole('tab', { name: 'search' }));
    expect(pressSurfaceTab).toHaveBeenCalledWith('search');
    expect(place(container)).toContain('translate3d(85px');
  });

  it('страница не ответила: капсула возвращается к выбранной вкладке', () => {
    const { container } = render(<HomeTabs tabs={tabs('chat')} />);
    fireEvent.click(screen.getByRole('tab', { name: 'search' }));
    act(() => { vi.advanceTimersByTime(600); });
    expect(place(container)).toContain('translate3d(5px');
  });

  it('«ещё» и выбранная вкладка капсулу заранее не двигают: куда она уйдёт, решает страница', () => {
    const { container } = render(<HomeTabs tabs={tabs('search')} />);
    fireEvent.click(screen.getByRole('button', { name: 'more' }));
    expect(place(container)).toContain('translate3d(85px');
    fireEvent.click(screen.getByRole('tab', { name: 'search' }));
    expect(place(container)).toContain('translate3d(85px');
    expect(pressSurfaceTab).toHaveBeenCalledTimes(2);
  });
});

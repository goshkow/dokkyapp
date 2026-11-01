import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FadingSlot } from './SiteSurface';

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

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PanelResizer, clampWidth } from './PanelResizer';

function Shell() {
  return <div id="shell" style={{ ['--rw-open' as string]: '350px', ['--lw-open' as string]: '288px' }}><aside id="left"><PanelResizer side="left" /></aside><aside id="right"><PanelResizer /></aside></div>;
}

const width = () => document.getElementById('shell')?.style.getPropertyValue('--rw-open');

beforeEach(() => { localStorage.clear(); Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1600 }); });
afterEach(cleanup);

describe('ширина правой панели', () => {
  it('не уже 300 и оставляет чату место', () => {
    expect(clampWidth(100, 1600, 288)).toBe(300);
    expect(clampWidth(2000, 1600, 288)).toBe(892);
    expect(clampWidth(2000, 1600, 0)).toBe(900);
    expect(clampWidth(500, 700, 288)).toBe(300);
  });

  it('стрелки меняют ширину и запоминают её, двойной щелчок сбрасывает', () => {
    render(<Shell />);
    const handle = screen.getByRole('separator', { name: 'Ширина правой панели' });
    fireEvent.keyDown(handle, { key: 'ArrowLeft' });
    expect(width()).toBe('366px');
    expect(localStorage.getItem('dokky.inspector-width')).toBe('366');
    fireEvent.keyDown(handle, { key: 'End' });
    expect(width()).toBe('300px');
    fireEvent.doubleClick(handle);
    expect(width()).toBe('');
    expect(localStorage.getItem('dokky.inspector-width')).toBeNull();
  });

  it('сохранённая ширина возвращается при запуске', () => {
    localStorage.setItem('dokky.inspector-width', '480');
    render(<Shell />);
    expect(width()).toBe('480px');
  });
});

describe('ширина левого меню', () => {
  const left = () => document.getElementById('shell')?.style.getPropertyValue('--lw-open');

  it('держится в своих границах и оставляет чату место', () => {
    expect(clampWidth(100, 1600, 350, 'left')).toBe(244);
    expect(clampWidth(2000, 1600, 350, 'left')).toBe(460);
    expect(clampWidth(460, 1000, 350, 'left')).toBe(244);
  });

  it('стрелка вправо шире, запоминается отдельно от правой панели', () => {
    render(<Shell />);
    const handle = screen.getByRole('separator', { name: 'Ширина левого меню' });
    fireEvent.keyDown(handle, { key: 'ArrowRight' });
    expect(left()).toBe('304px');
    expect(localStorage.getItem('dokky.sidebar-width')).toBe('304');
    expect(localStorage.getItem('dokky.inspector-width')).toBeNull();
    fireEvent.keyDown(handle, { key: 'ArrowLeft' });
    expect(left()).toBe('288px');
    fireEvent.doubleClick(handle);
    expect(left()).toBe('');
    expect(localStorage.getItem('dokky.sidebar-width')).toBeNull();
  });

  it('при сужении окна отдаёт ширину, при расширении возвращает сохранённую', () => {
    localStorage.setItem('dokky.sidebar-width', '440');
    render(<Shell />);
    expect(left()).toBe('440px');
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1000 });
    fireEvent(window, new Event('resize'));
    expect(left()).toBe('244px');
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1600 });
    fireEvent(window, new Event('resize'));
    expect(left()).toBe('440px');
  });
});

describe('подсветка ручки', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });
  const hot = (handle: HTMLElement) => handle.hasAttribute('data-hot');

  it('курсор мимоходом её не зажигает', () => {
    render(<Shell />);
    const handle = screen.getByRole('separator', { name: 'Ширина левого меню' });
    fireEvent.pointerEnter(handle);
    act(() => { vi.advanceTimersByTime(100); });
    fireEvent.pointerLeave(handle);
    act(() => { vi.advanceTimersByTime(1000); });
    expect(hot(handle)).toBe(false);
  });

  it('загорается на ручке и гаснет, когда курсор ушёл', () => {
    render(<Shell />);
    const handle = screen.getByRole('separator', { name: 'Ширина левого меню' });
    fireEvent.pointerEnter(handle);
    act(() => { vi.advanceTimersByTime(200); });
    expect(hot(handle)).toBe(true);
    fireEvent.pointerLeave(handle);
    expect(hot(handle)).toBe(false);
  });

  it('гаснет сама, если уход курсора на плитку страница не заметила', () => {
    render(<Shell />);
    const handle = screen.getByRole('separator', { name: 'Ширина левого меню' });
    fireEvent.pointerEnter(handle);
    act(() => { vi.advanceTimersByTime(200); });
    act(() => { vi.advanceTimersByTime(1500); });
    expect(hot(handle)).toBe(false);
  });
});

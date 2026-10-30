import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { PanelResizer, clampWidth } from './PanelResizer';

function Shell() {
  return <div id="shell" style={{ ['--rw-open' as string]: '350px', ['--lw-open' as string]: '288px' }}><aside id="right"><PanelResizer /></aside></div>;
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

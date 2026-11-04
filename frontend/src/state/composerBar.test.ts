import { afterEach, describe, expect, it, vi } from 'vitest';
import { composerBar, isBarControl, watchComposerBar } from './composerBar';

const flush = () => new Promise((resolve) => setTimeout(resolve));

function row(): HTMLElement {
  const root = document.createElement('footer');
  root.innerHTML = `
    <button data-glass="pill" aria-label="Выбрать папку"><span>demo-app</span></button>
    <button data-glass="product" aria-label="Разрешения" aria-expanded="false"><span>Вручную</span></button>
    <button data-glass="mode" aria-label="Режим" aria-expanded="false" disabled><span>Light</span></button>`;
  document.body.append(root);
  return root;
}

describe('переключатели над полем в плитках', () => {
  afterEach(() => { document.body.innerHTML = ''; });

  it('плитки получают подпись и состояние настоящих кнопок', () => {
    const stop = watchComposerBar(row());
    expect(composerBar.get().pill).toMatchObject({ label: 'demo-app', title: 'Выбрать папку', disabled: false });
    expect(composerBar.get().mode).toMatchObject({ label: 'Light', disabled: true });
    stop();
  });

  it('смена подписи и открытие меню доходят до плитки', async () => {
    const root = row();
    const stop = watchComposerBar(root);
    root.querySelector('[data-glass="mode"] span')!.textContent = 'Max';
    root.querySelector('[data-glass="product"]')!.setAttribute('aria-expanded', 'true');
    await flush();
    expect(composerBar.get().mode?.label).toBe('Max');
    expect(composerBar.get().product?.open).toBe(true);
    stop();
  });

  it('кнопки нет или слежение снято: плитка ничего не показывает', () => {
    const root = row();
    root.querySelector('[data-glass="pill"]')!.remove();
    const stop = watchComposerBar(root);
    expect(composerBar.get().pill).toBeNull();
    stop();
    expect(composerBar.get()).toEqual({ pill: null, product: null, mode: null });
  });

  it('нажатие в плитке - щелчок по настоящей кнопке, чужие имена не проходят', async () => {
    const composer = document.createElement('section');
    composer.id = 'composer';
    const root = row();
    composer.append(root);
    document.body.append(composer);
    const clicked = vi.fn();
    root.querySelector('[data-glass="mode"]')!.removeAttribute('disabled');
    root.querySelector('[data-glass="mode"]')!.addEventListener('click', clicked);
    const { pressBarControl } = await import('./composerBar');
    pressBarControl('mode');
    expect(clicked).toHaveBeenCalledOnce();
    expect(isBarControl('composer')).toBe(false);
    expect(() => pressBarControl('"]' as never)).not.toThrow();
  });
});

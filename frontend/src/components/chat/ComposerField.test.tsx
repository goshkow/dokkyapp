import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ running: false, recording: false, menu: false, suggest: null as unknown, suggestCount: 0 }));
const actions = vi.hoisted(() => ({ stop: vi.fn(), sendOrStop: vi.fn(), cancelVoice: vi.fn(), send: vi.fn(), setText: vi.fn(), report: vi.fn() }));
vi.mock('../../lib/bridge', () => ({ announceComposerFocus: vi.fn() }));
vi.mock('../../state/chatCore', () => ({ useChat: (select: (s: unknown) => unknown) => select({ running: state.running, stopping: false }) }));
vi.mock('../../state/data', () => ({ useData: (select: (s: unknown) => unknown) => select({ settings: {} }) }));
vi.mock('../../state/surface', () => ({ useSurface: (select: (s: unknown) => unknown) => select({ field: false, siteField: null }) }));
vi.mock('../../state/ui', () => ({ useUi: (select: (s: unknown) => unknown) => select({ scope: 'development', baseSize: 14, uiFont: '' }) }));
vi.mock('../../state/field', () => ({
  fieldActions: new Proxy(actions, { get: (target, key: string) => target[key as keyof typeof target] ?? vi.fn() }),
  uploadFromField: vi.fn(),
  voiceMeter: { get: () => ({ levels: [] }) },
  useField: (select: (s: unknown) => unknown) => select({ text: '', hasFiles: false, recording: state.recording, transcribing: false, focus: 0 }),
  useFieldHost: (select: (s: unknown) => unknown) => select({ menu: state.menu, suggest: state.suggest, suggestCount: state.suggestCount, place: 0, placeAt: 0 }),
}));
vi.mock('./VoiceWave', () => ({ VoiceWave: () => null }));
const suggest = vi.hoisted(() => ({ set: vi.fn(), move: vi.fn(), pick: vi.fn() }));
vi.mock('../../state/suggest', () => ({ suggestActions: suggest }));

import { ComposerField } from './ComposerField';

// jsdom без ResizeObserver: поле им только пересчитывает переносы
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;

afterEach(() => { cleanup(); vi.clearAllMocks(); Object.assign(state, { running: false, recording: false, menu: false, suggest: null, suggestCount: 0 }); });

const escape = (init: KeyboardEventInit = {}) => {
  const { container } = render(<ComposerField />);
  const field = container.querySelector('textarea')!;
  const allowed = fireEvent.keyDown(field, { key: 'Escape', ...init });
  return { allowed };
};

describe('Esc в поле ввода', () => {
  it('останавливает работу агента, как в Claude Code', () => {
    state.running = true;
    expect(escape().allowed).toBe(false);
    expect(actions.stop).toHaveBeenCalledTimes(1);
    // остановка, а не «отправить или остановить»: закончившийся ход не превращает Esc в отправку черновика
    expect(actions.sendOrStop).not.toHaveBeenCalled();
  });

  it('удержание Esc не шлёт остановку повторно', () => {
    state.running = true;
    expect(escape({ repeat: true }).allowed).toBe(false);
    expect(actions.stop).not.toHaveBeenCalled();
  });

  it('отменяет запись голоса без отправки', () => {
    state.recording = true;
    escape();
    expect(actions.cancelVoice).toHaveBeenCalledTimes(1);
    expect(actions.stop).not.toHaveBeenCalled();
  });

  it('без работы и при открытом меню не трогает ход: Esc остаётся навигации', () => {
    expect(escape().allowed).toBe(true);
    cleanup();
    state.running = true;
    state.menu = true;
    expect(escape().allowed).toBe(true);
    cleanup();
    state.menu = false;
    expect(escape({ shiftKey: true }).allowed).toBe(true);
    expect(actions.stop).not.toHaveBeenCalled();
    expect(actions.sendOrStop).not.toHaveBeenCalled();
  });
});

describe('стрелки в поле ввода', () => {
  it('вверх в пустом поле подставляет прошлый запрос, вниз возвращает пустое поле', () => {
    localStorage.setItem('dokky.prompt-history.development', JSON.stringify(['старый', 'новый']));
    const { container } = render(<ComposerField />);
    const field = container.querySelector('textarea')!;
    expect(fireEvent.keyDown(field, { key: 'ArrowUp' })).toBe(false);
    expect(field.value).toBe('новый');
    fireEvent.keyDown(field, { key: 'ArrowUp' });
    expect(field.value).toBe('старый');
    fireEvent.keyDown(field, { key: 'ArrowDown' });
    fireEvent.keyDown(field, { key: 'ArrowDown' });
    expect(field.value).toBe('');
    // набранный текст стрелки не трогают: они двигают курсор
    fireEvent.change(field, { target: { value: 'черновик' } });
    expect(fireEvent.keyDown(field, { key: 'ArrowUp' })).toBe(true);
    expect(field.value).toBe('черновик');
    localStorage.clear();
  });

  it('отправка запоминает запрос', () => {
    localStorage.clear();
    const { container } = render(<ComposerField />);
    const field = container.querySelector('textarea')!;
    fireEvent.change(field, { target: { value: 'сделай тесты' } });
    fireEvent.keyDown(field, { key: 'Enter' });
    expect(actions.send).toHaveBeenCalledTimes(1);
    expect(JSON.parse(localStorage.getItem('dokky.prompt-history.development')!)).toEqual(['сделай тесты']);
    localStorage.clear();
  });

  it('пустое поле в историю не попадает', () => {
    localStorage.clear();
    const { container } = render(<ComposerField />);
    fireEvent.keyDown(container.querySelector('textarea')!, { key: 'Enter' });
    expect(localStorage.getItem('dokky.prompt-history.development')).toBeNull();
  });
});

describe('подсказки в поле', () => {
  it('набранный «/» и «@» уходит главному виду, обычный текст - нет', () => {
    const { container } = render(<ComposerField />);
    const field = container.querySelector('textarea')!;
    fireEvent.change(field, { target: { value: '/pl', selectionStart: 3 } });
    expect(suggest.set).toHaveBeenLastCalledWith({ kind: '/', query: 'pl', start: 0, end: 3 });
    fireEvent.change(field, { target: { value: '/pl сейчас', selectionStart: 10 } });
    expect(suggest.set).toHaveBeenLastCalledWith(null);
    suggest.set.mockClear();
    fireEvent.change(field, { target: { value: 'обычный текст', selectionStart: 13 } });
    expect(suggest.set).not.toHaveBeenCalled();
  });

  it('открытый список забирает стрелки, Enter и Esc, а не история и не отправка', () => {
    state.suggest = { kind: '@', query: 'a', start: 0, end: 2 };
    state.suggestCount = 3;
    state.running = true;
    const { container } = render(<ComposerField />);
    const field = container.querySelector('textarea')!;
    fireEvent.keyDown(field, { key: 'ArrowDown' });
    expect(suggest.move).toHaveBeenCalledWith(1);
    fireEvent.keyDown(field, { key: 'Enter' });
    expect(suggest.pick).toHaveBeenCalledTimes(1);
    expect(actions.send).not.toHaveBeenCalled();
    fireEvent.keyDown(field, { key: 'Escape' });
    expect(suggest.set).toHaveBeenLastCalledWith(null);
    // Esc закрыл список, но не остановил агента
    expect(actions.stop).not.toHaveBeenCalled();
  });

  it('пустой список закрывается по Esc, а Enter отправляет как обычно', () => {
    state.suggest = { kind: '@', query: 'zzz', start: 0, end: 4 };
    state.suggestCount = 0;
    const { container } = render(<ComposerField />);
    const field = container.querySelector('textarea')!;
    fireEvent.change(field, { target: { value: '@zzz', selectionStart: 4 } });
    suggest.set.mockClear();
    fireEvent.keyDown(field, { key: 'Escape' });
    expect(suggest.set).toHaveBeenLastCalledWith(null);
    fireEvent.keyDown(field, { key: 'Enter' });
    expect(suggest.pick).not.toHaveBeenCalled();
    expect(actions.send).toHaveBeenCalledTimes(1);
  });
});

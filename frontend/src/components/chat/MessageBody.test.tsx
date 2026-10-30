import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearReplays, dropBody, hasReplay, pushDelta, resetBuffer } from '../../state/stream';
import { AssistantBody } from './MessageBody';

const text = 'Готово: освобождено 64 ГиБ, свободное место выросло до 153 ГиБ.';

describe('итог, пришедший одним куском', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame', 'setTimeout', 'clearTimeout', 'performance'] });
    // в jsdom нет Web Animations: анимация слов заменяется мгновенной
    Element.prototype.animate ??= function animate() {
      return { finished: Promise.resolve({ cancel() {} }), cancel() {} } as unknown as Animation;
    };
    Element.prototype.getAnimations ??= () => [];
    window.matchMedia ??= ((query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia;
  });
  afterEach(() => {
    cleanup();
    for (const id of ['10', '11']) dropBody(id);
    clearReplays();
    vi.useRealTimers();
  });

  it('печатается плавно, если окно получило его вживую', async () => {
    // порция пришла раньше, чем сервер отдал сообщение уже готовым
    pushDelta('10', { content: text }, '');
    resetBuffer('10');
    expect(hasReplay('10')).toBe(true);
    const settled = vi.fn();
    const { container } = render(<AssistantBody id="10" content={text} running={false} onSettled={settled} />);
    const body = container.querySelector('.message-body') as HTMLElement;
    // текст не вывален сразу целиком: печать начинается с первых слов
    expect(body.textContent ?? '').not.toBe(text);
    expect(settled).toHaveBeenLastCalledWith(false);
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(body.textContent).toContain('153 ГиБ');
    expect(settled).toHaveBeenLastCalledWith(true);
    expect(hasReplay('10')).toBe(false);
  });

  it('ответ из истории показывается сразу', () => {
    const settled = vi.fn();
    const { container } = render(<AssistantBody id="11" content={text} running={false} onSettled={settled} />);
    expect(container.querySelector('.message-body')?.textContent).toContain('153 ГиБ');
    expect(settled).toHaveBeenLastCalledWith(true);
  });
});

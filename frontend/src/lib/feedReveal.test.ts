import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { revealFeed } from './feedReveal';

describe('revealFeed', () => {
  let feed: HTMLElement;
  const made: { cancel: ReturnType<typeof vi.fn>; delay: number }[] = [];

  beforeEach(() => {
    made.length = 0;
    feed = document.createElement('div');
    feed.id = 'feed';
    document.body.append(feed);
    vi.stubGlobal('matchMedia', () => ({ matches: false }));
    feed.animate = vi.fn((_frames, options) => {
      const item = { cancel: vi.fn(), delay: Number((options as KeyframeAnimationOptions).delay) };
      made.push(item);
      return item as unknown as Animation;
    });
  });
  afterEach(() => {
    feed.remove();
    vi.unstubAllGlobals();
  });

  it('новая просьба отменяет идущее проявление: чат мигает один раз', () => {
    revealFeed(240, 250);
    revealFeed();
    expect(made).toHaveLength(2);
    expect(made[0]?.cancel).toHaveBeenCalledOnce();
    expect(made[1]?.cancel).not.toHaveBeenCalled();
  });

  it('лента ждёт, пока уйдёт новый экран, даже если чат загрузился раньше', () => {
    revealFeed(240, 250);
    revealFeed();
    expect(made[1]?.delay).toBeGreaterThan(0);
    expect(made[1]?.delay).toBeLessThanOrEqual(250);
  });
});

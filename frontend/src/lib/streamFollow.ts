/* Автопрокрутка ленты во время ответа - порт mini_app/assets/stream-follow.js сайта Докки.
   Пока агент работает, лента плавно идёт к концу. Когда начался текст ответа, лента не уезжает вниз за ним:
   начало ответа встаёт у верхнего края, и читатель читает с начала, пока текст дописывается ниже.
   Жест пользователя (колесо, касание, клавиши, полоса прокрутки) отключает слежение; возврат к концу включает снова. */

export interface FollowOptions {
  scroller: HTMLElement;
  /** Идёт ли ход: после него лента ничего не двигает сама. */
  isRunning(): boolean;
  /** Узел начавшегося ответа или null, пока идёт только работа. */
  target(): HTMLElement | null;
  /** Высота верхнего края, закрытого шапкой. */
  topInset(): number;
  /** Положение изменилось (для кнопки «К последнему сообщению»). */
  onMove?(): void;
}

export interface Follower {
  update(): void;
  /** Принудительный возврат к концу ленты (отправка сообщения, кнопка «вниз»). */
  bottom(): void;
  stop(): void;
  /** Жест пользователя, который не прошёл через само поле (колесо над шапкой, например). */
  gesture(event?: Event): void;
  /** Программный переход вверх (навигатор, варианты ответа): лента не возвращается к концу, пока не прокрутят вниз. */
  release(): void;
  enabled(): boolean;
  /** Сбрасывает «начало ответа» при открытии другого чата. */
  reset(): void;
  destroy(): void;
}

export function createFollower(options: FollowOptions): Follower {
  const { scroller } = options;
  let enabled = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= 10;
  let manual = false;
  // читатель потянул вверх, а прокрутка ещё не успела отойти от конца: возвращать к концу нельзя
  let leaving = false;
  let anchored = false;
  let frame = 0;
  let gestureFrame = 0;
  let lastFrame = 0;
  let closed = false;
  let observed: HTMLElement | null = null;

  const reduce = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const bottomOf = () => Math.max(0, scroller.scrollHeight - scroller.clientHeight);

  const stop = () => {
    enabled = false;
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
  };

  const desired = (): number => {
    const bottom = bottomOf();
    const target = options.target();
    if (!target || (!options.isRunning() && !anchored)) return bottom;
    anchored = true;
    const bounds = scroller.getBoundingClientRect();
    const inset = options.topInset() + 8;
    const beginning = target.getBoundingClientRect().top - bounds.top + scroller.scrollTop - inset;
    return Math.min(bottom, Math.max(0, beginning));
  };

  const watch = () => {
    const target = options.target();
    if (target === observed) return;
    if (observed) resize.unobserve(observed);
    observed = target;
    if (target) resize.observe(target);
  };

  const step = (timestamp: number) => {
    frame = 0;
    if (closed || !enabled) return;
    const next = desired();
    const difference = next - scroller.scrollTop;
    const elapsed = lastFrame ? Math.min(64, Math.max(8, timestamp - lastFrame)) : 16;
    lastFrame = timestamp;
    const distance = Math.sign(difference) * Math.max(1, Math.abs(difference) * (1 - Math.exp(-elapsed / 70)));
    scroller.scrollTop = reduce() || Math.abs(difference) < 3 ? next : scroller.scrollTop + distance;
    options.onMove?.();
    if (!reduce() && Math.abs(next - scroller.scrollTop) > 0.8) frame = requestAnimationFrame(step);
  };

  const update = () => {
    if (closed || !enabled) {
      options.onMove?.();
      return;
    }
    watch();
    if (options.isRunning()) {
      if (!frame) {
        lastFrame = 0;
        frame = requestAnimationFrame(step);
      }
      return;
    }
    // вне хода лента просто стоит у конца, если читатель внизу; начало прочитанного ответа не трогаем
    if (!anchored) scroller.scrollTop = bottomOf();
    options.onMove?.();
  };

  const position = () => {
    if (closed) return;
    options.onMove?.();
    if (!manual) return;
    const gap = Math.max(0, scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight);
    if (gap >= 15) leaving = false;
    if (gap <= 10 && !leaving) {
      manual = false;
      enabled = true;
      anchored = false;
      update();
    } else if (gap >= 15) stop();
  };

  const gesture = (event?: Event) => {
    if (event instanceof WheelEvent && event.deltaY < 0) leaving = true;
    else if (event instanceof WheelEvent && event.deltaY > 0) leaving = false;
    manual = true;
    enabled = false;
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    if (gestureFrame) cancelAnimationFrame(gestureFrame);
    gestureFrame = requestAnimationFrame(() => {
      gestureFrame = 0;
      position();
    });
  };
  const key = (event: KeyboardEvent) => {
    if (['ArrowUp', 'PageUp', 'Home'].includes(event.key)) leaving = true;
    if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) gesture();
  };
  // нажатие на полосу прокрутки справа от содержимого тоже жест
  const pointer = (event: PointerEvent) => {
    if (event.pointerType === 'mouse' && event.clientX >= scroller.getBoundingClientRect().left + scroller.clientWidth) gesture();
  };
  const resize = new ResizeObserver(update);
  resize.observe(scroller);
  scroller.style.overflowAnchor = 'none';
  scroller.addEventListener('wheel', gesture, { passive: true });
  scroller.addEventListener('touchmove', gesture, { passive: true });
  scroller.addEventListener('pointerdown', pointer, { passive: true });
  scroller.addEventListener('keydown', key);
  scroller.addEventListener('scroll', position, { passive: true });

  return {
    update,
    bottom() {
      manual = false;
      leaving = false;
      enabled = true;
      anchored = false;
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
      scroller.scrollTop = bottomOf();
      update();
    },
    stop,
    gesture,
    release() {
      // без этого кадр спустя position() видит, что лента ещё внизу, и возвращает её к концу посреди перехода
      leaving = true;
      gesture();
    },
    enabled: () => enabled,
    reset() {
      manual = false;
      leaving = false;
      enabled = true;
      anchored = false;
    },
    destroy() {
      closed = true;
      if (frame) cancelAnimationFrame(frame);
      if (gestureFrame) cancelAnimationFrame(gestureFrame);
      resize.disconnect();
      scroller.removeEventListener('wheel', gesture);
      scroller.removeEventListener('touchmove', gesture);
      scroller.removeEventListener('pointerdown', pointer);
      scroller.removeEventListener('keydown', key);
      scroller.removeEventListener('scroll', position);
    },
  };
}

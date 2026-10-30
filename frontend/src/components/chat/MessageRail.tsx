import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { releaseFeed } from '../../state/chatCore';
import { reducedMotion } from '../../lib/motion';

/* Рейка быстрой навигации по вопросам, как на сайте Докки: черточки у правого края, подсказка при наведении. */
const MIN_TURNS = 2;

interface Turn {
  element: HTMLElement;
  question: string;
  /** узел ответа: текст читается лениво, только когда карточка показана */
  answerElement: HTMLElement | null;
}

const clean = (value: string) => value.replace(/\s+/g, ' ').trim();
const clip = (value: string, limit: number) => (value.length > limit ? `${value.slice(0, limit - 1).trimEnd()}…` : value);
// textContent, а не innerText: чтение innerText заставляет браузер пересчитывать раскладку всей ленты
const textOf = (element: Element | null) => clean((element?.querySelector('.message-body') as HTMLElement | null)?.textContent ?? '');

function collect(feed: HTMLElement): Turn[] {
  const messages = [...feed.querySelectorAll<HTMLElement>('article.message')];
  const result: Turn[] = [];
  messages.forEach((element, index) => {
    if (!element.classList.contains('user')) return;
    let answer: HTMLElement | null = null;
    for (let next = index + 1; next < messages.length && !messages[next]?.classList.contains('user'); next += 1) {
      if (messages[next]?.classList.contains('assistant')) {
        answer = messages[next] as HTMLElement;
        break;
      }
    }
    const asked = textOf(element) || (element.parentElement?.querySelector('.message-attachments, .message-attachment') ? 'Вложение' : 'Сообщение');
    result.push({ element, question: asked, answerElement: answer });
  });
  return result;
}

export function MessageRail() {
  const nav = useRef<HTMLElement>(null);
  const track = useRef<HTMLDivElement>(null);
  const card = useRef<HTMLDivElement>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [active, setActive] = useState(-1);
  // hover хранит последнюю показанную карточку: при скрытии она гаснет на месте, а не прыгает к верху
  const [hover, setHover] = useState<{ index: number } | null>(null);
  const tickNode = useRef<HTMLElement | null>(null);
  const [open, setOpen] = useState(false);
  const [wide, setWide] = useState(false);
  const hideTimer = useRef(0);
  const feedNode = () => document.getElementById('feed');

  const fit = useCallback(() => {
    const feed = feedNode();
    const box = nav.current;
    if (!feed || !box) return;
    const scrollbar = Math.max(0, feed.offsetWidth - feed.clientWidth);
    const right = scrollbar + 10;
    box.style.right = `${right}px`;
    const gutter = parseFloat(getComputedStyle(feed).paddingRight) || 0;
    setWide(gutter >= right + 26 + 10);
  }, []);

  const update = useCallback((list: Turn[]) => {
    const feed = feedNode();
    if (!feed || !list.length) return;
    const bounds = feed.getBoundingClientRect();
    const inset = parseFloat(getComputedStyle(feed).paddingTop) || 66;
    const line = bounds.top + inset + 24;
    const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight <= 10;
    let index = 0;
    if (atBottom) index = list.length - 1;
    else {
      // вопросы идут сверху вниз, поэтому последний, чей верх выше линии, находится двоичным поиском
      let low = 0;
      let high = list.length - 1;
      while (low <= high) {
        const middle = (low + high) >> 1;
        if ((list[middle] as Turn).element.getBoundingClientRect().top <= line) {
          index = middle;
          low = middle + 1;
        } else high = middle - 1;
      }
    }
    setActive(index);
  }, []);

  // перестраиваем только при появлении или удалении сообщений, а не на каждый токен потока
  useEffect(() => {
    const feed = feedNode();
    if (!feed) return undefined;
    let timer = 0;
    let frame = 0;
    let current: Turn[] = [];
    const build = () => {
      timer = 0;
      fit();
      const list = collect(feed);
      current = list;
      setTurns((previous) => (previous.length === list.length && previous.every((turn, i) => turn.element === list[i]?.element && turn.question === list[i]?.question) ? previous : list));
      update(list);
    };
    const schedule = () => { if (!timer) timer = window.setTimeout(build, 40); };
    const touches = (node: Node) => node.nodeType === 1 && ((node as Element).matches('article.message, .turn-message') || Boolean((node as Element).querySelector('article.message')));
    const mutations = new MutationObserver((records) => {
      for (const record of records) {
        if ([...record.addedNodes, ...record.removedNodes].some(touches)) {
          schedule();
          return;
        }
      }
    });
    mutations.observe(feed, { childList: true, subtree: true });
    const scroll = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; update(current); }); };
    feed.addEventListener('scroll', scroll, { passive: true });
    const resize = new ResizeObserver(() => { fit(); scroll(); });
    resize.observe(feed);
    window.addEventListener('resize', fit, { passive: true });
    build();
    return () => {
      mutations.disconnect();
      resize.disconnect();
      feed.removeEventListener('scroll', scroll);
      window.removeEventListener('resize', fit);
      clearTimeout(timer);
      cancelAnimationFrame(frame);
    };
  }, [fit, update]);

  // активная черта остаётся в видимой части длинного списка
  useEffect(() => {
    const ticks = track.current?.children;
    const tick = ticks?.[active] as HTMLElement | undefined;
    const box = track.current;
    if (!tick || !box) return;
    const top = tick.offsetTop;
    const bottom = top + tick.offsetHeight;
    if (top < box.scrollTop) box.scrollTop = top;
    else if (bottom > box.scrollTop + box.clientHeight) box.scrollTop = bottom - box.clientHeight;
  }, [active, turns]);

  const hide = () => {
    clearTimeout(hideTimer.current);
    setOpen(false);
  };
  const scheduleHide = () => {
    clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => setOpen(false), 90);
  };
  // место карточки считается после отрисовки текста: по настоящей высоте, а не по высоте прежнего содержимого
  const place = useCallback(() => {
    const box = nav.current;
    const main = box?.parentElement;
    const button = tickNode.current;
    const element = card.current;
    if (!box || !main || !button || !element) return;
    const navBox = box.getBoundingClientRect();
    const tick = button.getBoundingClientRect();
    const mainBox = main.getBoundingClientRect();
    const height = element.offsetHeight;
    let top = tick.top - navBox.top + tick.height / 2 - height / 2;
    const minTop = mainBox.top + 72 - navBox.top;
    const maxTop = mainBox.bottom - 108 - navBox.top - height;
    top = Math.max(minTop, Math.min(top, Math.max(minTop, maxTop)));
    element.style.transform = `translateY(${Math.round(top)}px)`;
  }, []);
  useLayoutEffect(() => {
    if (hover) place();
  }, [hover, place]);
  const show = (index: number, button: HTMLElement) => {
    clearTimeout(hideTimer.current);
    tickNode.current = button;
    setHover({ index });
    setOpen(true);
  };
  const go = (index: number) => {
    const turn = turns[index];
    const feed = feedNode();
    if (!turn || !feed || !turn.element.isConnected) return;
    // жест отключает автопрокрутку потока, иначе она вернёт чат вниз
    releaseFeed();
    const bounds = feed.getBoundingClientRect();
    const inset = parseFloat(getComputedStyle(feed).paddingTop) || 66;
    const top = turn.element.getBoundingClientRect().top - bounds.top + feed.scrollTop - inset - 8;
    feed.scrollTo({ top: Math.max(0, top), behavior: reducedMotion() ? 'auto' : 'smooth' });
    turn.element.classList.add('rail-target');
    window.setTimeout(() => turn.element.classList.remove('rail-target'), 1500);
    hide();
  };

  const enough = turns.length >= MIN_TURNS;
  const shown = hover ? turns[hover.index] : undefined;
  const answer = shown?.answerElement?.isConnected ? textOf(shown.answerElement) : '';
  return (
    <nav
      ref={nav}
      className={`message-rail${enough ? ' has-turns' : ''}${wide ? ' is-wide' : ''}`}
      aria-label="Навигация по сообщениям"
      onPointerLeave={scheduleHide}
      onKeyDown={(event) => {
        if (event.key === 'Escape') { event.preventDefault(); return hide(); }
        if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
        const items = [...(track.current?.querySelectorAll<HTMLElement>('.message-rail-tick') ?? [])];
        const at = items.indexOf(document.activeElement as HTMLElement);
        if (at < 0) return;
        event.preventDefault();
        items[Math.max(0, Math.min(items.length - 1, at + (event.key === 'ArrowDown' ? 1 : -1)))]?.focus();
      }}
    >
      <div className="message-rail-track" ref={track}>
        {enough && turns.map((turn, index) => (
          <button
            key={index}
            type="button"
            className={`message-rail-tick${index === active ? ' is-active' : ''}`}
            aria-current={index === active ? 'true' : undefined}
            aria-describedby="messageRailCard"
            aria-label={`Сообщение ${index + 1} из ${turns.length}: ${clip(turn.question, 80)}`}
            onPointerEnter={(event) => show(index, event.currentTarget)}
            onFocus={(event) => show(index, event.currentTarget)}
            onBlur={scheduleHide}
            onClick={() => go(index)}
          />
        ))}
      </div>
      <div ref={card} id="messageRailCard" role="tooltip" className={`message-rail-card${open && shown ? ' is-visible' : ''}`}>
        {shown && (
          <>
            <div className="message-rail-count">{hover!.index + 1} из {turns.length}</div>
            <div className="message-rail-question">{clip(shown.question, 160)}</div>
            {answer && <div className="message-rail-answer">{clip(answer, 130)}</div>}
          </>
        )}
      </div>
    </nav>
  );
}

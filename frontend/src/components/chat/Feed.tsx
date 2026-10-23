import { useEffect, useLayoutEffect, useMemo, useRef, type ReactNode } from 'react';
import { buildFeed, type FeedItem } from '../../lib/feed';
import { conciseStatus } from '../../lib/journal';
import { reducedMotion } from '../../lib/motion';
import { createFollower, type Follower } from '../../lib/streamFollow';
import { reloadChat, loadOlder } from '../../state/chat';
import { chat, pinFeed, registerFeed, useChat } from '../../state/chatCore';
import { useProgress } from '../../state/cloudProgress';
import { feedUi } from '../../state/feedUi';
import { ui, useUi } from '../../state/ui';
import { useView } from '../../state/view';
import { Sentinel } from '../ui/Sentinel';
import { PageHost } from '../pages/PageHost';
import { CloudWork } from './CloudExtras';
import { Journal } from './Journal';
import { MessageItem } from './MessageItem';
import { LivePlan, PlanApproval } from './Plan';
import { Welcome } from './Welcome';

/** Узел начавшегося ответа: пока идёт одна работа, его нет и лента следует за концом. */
const answerTarget = (feed: HTMLElement): HTMLElement | null =>
  feed.querySelector<HTMLElement>(':scope > .turn-message.assistant:not(.local-work):not(.cloud-work):has(> article.streaming)');

/** Прокрутка ленты: следование как на сайте Докки (`lib/streamFollow.ts`), кнопка «вниз», чтение не прыгает при движении панелей. */
function useFeedScroll(node: React.RefObject<HTMLElement | null>) {
  const follower = useRef<Follower | null>(null);
  useEffect(() => {
    const feed = node.current;
    if (!feed) return undefined;
    // кнопка появляется, когда до конца больше трёх экранов, как на сайте
    const jump = () => {
      const has = Boolean(feed.querySelector('[data-message-id], .cloud-work'));
      const distance = feed.scrollHeight - feed.scrollTop - feed.clientHeight;
      const hidden = document.getElementById('main')?.classList.contains('chat-page') === false || !has || distance < feed.clientHeight * 3;
      if (feedUi.get().jump === hidden) feedUi.set({ jump: !hidden });
    };
    const main = document.getElementById('main');
    const instance = createFollower({
      scroller: feed,
      isRunning: () => chat.get().running,
      target: () => answerTarget(feed),
      topInset: () => Math.max(0, (parseFloat(getComputedStyle(main ?? feed).getPropertyValue('--feed-top')) || 58) - 20),
      onMove: jump,
    });
    follower.current = instance;
    registerFeed({
      follow: () => instance.update(),
      pin: () => instance.bottom(),
      release: () => instance.gesture(),
      following: () => instance.enabled(),
    });
    // колесо над шапкой, полем ввода и полосой перетаскивания листает чат, как под прозрачным краем
    const edge = (event: WheelEvent) => {
      const target = event.target as Element;
      if (!target.closest?.('.chat-header, #composer, .desktop-drag-strip')) return;
      if (target.closest('textarea, select, .composer-actions, .popover, .select-menu, dialog')) return;
      instance.gesture(event);
      feed.scrollTop += event.deltaY;
    };
    document.addEventListener('wheel', edge, { passive: true, capture: true });
    // пока панель едет, колонка меняет ширину: выбранный абзац удерживается на месте
    let frame = 0;
    const hold = (duration = 520) => {
      if (!feed.isConnected) return;
      const box = feed.getBoundingClientRect();
      const target = instance.enabled() ? null : document.elementsFromPoint(box.left + box.width / 2, box.top + box.height / 2)
        .find((item) => feed.contains(item) && item.matches('p,li,h1,h2,h3,h4,pre,table,blockquote,.code-block,.activity,.journal-commentary,.message-actions'));
      const offset = target ? target.getBoundingClientRect().top - box.top : 0;
      const end = performance.now() + duration;
      cancelAnimationFrame(frame);
      const tick = () => {
        if (target?.isConnected) {
          const delta = target.getBoundingClientRect().top - feed.getBoundingClientRect().top - offset;
          if (Math.abs(delta) > 0.5) feed.scrollTop += delta;
        } else if (instance.enabled()) instance.update();
        if (performance.now() < end) frame = requestAnimationFrame(tick);
      };
      frame = requestAnimationFrame(tick);
    };
    // окно меняет ширину колонки: удерживаем блок у верхнего края ленты, чтобы чат не уезжал вверх и вниз
    const BLOCK = 'p,li,h1,h2,h3,h4,pre,table,blockquote,.code-block,.activity,.journal-commentary,.message-actions';
    let held: { node: Element; offset: number } | null = null;
    let width = feed.clientWidth;
    const remember = () => {
      const box = feed.getBoundingClientRect();
      const inset = Math.max(0, (parseFloat(getComputedStyle(main ?? feed).getPropertyValue('--feed-top')) || 58) - 20);
      const node = document.elementsFromPoint(box.left + box.width / 2, box.top + inset + 6).find((item) => feed.contains(item) && item.matches(BLOCK));
      held = node ? { node, offset: node.getBoundingClientRect().top - box.top } : null;
    };
    let rememberFrame = 0;
    const later = () => { if (!rememberFrame) rememberFrame = requestAnimationFrame(() => { rememberFrame = 0; remember(); }); };
    const widthWatch = new ResizeObserver(() => {
      const next = feed.clientWidth;
      if (next === width) return later();
      width = next;
      if (instance.enabled()) return instance.update();
      if (feed.scrollTop <= 2 || !held?.node.isConnected) return;
      const delta = held.node.getBoundingClientRect().top - feed.getBoundingClientRect().top - held.offset;
      if (Math.abs(delta) > 0.5) feed.scrollTop += delta;
    });
    widthWatch.observe(feed);
    feed.addEventListener('scroll', later, { passive: true });
    remember();
    let left = ui.get().leftOpen;
    let right = ui.get().rightOpen;
    const off = ui.subscribe(() => {
      const state = ui.get();
      if (state.leftOpen !== left || state.rightOpen !== right) {
        left = state.leftOpen;
        right = state.rightOpen;
        hold();
      }
    });
    return () => {
      registerFeed(null);
      instance.destroy();
      follower.current = null;
      document.removeEventListener('wheel', edge, true);
      cancelAnimationFrame(frame);
      cancelAnimationFrame(rememberFrame);
      widthWatch.disconnect();
      feed.removeEventListener('scroll', later);
      off();
    };
  }, [node]);
  return follower;
}

function useItems(): FeedItem[] {
  const messages = useChat((s) => s.messages);
  const optimistic = useChat((s) => s.optimistic);
  const live = useChat((s) => s.live);
  const alias = useChat((s) => s.alias);
  const sent = useChat((s) => s.sent);
  const plan = useChat((s) => s.plan);
  const running = useChat((s) => s.running);
  const deltaSeen = useChat((s) => s.deltaSeen);
  const runId = useChat((s) => s.runId);
  const contextOperation = useChat((s) => s.contextOperation);
  const variants = useChat((s) => s.variants);
  const development = useUi((s) => s.scope === 'development');
  const chatId = useView((s) => s.chat?.id ?? null);
  const cloudWork = useProgress((s) => Boolean(s.live));
  return useMemo(() => buildFeed({
    development, chatId, messages, optimistic, live, alias, sent, selectedVariants: new Map(Object.entries(variants)), plan, running, deltaSeen, runId, cloudWork, contextOperation,
  }), [development, chatId, messages, optimistic, live, alias, sent, variants, plan, running, deltaSeen, runId, cloudWork, contextOperation]);
}

function Waiting() {
  const status = useChat((s) => s.liveStatus);
  return (
    <section id="local-waiting" className="work-progress">
      <div className="status-line" role="status" aria-live="polite">
        <i className="journal-dots" aria-hidden="true"><b /><b /><b /><b /></i>
        <span key={status} className="local-status swap">{conciseStatus(status || 'Думаю…')}</span>
      </div>
    </section>
  );
}

function ChatItems(): ReactNode {
  const load = useChat((s) => s.load);
  const loadError = useChat((s) => s.loadError);
  const epoch = useChat((s) => s.epoch);
  const olderAvailable = useChat((s) => s.olderAvailable);
  const olderCursor = useChat((s) => s.olderCursor);
  const olderBusy = useChat((s) => s.olderBusy);
  const open = useView((s) => s.chat !== null);
  const development = useUi((s) => s.scope === 'development');
  const items = useItems();
  const seen = useRef({ epoch: -1, keys: new Set<string>(), armed: false });
  if (seen.current.epoch !== epoch) seen.current = { epoch, keys: new Set(), armed: false };
  const known = seen.current;
  const first = items[0]?.key;
  const height = useRef({ first, height: 0 });

  // догрузка старой истории не сдвигает читаемое место
  useLayoutEffect(() => {
    const feed = document.getElementById('feed');
    if (!feed) return;
    const before = height.current;
    if (before.first && first !== before.first && !chat.get().running && items.some((item) => item.key === before.first)) {
      feed.scrollTop += feed.scrollHeight - before.height;
    }
    height.current = { first, height: feed.scrollHeight };
  });

  // открытый чат сразу показывает конец переписки, а не начало, и весь появляется одинаково плавно
  const pinned = useRef(-1);
  useLayoutEffect(() => {
    if (load !== 'ready' || pinned.current === epoch) return;
    pinned.current = epoch;
    pinFeed();
    const feed = document.getElementById('feed');
    // пустой новый чат проявляет приветствие сам, ленту с сообщениями проявляем целиком и один раз
    if (!feed || reducedMotion() || items.length === 0) return;
    feed.dataset.opening = '';
    feed.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 260, easing: 'ease-out' });
    window.setTimeout(() => { delete feed.dataset.opening; }, 320);
  }, [load, epoch]);

  useEffect(() => {
    for (const item of items) known.keys.add(item.key);
    if (load === 'ready' && (items.length || !open)) known.armed = true;
  });

  if (load === 'loading') {
    return (
      <div className="chat-skeleton" role="status" aria-label="Загрузка чата">
        <span className="sk sk-user" />
        <span className="sk-block"><span className="sk sk-line" /><span className="sk sk-line" /><span className="sk sk-line short" /></span>
        <span className="sk sk-user small" />
        <span className="sk-block"><span className="sk sk-line" /><span className="sk sk-line medium" /></span>
      </div>
    );
  }
  if (load === 'failed') {
    return (
      <section className="chat-load-error">
        <h2>Не удалось открыть чат</h2>
        <p>{loadError}</p>
        <button type="button" className="secondary" onClick={() => reloadChat()}>Повторить загрузку</button>
      </section>
    );
  }
  const older = development ? olderAvailable : Boolean(olderCursor);
  return (
    <>
      {older && (
        <>
          <Sentinel scroller="#feed" edge="top" margin={600} onReach={() => { if (!chat.get().olderBusy) void loadOlder(); }} />
          {olderBusy && <div className="older-busy" role="status">Загружаю предыдущие сообщения…</div>}
        </>
      )}
      {!open && items.length === 0 && <Welcome epoch={epoch} />}
      {items.map((item) => {
        const isNew = known.armed && !known.keys.has(item.key);
        switch (item.kind) {
          case 'message': return <MessageItem key={item.key} message={item.message} isNew={isNew} />;
          case 'journal': return <Journal key={item.key} message={item.message} turnId={item.turnId} />;
          case 'plan-approval': return <PlanApproval key={item.key} snapshot={item.snapshot} />;
          case 'live-plan': return <LivePlan key={item.key} steps={item.steps} snapshot={item.snapshot} />;
          case 'cloud-work': return <CloudWork key={item.key} />;
          case 'waiting': return <Waiting key={item.key} />;
          case 'context': return <ContextRow key={item.key} text={item.text} />;
          default: return null;
        }
      })}
    </>
  );
}

function ContextRow({ text }: { text: string }) {
  return <div id="context-operation" className="journal-stage" role="status">{text}</div>;
}

export function Feed() {
  const node = useRef<HTMLElement>(null);
  const page = useView((s) => s.page);
  const scope = useUi((s) => s.scope);
  const busy = useChat((s) => s.load === 'loading');
  const follower = useFeedScroll(node);
  // при смене экрана чтение начинается с конца
  const version = useView((s) => s.version);
  useLayoutEffect(() => {
    follower.current?.reset();
    if (node.current) node.current.scrollTop = page === 'chat' ? node.current.scrollHeight : 0;
  }, [version, page, follower]);
  return (
    <section id="feed" className="feed" aria-label="Диалог" aria-busy={busy} tabIndex={0} ref={node} data-overlay-scroll="feed" inert={scope === 'search'}>
      {page === 'chat' ? <ChatItems /> : <PageHost />}
    </section>
  );
}

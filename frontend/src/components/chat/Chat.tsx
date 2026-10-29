import { useEffect } from 'react';
import { switchScope } from '../../state/actions';
import { followFeed, isFollowing, useChat } from '../../state/chatCore';
import { guarded } from '../../state/overlay';
import { useUi } from '../../state/ui';
import { pageNames, useView } from '../../state/view';
import { CloudSearch } from '../search/CloudSearch';
import { Composer } from './Composer';
import { ExpandedEditor } from './ExpandedEditor';
import { Feed } from './Feed';
import { MessageRail } from './MessageRail';

/** Отступы ленты под шапку и поле ввода считаются по их реальным размерам; чтение не прыгает, когда они меняются. */
function useChatInsets() {
  useEffect(() => {
    const main = document.getElementById('main');
    const feed = document.getElementById('feed');
    const header = main?.querySelector<HTMLElement>('.chat-header');
    const composer = document.getElementById('composer');
    if (!main || !feed || !header || !composer) return undefined;
    let frame = 0;
    const update = () => {
      frame = 0;
      if (!main.classList.contains('chat-page')) return;
      const headerHeight = header.getBoundingClientRect().height;
      const composerHeight = composer.getBoundingClientRect().height;
      // при ресайзе блок может на кадр схлопнуться до нуля: такие замеры не принимаем, иначе затухание пропадает
      if (headerHeight < 1 || composerHeight < 1) return schedule();
      const top = Math.ceil(headerHeight) + 8;
      const bottom = Math.ceil(composerHeight) + 20;
      const before = parseFloat(getComputedStyle(feed).paddingTop) || 0;
      const scroll = feed.scrollTop;
      const following = isFollowing();
      main.style.setProperty('--feed-top', `${top}px`);
      main.style.setProperty('--feed-bottom', `${bottom}px`);
      if (following) followFeed();
      else feed.scrollTop = scroll + top - before;
    };
    function schedule() { if (!frame) frame = requestAnimationFrame(update); }
    const resize = new ResizeObserver(schedule);
    for (const node of [header, composer, main]) resize.observe(node);
    // затухание у краёв ленты выключено, когда лента стоит у начала или у конца
    const edges = () => {
      const gap = feed.scrollHeight - feed.scrollTop - feed.clientHeight;
      main.toggleAttribute('data-at-top', feed.scrollTop <= 2);
      main.toggleAttribute('data-at-bottom', gap <= 2);
    };
    feed.addEventListener('scroll', edges, { passive: true });
    // лента меняется не только прокруткой: пришли сообщения, дописался ответ, раскрылся блок
    const content = new ResizeObserver(edges);
    const watch = () => {
      content.disconnect();
      content.observe(feed);
      for (const child of feed.children) content.observe(child);
    };
    const mutations = new MutationObserver(() => { watch(); edges(); });
    mutations.observe(feed, { childList: true });
    watch();
    edges();
    const classes = new MutationObserver(schedule);
    classes.observe(main, { attributes: true, attributeFilter: ['class'] });
    window.visualViewport?.addEventListener('resize', schedule);
    window.addEventListener('resize', schedule);
    schedule();
    return () => {
      resize.disconnect();
      classes.disconnect();
      content.disconnect();
      mutations.disconnect();
      feed.removeEventListener('scroll', edges);
      window.visualViewport?.removeEventListener('resize', schedule);
      window.removeEventListener('resize', schedule);
      cancelAnimationFrame(frame);
    };
  }, []);
}

function Title() {
  const page = useView((s) => s.page);
  const title = useView((s) => s.chat?.title ?? '');
  const text = page === 'chat' ? title : pageNames[page];
  return <b id="title">{text}</b>;
}

/** Переключатель «Чат / Поиск» в шапке облачных режимов: индикатор переезжает плавно. */
function ProductPill() {
  const scope = useUi((s) => s.scope);
  const page = useView((s) => s.page);
  const hidden = scope === 'development' || page !== 'chat';
  // переключатель нужен только в новом чате: когда переписка началась, режим уже выбран
  const started = useChat((s) => s.messages.length > 0 || s.optimistic !== null || s.live.length > 0);
  const chat = useView((s) => s.chat !== null);
  const items = [['chat', 'Чат'], ['search', 'Поиск']] as const;
  return (
    <div id="segmented-mode" className="ai-product-switch" data-glass="product-switch" data-product={scope === 'search' ? 'search' : 'chat'} role="tablist" aria-label="Чат или поиск" hidden={hidden} data-away={chat || started || undefined}>
      <span className="ai-product-indicator" aria-hidden="true" />
      {items.map(([id, label]) => (
        <button
          key={id}
          id={`product-${id}`}
          type="button"
          role="tab"
          data-product-mode={id}
          aria-selected={scope === id}
          tabIndex={scope === id ? 0 : -1}
          className={scope === id ? 'active' : undefined}
          onClick={guarded(() => switchScope(id))}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

export function Chat() {
  const scope = useUi((s) => s.scope);
  const page = useView((s) => s.page);
  const epoch = useChat((s) => s.epoch);
  useChatInsets();


  void epoch;
  return (
    <>
      <header className="chat-header">
        <div className="header-start"><Title /></div>
        <ProductPill />
        <div className="header-end" />
      </header>
      <div className="chat-edge-fade chat-edge-fade-top" aria-hidden="true" />
      <div className="chat-edge-fade chat-edge-fade-bottom" aria-hidden="true" />
      <Feed />
      <Composer />
      {page === 'chat' && <MessageRail />}
      <ExpandedEditor />
      {scope === 'search' && <CloudSearch />}
    </>
  );
}

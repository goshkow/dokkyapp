import { ChevronRight, Ellipsis } from 'lucide-react';
import { memo, useRef, useState, type RefObject } from 'react';
import { readText, writeText } from '../../lib/storage';
import {
  chatModeChats, openSurfaceChat, otherComputerChats, surfaceChatAction, surfaceEnableMultiChat, surfaceLoadMore, useSurface,
  type SurfaceChat,
} from '../../state/surface';
import { useSidebarQuery } from '../../state/sidebarQuery';
import { Popover } from '../ui/Popover';
import { Sentinel } from '../ui/Sentinel';
import { SurfaceIcon } from './Nav';

interface MenuState {
  chat: SurfaceChat;
  anchor: RefObject<HTMLElement | null> | { x: number; y: number };
}

/** Строка чата сайта: значок типа чата (облако, компьютер и новые типы) и состояние берутся со страницы сайта. */
const SurfaceRow = memo(function SurfaceRow({ chat, selected, host, onMenu }: { chat: SurfaceChat; selected: boolean; host?: boolean; onMenu: (state: MenuState) => void }) {
  const more = useRef<HTMLButtonElement>(null);
  const kinds = chat.kinds.join(' ');
  return (
    <div
      className="chat-row"
      data-chat-id={String(chat.id)}
      data-kinds={kinds || undefined}
      data-selected={selected || undefined}
      onContextMenu={(event) => {
        if (!chat.actions.length) return;
        event.preventDefault();
        onMenu({ chat, anchor: { x: event.clientX, y: event.clientY } });
      }}
    >
      <button type="button" className="chat-item" title={chat.title} onClick={() => { void openSurfaceChat(chat.id); }}>
        <span>
          {chat.icon && <SurfaceIcon icon={chat.icon} fallback={null} />}
          {chat.title}
        </span>
      </button>
      {host && chat.remote && (
        <span className="chat-host" data-offline={!chat.remote.online || undefined} title={chat.remote.online ? 'Компьютер в сети' : 'Компьютер не в сети'}>
          {chat.remote.host_name}
        </span>
      )}
      {chat.working ? (
        <span className="chat-state chat-working" title="Докки отвечает"><span className="spinner" aria-label="Докки отвечает" /></span>
      ) : chat.unread ? (
        <span className="chat-state" title="Новый ответ"><span className="chat-unread-dot" aria-label="Новый ответ" /></span>
      ) : null}
      {chat.actions.length > 0 && (
        <button ref={more} type="button" className="icon-btn chat-more" aria-label="Действия с чатом" onClick={() => onMenu({ chat, anchor: more })}>
          <Ellipsis aria-hidden="true" />
        </button>
      )}
    </div>
  );
});

function ActionsMenu({ menu, onClose }: { menu: MenuState | null; onClose: () => void }) {
  return (
    <Popover open={Boolean(menu)} anchor={menu?.anchor ?? null} onClose={onClose} label="Действия с чатом" align="end">
      {menu?.chat.actions.map((item) => (
        <button
          key={item.action}
          type="button"
          role="menuitem"
          className={`menu-item${item.danger ? ' danger' : ''}`}
          onClick={() => { const id = menu.chat.id; onClose(); void surfaceChatAction(id, item.action); }}
        >
          <span>{item.label}</span>
        </button>
      ))}
    </Popover>
  );
}

const matches = (query: string) => (chat: SurfaceChat) => !query || chat.title.toLowerCase().includes(query);

/** Чаты режима чата - это чаты сайта: порядок, закрепление, значки и действия те же, что в меню сайта. */
export function SurfaceChatLists() {
  const all = useSurface((state) => state.chats);
  const hasMore = useSurface((state) => state.hasMore);
  const multiChat = useSurface((state) => state.multiChat);
  const ready = useSurface((state) => state.status === 'ready');
  const query = useSidebarQuery().trim().toLowerCase();
  const [menu, setMenu] = useState<MenuState | null>(null);
  const chats = chatModeChats(all).filter(matches(query));
  const pinned = chats.filter((chat) => chat.group === 'pinned');
  const recent = chats.filter((chat) => chat.group === 'recent');
  const selected = chats.find((chat) => chat.active)?.id;
  return (
    <>
      {pinned.length > 0 && (
        <section className="pinned-chats" aria-label="Закреплённые чаты">
          <div className="chat-list">
            {pinned.map((chat) => <SurfaceRow key={chat.id} chat={chat} selected={chat.id === selected} onMenu={setMenu} />)}
          </div>
        </section>
      )}
      <section className="recent-chats" aria-label="Недавние чаты">
        <h2 className="section-title">Недавнее</h2>
        <div id="chats">
          {!multiChat ? (
            <div className="surface-multi-off">
              <b>Несколько чатов выключены</b>
              <span>Включите режим, чтобы создавать отдельные чаты с Докки.</span>
              <button type="button" className="btn" onClick={() => { void surfaceEnableMultiChat(); }}>Включить</button>
            </div>
          ) : recent.length ? (
            <div className="chat-list">
              {recent.map((chat) => <SurfaceRow key={chat.id} chat={chat} selected={chat.id === selected} onMenu={setMenu} />)}
            </div>
          ) : (
            <span className="list-empty">{!ready ? 'Загрузка…' : query ? 'Ничего не найдено' : 'Чатов пока нет'}</span>
          )}
          {multiChat && hasMore && !query && <Sentinel scroller=".sidebar-scroll" onReach={() => { void surfaceLoadMore(); }} />}
        </div>
      </section>
      <ActionsMenu menu={menu} onClose={() => setMenu(null)} />
    </>
  );
}

const OPEN_KEY = 'koddi-other-computers-open';

/** Режим кода: чаты удалённого доступа с других компьютеров, свёрнутым списком под «Недавним». Этот компьютер не показывается. */
export function OtherComputerChats() {
  const all = useSurface((state) => state.chats);
  const hostId = useSurface((state) => state.hostId);
  const remoteChat = useSurface((state) => state.remoteChat);
  const query = useSidebarQuery().trim().toLowerCase();
  const [open, setOpen] = useState(() => readText(OPEN_KEY) === '1');
  const [menu, setMenu] = useState<MenuState | null>(null);
  const chats = otherComputerChats(all, hostId).filter(matches(query));
  if (!chats.length) return null;
  const toggle = () => {
    setOpen(!open);
    writeText(OPEN_KEY, open ? '0' : '1');
  };
  return (
    <section className="remote-chats" data-open={open || undefined} aria-label="Чаты других компьютеров">
      <h2 className="section-title">
        <button type="button" className="section-toggle" aria-expanded={open} onClick={toggle}>
          <ChevronRight aria-hidden="true" />
          <span>Другие компьютеры</span>
        </button>
      </h2>
      {open && (
        <div className="chat-list">
          {chats.map((chat) => (
            <SurfaceRow
              key={chat.id}
              chat={chat}
              selected={chat.id === remoteChat}
              host
              onMenu={setMenu}
            />
          ))}
        </div>
      )}
      <ActionsMenu menu={menu} onClose={() => setMenu(null)} />
    </section>
  );
}

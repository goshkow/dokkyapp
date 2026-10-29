import { ChevronRight, Ellipsis, Folder, Pin, Plus } from 'lucide-react';
import { memo, useEffect, useMemo, useRef, useState, type DragEvent, type RefObject } from 'react';
import { copyText } from '../../lib/clipboard';
import { relativeTime, serverTime } from '../../lib/format';
import { applyOrder, moveChat } from '../../lib/order';
import type { Chat, Project } from '../../lib/types';
import { useData } from '../../state/data';
import { guarded } from '../../state/overlay';
import { refreshRemote, setChatShared, sharedOnPhone, useRemote } from '../../state/remoteControl';
import { deleteChat, loadCloud, newChat, openChat, removeProject, renameChat, setProjectOpen, togglePin } from '../../state/remote';
import { useSidebarQuery } from '../../state/sidebarQuery';
import { useUi } from '../../state/ui';
import { useView } from '../../state/view';
import { Popover } from '../ui/Popover';
import { Sentinel } from '../ui/Sentinel';

const stamp = (chat: Chat): number => serverTime(chat.updated_at ?? chat.created_at) || 0;
const byRecent = (a: Chat, b: Chat) => stamp(b) - stamp(a) || Number(b.id) - Number(a.id);

/** Метки времени обновляются раз в минуту. */
function useNow(period = 60_000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), period);
    return () => window.clearInterval(timer);
  }, [period]);
  return now;
}

interface MenuState {
  chat: Chat;
  anchor: RefObject<HTMLElement | null> | { x: number; y: number };
}

interface ProjectMenuState {
  project: Project;
  anchor: RefObject<HTMLElement | null> | { x: number; y: number };
}

let dragged: string | null = null;

interface RowProps {
  chat: Chat;
  selected: boolean;
  now: number;
  group: string;
  siblings: Chat[];
  onMenu: (state: MenuState) => void;
  onOrder: () => void;
}

/** Строка чата: слоты заголовка, времени и «…» фиксированы, при наведении меняется только прозрачность. */
const ChatRow = memo(function ChatRow({ chat, selected, now, group, siblings, onMenu, onOrder }: RowProps) {
  const more = useRef<HTMLButtonElement>(null);
  const [drop, setDrop] = useState<'before' | 'after' | null>(null);
  const when = relativeTime(chat.updated_at ?? chat.created_at, now);

  const onDragOver = (event: DragEvent<HTMLDivElement>) => {
    if (!dragged || dragged === String(chat.id)) return;
    event.preventDefault();
    const box = event.currentTarget.getBoundingClientRect();
    setDrop(event.clientY > box.top + box.height / 2 ? 'after' : 'before');
  };
  return (
    <div
      className="chat-row"
      data-chat-id={String(chat.id)}
      data-selected={selected || undefined}
      data-drop={drop ?? undefined}
      draggable
      onDragStart={(event) => {
        dragged = String(chat.id);
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', dragged);
      }}
      onDragEnd={() => { dragged = null; setDrop(null); }}
      onDragOver={onDragOver}
      onDragLeave={() => setDrop(null)}
      onDrop={(event) => {
        event.preventDefault();
        if (dragged && drop) {
          moveChat(siblings, group, dragged, String(chat.id), drop === 'after');
          onOrder();
        }
        dragged = null;
        setDrop(null);
      }}
      onContextMenu={(event) => {
        event.preventDefault();
        onMenu({ chat, anchor: { x: event.clientX, y: event.clientY } });
      }}
    >
      <button type="button" className="chat-item" title={chat.title} onClick={() => openChat(chat)}>
        <span>{chat.title}</span>
      </button>
      {chat.pinned && <Pin className="chat-pin" size={12} aria-label="Закреплён" />}
      {when && <time className="chat-time" dateTime={chat.updated_at}>{when}</time>}
      <button ref={more} type="button" className="icon-btn chat-more" aria-label="Действия с чатом" onClick={() => onMenu({ chat, anchor: more })}>
        <Ellipsis aria-hidden="true" />
      </button>
    </div>
  );
});

interface ListProps {
  chats: Chat[];
  group: string;
  now: number;
  selectedId: Chat['id'] | undefined;
  onMenu: (state: MenuState) => void;
  empty: string;
}

function ChatList({ chats, group, now, selectedId, onMenu, empty }: ListProps) {
  const [version, setVersion] = useState(0);
  const sorted = useMemo(() => {
    const ordered = applyOrder([...chats].sort(byRecent), group);
    return [...ordered.filter((chat) => chat.pinned), ...ordered.filter((chat) => !chat.pinned)];
  }, [chats, group, version]);
  if (!sorted.length) return <span className="list-empty">{empty}</span>;
  return (
    <div className="chat-list">
      {sorted.map((chat) => (
        <ChatRow
          key={chat.id}
          chat={chat}
          selected={chat.id === selectedId}
          now={now}
          group={group}
          siblings={sorted}
          onMenu={onMenu}
          onOrder={() => setVersion((state) => state + 1)}
        />
      ))}
    </div>
  );
}

function ProjectGroup({ project, chats, open, onToggle, onMenu, children }: { project: Project; chats: Chat[]; open: boolean; onToggle: (open: boolean) => void; onMenu: (state: ProjectMenuState) => void; children: React.ReactNode }) {
  void chats;
  const more = useRef<HTMLButtonElement>(null);
  return (
    <div className="project-group" data-open={open || undefined}>
      <div className="project-head" onContextMenu={(event) => { event.preventDefault(); onMenu({ project, anchor: { x: event.clientX, y: event.clientY } }); }}>
        <button
          type="button"
          className="project-toggle"
          aria-expanded={open}
          title="Новый чат в проекте"
          onClick={(event) => {
            // стрелка раскрывает и сворачивает список, остальная строка открывает пустой чат в проекте
            if ((event.target as Element).closest('.project-chevron')) { onToggle(!open); return; }
            if (!open) onToggle(true);
            newChat(project);
          }}
        >
          <ChevronRight className="project-chevron" aria-hidden="true" />
          <Folder aria-hidden="true" />
          <span title={project.name}>{project.name}</span>
        </button>
        <button type="button" className="icon-btn" aria-label={`Новый чат в проекте ${project.name}`} onClick={() => newChat(project)}>
          <Plus aria-hidden="true" />
        </button>
        <button ref={more} type="button" className="icon-btn" aria-label={`Действия с проектом ${project.name}`} aria-haspopup="menu" onClick={() => onMenu({ project, anchor: more })}>
          <Ellipsis aria-hidden="true" />
        </button>
      </div>
      <div className="project-body" inert={!open}>
        <div className="project-body-inner">{children}</div>
      </div>
    </div>
  );
}

export function ChatLists() {
  const scope = useUi((state) => state.scope);
  const development = scope === 'development';
  const localChats = useData((state) => state.localChats);
  const cloudChats = useData((state) => state.cloudChats);
  const projects = useData((state) => state.projects);
  const cloudNext = useData((state) => state.cloudNext);
  const openProjects = useData((state) => state.openProjects);
  const query = useSidebarQuery().trim().toLowerCase();
  const selectedId = useView((state) => state.chat?.id);
  const currentProject = useView((state) => state.project);
  const now = useNow();
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [projectMenu, setProjectMenu] = useState<ProjectMenuState | null>(null);
  const remoteStatus = useRemote((state) => state.status);
  const phone = development && remoteStatus?.enabled === true;
  const menuChatId = menu?.chat.id;
  // видимость могла смениться с телефона (новая сессия, удаление в вебе): перед показом пункта сверяемся
  useEffect(() => { if (development && menuChatId !== undefined) refreshRemote(); }, [development, menuChatId]);

  const matches = (chat: Chat) => !query || (chat.title ?? '').toLowerCase().includes(query);
  const visible = (development ? localChats : cloudChats).filter(matches);
  // чаты без проекта живут в скрытой папке «Докки»: её нет в списке проектов, поэтому они в «Недавнем»
  const known = useMemo(() => new Set(projects.map((project) => project.id)), [projects]);
  const free = development ? visible.filter((chat) => chat.project_id == null || !known.has(chat.project_id)) : visible;

  const groups = useMemo(() => {
    if (!development) return [];
    const latest = new Map<number, number>();
    for (const chat of localChats) {
      if (chat.project_id != null && stamp(chat) > (latest.get(chat.project_id) ?? 0)) latest.set(chat.project_id, stamp(chat));
    }
    return [...projects]
      .sort((a, b) => (latest.get(b.id) ?? 0) - (latest.get(a.id) ?? 0))
      .map((project) => ({ project, chats: visible.filter((chat) => chat.project_id === project.id) }))
      .filter(({ project, chats }) => !query || chats.length || project.name.toLowerCase().includes(query));
  }, [development, localChats, projects, visible, query]);

  return (
    <>
      {development && (
        <section className="sidebar-projects" aria-label="Рабочие проекты">
          <h2 className="section-title">Проекты</h2>
          <div id="projects">
            {!projects.length && <span className="list-empty">Проектов пока нет</span>}
            {groups.map(({ project, chats }) => (
              <ProjectGroup
                key={project.id}
                project={project}
                chats={chats}
                open={currentProject?.id === project.id || openProjects.includes(String(project.id))}
                onToggle={(open) => setProjectOpen(project.id, open)}
                onMenu={setProjectMenu}
              >
                <ChatList chats={chats} group={`project:${project.id}`} now={now} selectedId={selectedId} onMenu={setMenu} empty="Нет чатов" />
              </ProjectGroup>
            ))}
          </div>
        </section>
      )}
      <section className="recent-chats" aria-label="Недавние чаты">
        <div className="section-head">
          <h2 className="section-title">Недавнее</h2>
          {development && (
            <button type="button" className="icon-btn" title="Новый чат без проекта" aria-label="Новый чат без проекта" onClick={() => newChat(null)}>
              <Plus aria-hidden="true" />
            </button>
          )}
        </div>
        <div id="chats">
          <ChatList
            chats={free}
            group={development ? 'free:development' : `free:${scope === 'search' ? 'chat' : scope}`}
            now={now}
            selectedId={selectedId}
            onMenu={setMenu}
            empty={query ? 'Ничего не найдено' : 'Чатов пока нет'}
          />
          {!development && cloudNext && <Sentinel scroller=".sidebar-scroll" onReach={() => { void loadCloud(cloudNext).catch(() => undefined); }} />}
        </div>
      </section>
      <Popover open={Boolean(projectMenu)} anchor={projectMenu?.anchor ?? null} onClose={() => setProjectMenu(null)} label="Действия с проектом" align="end">
        <button type="button" role="menuitem" className="menu-item" onClick={() => { const project = projectMenu?.project; setProjectMenu(null); if (project) newChat(project); }}><span>Новый чат в проекте</span></button>
        <button
          type="button"
          role="menuitem"
          className="menu-item"
          onClick={(event) => { const project = projectMenu?.project; setProjectMenu(null); if (project) guarded(() => copyText(project.path, event, 'Путь к проекту скопирован'))(); }}
        >
          <span>Копировать путь</span>
        </button>
        <button
          type="button"
          role="menuitem"
          className="menu-item danger"
          onClick={() => {
            const project = projectMenu?.project;
            setProjectMenu(null);
            if (!project) return;
            guarded(removeProject)(project);
          }}
        >
          <span>Убрать из списка</span>
        </button>
      </Popover>
      <Popover open={Boolean(menu)} anchor={menu?.anchor ?? null} onClose={() => setMenu(null)} label="Действия с чатом" align="end">
        {!development && (
          <button type="button" role="menuitem" className="menu-item" onClick={() => { const chat = menu?.chat; setMenu(null); if (chat) guarded(togglePin)(chat); }}><span>{menu?.chat.pinned ? 'Открепить' : 'Закрепить'}</span></button>
        )}
        <button type="button" role="menuitem" className="menu-item" onClick={() => { const chat = menu?.chat; setMenu(null); if (chat) guarded(renameChat)(chat); }}><span>Переименовать</span></button>
        {phone && menu && (
          <button
            type="button"
            role="menuitem"
            className="menu-item"
            onClick={() => { const chat = menu.chat; const shown = sharedOnPhone(remoteStatus, chat.id); setMenu(null); guarded(setChatShared)(chat.id, !shown); }}
          ><span>{sharedOnPhone(remoteStatus, menu.chat.id) ? 'Не показывать на телефоне' : 'Показывать на телефоне'}</span></button>
        )}
        <button type="button" role="menuitem" className="menu-item danger" onClick={() => { const chat = menu?.chat; setMenu(null); if (chat) guarded(deleteChat)(chat); }}><span>Удалить</span></button>
      </Popover>
    </>
  );
}


import {
  CalendarClock, Ellipsis, FolderOpen, GitPullRequest, Globe, Images, Layers, ListChecks, MessageCircle, ScrollText, Search, SquareTerminal, X,
} from 'lucide-react';
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { guarded } from '../../state/overlay';
import { openLibrary, openPage, switchScope } from '../../state/remote';
import { setSidebarQuery, useSidebarQuery } from '../../state/sidebarQuery';
import { openSection, useSurface } from '../../state/surface';
import { useUi } from '../../state/ui';
import { useView } from '../../state/view';

/** В ряду помещается пять плиток; если разделов больше, последняя плитка - «Ещё», остальные раскрываются под ней. */
const ROW = 5;

interface TileSpec {
  key: string;
  id?: string;
  label: string;
  icon: ReactNode;
  run: () => void;
  active?: boolean;
  pressed?: boolean;
  badge?: boolean;
}

/** Иконка раздела сайта: SVG-маска, которую прислала страница сайта. */
export function SurfaceIcon({ icon, fallback }: { icon: string; fallback: ReactNode }) {
  if (!icon) return <>{fallback}</>;
  return <span className="surface-icon" style={{ '--icon': `url("${icon}")` } as CSSProperties} aria-hidden="true" />;
}

function Tile({ spec }: { spec: TileSpec }) {
  return (
    <button
      id={spec.id}
      type="button"
      className="side-tile"
      aria-label={spec.label}
      title={spec.label}
      aria-current={spec.active ? 'page' : undefined}
      aria-pressed={spec.pressed}
      onClick={spec.run}
    >
      {spec.icon}
      {spec.badge && <span className="surface-badge" aria-hidden="true" />}
    </button>
  );
}

/** Поле поиска чатов выезжает под плитками и фильтрует список на месте. */
function SearchField({ open, onClose }: { open: boolean; onClose: () => void }) {
  const query = useSidebarQuery();
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (open) input.current?.focus({ preventScroll: true });
  }, [open]);
  return (
    <div id="chat-search-row" className="side-search" data-open={open || undefined}>
      <div className="side-search-inner">
        <div className="side-search-field">
          <Search aria-hidden="true" />
          <input
            id="chat-search-input"
            ref={input}
            type="search"
            placeholder="Поиск чатов"
            aria-label="Поиск чатов"
            autoComplete="off"
            tabIndex={open ? 0 : -1}
            value={query}
            onChange={(event) => setSidebarQuery(event.target.value)}
            onBlur={() => { if (!query.trim()) window.setTimeout(onClose, 120); }}
            onKeyDown={(event) => {
              if (event.key !== 'Escape') return;
              event.preventDefault();
              event.stopPropagation();
              onClose();
            }}
          />
          <button id="chat-search-close" type="button" className="icon-btn" aria-label="Закрыть поиск" tabIndex={open ? 0 : -1} onClick={onClose}>
            <X aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>
  );
}

/** Ряд плиток разделов. Плитка режима: «>_» включает разработку, «пузырь» возвращает в чат. */
export function Nav() {
  const development = useUi((state) => state.scope === 'development');
  const page = useView((state) => state.page);
  // в режиме чата разделы задаёт сайт: что видно в его меню (с учётом доступа), то и здесь
  const site = useSurface((state) => state.status !== 'off') && !development;
  const sections = useSurface((state) => state.sections);
  const [more, setMore] = useState(false);
  const [searching, setSearching] = useState(false);
  const closeSearch = () => {
    setSidebarQuery('');
    setSearching(false);
  };
  const page$ = (key: Exclude<typeof page, 'chat'>, label: string, icon: ReactNode, id?: string): TileSpec => ({
    key, id, label, icon, active: page === key, run: () => openPage(key),
  });
  const tiles: TileSpec[] = [
    { key: 'search', id: 'chat-search', label: 'Поиск чатов', icon: <Search aria-hidden="true" />, pressed: searching, run: () => (searching ? closeSearch() : setSearching(true)) },
    {
      key: 'mode',
      id: 'scope-toggle',
      label: development ? 'Вернуться в режим чата' : 'Режим разработки',
      icon: development ? <MessageCircle aria-hidden="true" /> : <SquareTerminal aria-hidden="true" />,
      pressed: development,
      run: guarded(() => switchScope(development ? 'chat' : 'development')),
    },
    ...(development
      ? [
          page$('projects', 'Проекты', <FolderOpen aria-hidden="true" />),
          page$('mission', 'Задачи', <ListChecks aria-hidden="true" />),
          page$('automations', 'Автоматизации', <CalendarClock aria-hidden="true" />),
          page$('review', 'Проверка изменений', <GitPullRequest aria-hidden="true" />),
          page$('skills', 'Навыки', <Layers aria-hidden="true" />),
          page$('sources', 'Источники', <Globe aria-hidden="true" />),
          page$('logs', 'Журнал событий', <ScrollText aria-hidden="true" />),
        ]
      : site
        ? sections.map((section): TileSpec => ({
            key: `site-${section.key}`,
            id: `site-section-${section.key}`,
            label: section.label,
            icon: <SurfaceIcon icon={section.icon} fallback={<Globe aria-hidden="true" />} />,
            active: section.active,
            badge: section.badge,
            run: () => { void openSection(section.key); },
          }))
        : [{ key: 'library', id: 'cloud-library-button', label: 'Библиотека', icon: <Images aria-hidden="true" />, active: page === 'library', run: openLibrary }]),
  ];
  const overflow = tiles.length > ROW;
  const visible = overflow ? tiles.slice(0, ROW - 1) : tiles;
  const hidden = overflow ? tiles.slice(ROW - 1) : [];
  return (
    <nav className="sidebar-nav" aria-label="Разделы">
      <div className="side-tiles" role="group">
        {visible.map((spec) => <Tile key={spec.key} spec={spec} />)}
        {overflow && (
          <button id="side-more" type="button" className="side-tile" aria-label="Ещё" title="Ещё" aria-expanded={more} onClick={() => setMore((state) => !state)}>
            <Ellipsis aria-hidden="true" />
          </button>
        )}
      </div>
      {overflow && (
        <div className="side-more" data-open={more || undefined}>
          <div className="side-more-inner" inert={!more}>
            <div className="side-tiles more-tiles">
              {hidden.map((spec) => <Tile key={spec.key} spec={spec} />)}
            </div>
          </div>
        </div>
      )}
      <SearchField open={searching} onClose={closeSearch} />
    </nav>
  );
}

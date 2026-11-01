import { useEffect, useMemo, useRef, useState } from 'react';
import { switchScope } from '../../state/actions';
import { useData } from '../../state/data';
import { guarded } from '../../state/overlay';
import { closePalette, paletteResults, usePalette, type PaletteItem } from '../../state/palette';
import { openSettings } from '../../state/settingsUi';
import { startChat } from '../../state/surface';
import { ui } from '../../state/ui';
import { openChat, pageNames } from '../../state/view';
import { settingsEntries } from '../settings/SettingsDialog';
import { Dialog } from '../ui/Dialog';

// чуть дольше закрывающей анимации Dialog
const PALETTE_CLOSE_MS = 170;
const mac = /mac/i.test(navigator.platform);
const key = (letter: string) => (mac ? `⌘${letter}` : `Ctrl+${letter}`);

const COMMANDS: PaletteItem[] = [
  { key: 'new-chat', kind: 'Команда', title: 'Новый чат', action: 'new-chat', hint: key('N') },
  { key: 'chat-search', kind: 'Команда', title: 'Найти в чате', action: 'chat-search', hint: key('F'), keywords: 'поиск' },
  { key: 'toggle-left', kind: 'Команда', title: 'Главное меню', action: 'toggle-left', keywords: 'панель слева скрыть показать' },
  { key: 'toggle-right', kind: 'Команда', title: 'Изменения и файлы', action: 'toggle-right', keywords: 'панель справа инспектор diff' },
  { key: 'page-review', kind: 'Команда', title: pageNames.review, action: 'page-review', keywords: 'все правки откат diff' },
  { key: 'page-projects', kind: 'Команда', title: pageNames.projects, action: 'page-projects' },
  { key: 'page-skills', kind: 'Команда', title: pageNames.skills, action: 'page-skills', keywords: 'плагины' },
  { key: 'page-logs', kind: 'Команда', title: pageNames.logs, action: 'page-logs' },
  { key: 'page-automations', kind: 'Команда', title: pageNames.automations, action: 'page-automations' },
  { key: 'page-sources', kind: 'Команда', title: pageNames.sources, action: 'page-sources' },
  { key: 'page-mission', kind: 'Команда', title: pageNames.mission, action: 'page-mission' },
  { key: 'scope-development', kind: 'Команда', title: 'Режим кода', action: 'scope-development', keywords: 'разработка докки code' },
  { key: 'scope-chat', kind: 'Команда', title: 'Режим чата', action: 'scope-chat' },
  { key: 'scope-search', kind: 'Команда', title: 'Режим поиска', action: 'scope-search' },
];

async function runItem(item: PaletteItem): Promise<void> {
  if (item.chat) {
    if (ui.get().scope !== 'development') await switchScope('development');
    openChat(item.chat);
  } else if (item.project) {
    if (ui.get().scope !== 'development') await switchScope('development');
    await startChat(item.project);
  } else if (item.section) {
    openSettings(item.section as never);
  } else if (item.action) {
    window.koddiMenu?.(item.action);
  }
}

/** Окно быстрого перехода: поле и список, стрелки двигают выбор, Enter выполняет. */
export function CommandPalette() {
  const open = usePalette((s) => s.open);
  const chats = useData((s) => s.localChats);
  const projects = useData((s) => s.projects);
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  const list = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const items = useMemo<PaletteItem[]>(() => [
    ...COMMANDS,
    ...chats.map((chat) => ({ key: `chat:${chat.id}`, kind: 'Чат' as const, title: chat.title || 'Без названия', chat })),
    ...projects.map((project) => ({ key: `project:${project.id}`, kind: 'Проект' as const, title: project.name, keywords: project.path, project })),
    ...settingsEntries.map((entry) => ({ key: `settings:${entry.id}`, kind: 'Настройки' as const, title: entry.title, keywords: entry.keywords, section: entry.id })),
  ], [chats, projects]);
  const results = useMemo(() => (open ? paletteResults(items, query) : []), [items, query, open]);
  useEffect(() => {
    if (!open) return undefined;
    setQuery('');
    setSelected(0);
    try { window.koddiWindowBridge?.focusView?.('main'); } catch { /* окно без моста */ }
    const frame = requestAnimationFrame(() => input.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [open]);
  useEffect(() => setSelected(0), [query]);
  useEffect(() => {
    list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [selected]);
  const choose = (item: PaletteItem | undefined) => {
    if (!item) return;
    closePalette();
    // окно при закрытии возвращает фокус туда, где он был: действие должно получить его уже после этого
    window.setTimeout(() => guarded(runItem)(item), PALETTE_CLOSE_MS);
  };
  return (
    <Dialog open={open} onClose={closePalette} className="command-palette" label="Быстрый переход">
      <input
        ref={input}
        type="text"
        value={query}
        placeholder="Чаты, проекты, команды, настройки"
        aria-label="Быстрый переход"
        aria-controls="palette-list"
        spellCheck={false}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            if (results.length) setSelected((value) => (value + (event.key === 'ArrowDown' ? 1 : -1) + results.length) % results.length);
          } else if (event.key === 'Enter') {
            event.preventDefault();
            choose(results[selected]);
          }
        }}
      />
      <div ref={list} id="palette-list" className="command-palette-list" role="listbox" aria-label="Результаты">
        {results.map((item, index) => (
          <button
            key={item.key}
            type="button"
            role="option"
            tabIndex={-1}
            aria-selected={index === selected}
            onPointerMove={() => setSelected(index)}
            onClick={() => choose(item)}
          >
            <span>{item.title}</span>
            {(item.hint || item.kind !== 'Команда') && <small>{item.hint ?? item.kind}</small>}
          </button>
        ))}
        {!results.length && <div className="command-palette-empty">Ничего не нашлось</div>}
      </div>
    </Dialog>
  );
}

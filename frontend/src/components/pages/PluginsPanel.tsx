import { Download, ExternalLink, Power, RefreshCw, ShieldAlert, ShieldCheck, Star, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { openExternal } from '../../lib/external';
import { confirmDialog, guarded, notify } from '../../state/overlay';
import {
  decideExec, filterCatalog, installFromCatalog, installFromSource, loadCatalog, loadPlugins, removePlugin, setPluginEnabled, usePlugins,
  type CatalogItem, type Plugin, type PluginChoice,
} from '../../state/plugins';
import { Empty, Hint } from './shared';

const plural = (count: number, one: string, few: string, many: string) => {
  const tail = count % 100;
  const last = count % 10;
  const word = tail > 10 && tail < 20 ? many : last === 1 ? one : last >= 2 && last <= 4 ? few : many;
  return `${count} ${word}`;
};

export const componentsLine = (counts: { skills: number; commands: number; agents: number; hooks: boolean; mcp: boolean }): string =>
  [
    counts.skills && plural(counts.skills, 'навык', 'навыка', 'навыков'),
    counts.commands && plural(counts.commands, 'команда', 'команды', 'команд'),
    counts.agents && plural(counts.agents, 'агент', 'агента', 'агентов'),
    counts.hooks && 'хуки',
    counts.mcp && 'MCP',
  ].filter(Boolean).join(' · ');

const source = (repo: string, path: string) => (path ? `${repo}/${path}` : repo);

/** Хуки и MCP - исполняемые части: показываем точные команды и разрешаем только эту версию плагина. */
function Executable({ plugin }: { plugin: Plugin }) {
  const allow = guarded(async () => {
    const hooks = plugin.hooks.filter((hook) => hook.supported).length;
    const text = [
      'Докки будет запускать на этом компьютере команды плагина из списка в карточке:',
      hooks ? `хуки (${hooks}) - в начале чата и перед каждым сообщением в проекте;` : '',
      plugin.mcp.length ? `MCP-серверы (${plugin.mcp.length}) - их инструменты агент вызывает через обычные подтверждения.` : '',
      'Разрешение действует только для этой версии. После обновления плагина Докки спросит снова.',
    ].filter(Boolean).join('\n');
    if (!(await confirmDialog(`Разрешить запуск «${plugin.title}»?`, text, 'Разрешить'))) return;
    await decideExec(plugin, true);
  });
  const revoke = guarded(async () => { await decideExec(plugin, false); });
  return (
    <div className="plugin-exec" data-approved={plugin.exec_approved || undefined}>
      <p className="plugin-exec-state">
        {plugin.exec_approved ? <ShieldCheck aria-hidden="true" /> : <ShieldAlert aria-hidden="true" />}
        <span>{plugin.exec_approved ? 'Запуск хуков и MCP разрешён для этой версии' : 'Хуки и MCP не запускаются, пока вы не разрешите'}</span>
      </p>
      <ul>
        {plugin.hooks.map((hook, index) => (
          <li key={`h${index}`}>
            <b>{hook.event}{hook.matcher ? ` (${hook.matcher})` : ''}</b>
            {!hook.supported && <small> - Докки это событие не исполняет</small>}
            <code>{hook.command || hook.type}</code>
          </li>
        ))}
        {plugin.mcp.map((server) => (
          <li key={`m${server.name}`}>
            <b>MCP {server.name}</b>
            <code>{server.url || [server.command, ...server.args].join(' ')}</code>
            {server.cwd && <small>Папка: {server.cwd}</small>}
            {server.env.length > 0 && <small>Окружение: {server.env.map((item) => `${item.name}=${item.value}`).join(', ')}</small>}
            {server.headers.length > 0 && <small>Заголовки: {server.headers.map((item) => `${item.name}: ${item.value}`).join(', ')}</small>}
          </li>
        ))}
      </ul>
      {plugin.exec_approved
        ? <button type="button" className="secondary" onClick={revoke}>Запретить</button>
        : <button type="button" className="secondary" onClick={allow}>Разрешить запуск</button>}
    </div>
  );
}

function PluginCard({ plugin }: { plugin: Plugin }) {
  const toggle = guarded(async () => { await setPluginEnabled(plugin, !plugin.enabled); });
  const remove = guarded(async () => {
    if (!(await confirmDialog('Удалить плагин?', `${plugin.title}: файлы плагина будут удалены из данных Докки.`, 'Удалить'))) return;
    await removePlugin(plugin);
  });
  const counts = { skills: plugin.skills.length, commands: plugin.commands.length, agents: plugin.agents.length, hooks: plugin.hooks.length > 0, mcp: plugin.mcp.length > 0 };
  return (
    <article className="skill-card plugin-card" data-off={plugin.enabled ? undefined : true}>
      <div className="skill-head">
        <h3>{plugin.title}</h3>
        <span className="skill-badge">{plugin.enabled ? 'Включён' : 'Выключен'}</span>
      </div>
      {plugin.description && <p>{plugin.description}</p>}
      <p className="plugin-meta">
        {source(plugin.repo, plugin.path)} · {plugin.version ? `v${plugin.version} · ` : ''}{plugin.commit.slice(0, 7)}{plugin.license ? ` · ${plugin.license}` : ''}
      </p>
      <p className="plugin-meta">{componentsLine(counts)}</p>
      <details className="skill-details">
        <summary>Состав</summary>
        <ul className="plugin-list">
          {plugin.skills.map((item) => <li key={`s${item.name}`}><b>/{plugin.name}:{item.name}</b> {item.description}</li>)}
          {plugin.commands.map((item) => <li key={`c${item.name}`}><b>/{plugin.name}:{item.name}</b>{item.argument_hint ? ` ${item.argument_hint}` : ''} {item.description}</li>)}
          {plugin.agents.map((item) => <li key={`a${item.name}`}><b>агент {item.name}</b> {item.description}</li>)}
        </ul>
        {plugin.agents.length > 0 && <p className="plugin-hint">Отдельных субагентов в Докки нет: агент читает роль из файла плагина и проходит её сам.</p>}
        {plugin.skipped_links.length > 0 && <p className="plugin-hint">Ссылки в архиве не распакованы: {plugin.skipped_links.slice(0, 5).join(', ')}</p>}
      </details>
      {plugin.executable && <Executable plugin={plugin} />}
      <div className="skill-actions">
        <button type="button" className="icon-button" title="Открыть на GitHub" aria-label="Открыть на GitHub" onClick={() => openExternal(plugin.url)}><ExternalLink aria-hidden="true" /></button>
        <button type="button" className="icon-button" aria-pressed={plugin.enabled} title={plugin.enabled ? 'Выключить плагин' : 'Включить плагин'} aria-label={plugin.enabled ? 'Выключить плагин' : 'Включить плагин'} onClick={toggle}><Power aria-hidden="true" /></button>
        <button type="button" className="icon-button" title="Удалить" aria-label="Удалить плагин" onClick={remove}><Trash2 aria-hidden="true" /></button>
      </div>
    </article>
  );
}

function InstallForm() {
  const [value, setValue] = useState('');
  const [choices, setChoices] = useState<PluginChoice[]>([]);
  const busy = usePlugins((s) => s.busy.some((item) => item.startsWith('source:')));
  const install = guarded(async (path: string = '') => {
    const text = value.trim();
    if (!text) return;
    const result = await installFromSource(text, path);
    if (result.needs_choice) {
      setChoices(result.choices ?? []);
      return;
    }
    setChoices([]);
    setValue('');
    if (result.plugin) notify(result.installed ? `Плагин «${result.plugin.title}» установлен` : `«${result.plugin.title}» уже установлен`);
  });
  return (
    <form className="plugin-install" onSubmit={(event) => { event.preventDefault(); install(); }}>
      <label><span className="sr-only">Репозиторий GitHub</span>
        <input className="page-input" value={value} maxLength={500} placeholder="https://github.com/owner/repo или owner/repo" onChange={(event) => { setValue(event.target.value); setChoices([]); }} />
      </label>
      <button type="submit" className="primary" disabled={busy || !value.trim()}><Download aria-hidden="true" /><span>{busy ? 'Устанавливаю…' : 'Установить'}</span></button>
      {choices.length > 0 && (
        <div className="plugin-choices" role="group" aria-label="Плагины репозитория">
          <p>В репозитории несколько плагинов, выберите один:</p>
          {choices.map((choice) => (
            <button key={choice.path} type="button" className="menu-item" disabled={busy} onClick={() => install(choice.path)}>
              <span><b>{choice.name}</b><small>{choice.description || choice.path}</small></span>
            </button>
          ))}
        </div>
      )}
    </form>
  );
}

/** Установленные плагины: включение, удаление, разрешение хуков и MCP. */
export function PluginsPanel({ onCatalog }: { onCatalog: () => void }) {
  const items = usePlugins((s) => s.items);
  const loaded = usePlugins((s) => s.loaded);
  const error = usePlugins((s) => s.error);
  useEffect(() => { void loadPlugins(); }, []);
  return (
    <>
      <Hint>Плагин с GitHub добавляет навыки, команды и роли агентов. Докки скачивает его только с github.com, закрепляет на коммит и сверяет хэш каждого файла. Навыки и команды подключаются как обычные навыки или словом /плагин:команда в начале сообщения. Хуки и MCP-серверы плагина не запускаются, пока вы их не разрешите.</Hint>
      <InstallForm />
      {error && !items.length && <Empty title="Не удалось загрузить плагины" detail={error} />}
      {loaded && !items.length && !error && (
        <Empty icon={<Download size={24} aria-hidden="true" />} title="Плагинов пока нет" detail="Вставьте ссылку на репозиторий или выберите плагин в каталоге">
          <button type="button" className="secondary" onClick={onCatalog}>Открыть каталог</button>
        </Empty>
      )}
      <div className="skill-grid">
        {items.map((plugin) => <PluginCard key={plugin.id} plugin={plugin} />)}
      </div>
    </>
  );
}

function CatalogCard({ item }: { item: CatalogItem }) {
  const busy = usePlugins((s) => s.busy.includes(item.id));
  const install = guarded(async () => {
    const plugin = await installFromCatalog(item.id);
    if (plugin) notify(`Плагин «${plugin.title}» ${item.update ? 'обновлён' : 'установлен'}`);
  });
  const installed = item.installed_id !== null && !item.update;
  return (
    <article className="skill-card plugin-card">
      <div className="skill-head">
        <h3>{item.title}</h3>
        <span className="plugin-stars" title="Звёзды репозитория на GitHub"><Star aria-hidden="true" />{item.stars.toLocaleString('ru-RU')}</span>
      </div>
      <p>{item.description}</p>
      <p className="plugin-meta">{source(item.repo, item.path)} · {item.ref || item.commit.slice(0, 7)}{item.license ? ` · ${item.license}` : ''}</p>
      <p className="plugin-meta">{componentsLine(item.components)}</p>
      <div className="skill-actions">
        <button type="button" className="primary" disabled={busy || installed} onClick={install}>
          {item.update ? <RefreshCw aria-hidden="true" /> : <Download aria-hidden="true" />}
          <span>{busy ? 'Устанавливаю…' : installed ? 'Установлен' : item.update ? 'Обновить' : 'Установить'}</span>
        </button>
        <button type="button" className="icon-button" title="Открыть на GitHub" aria-label="Открыть на GitHub" onClick={() => openExternal(`https://github.com/${item.repo}/tree/${item.commit}${item.path ? `/${item.path}` : ''}`)}><ExternalLink aria-hidden="true" /></button>
      </div>
    </article>
  );
}

/** Каталог популярных открытых плагинов, каждый закреплён на проверенный коммит. */
export function PluginCatalog() {
  const items = usePlugins((s) => s.catalog);
  const loaded = usePlugins((s) => s.catalogLoaded);
  const checkedAt = usePlugins((s) => s.checkedAt);
  const error = usePlugins((s) => s.error);
  const [query, setQuery] = useState('');
  useEffect(() => { void loadCatalog(); }, []);
  const found = filterCatalog(items, query);
  return (
    <>
      <Hint>{`Популярные бесплатные плагины с открытым кодом для Claude Code и Codex: работают без отдельных аккаунтов и сервисов. Звёзды и коммиты проверены ${checkedAt ? new Date(checkedAt).toLocaleDateString('ru-RU') : ''}.`}</Hint>
      <label className="page-search"><span className="sr-only">Найти плагин</span>
        <input className="page-input" type="search" placeholder="Найти плагин" value={query} onChange={(event) => setQuery(event.target.value)} />
      </label>
      {error && !items.length && <Empty title="Не удалось загрузить каталог" detail={error} />}
      {loaded && !found.length && !error && <Empty title="Ничего не найдено" />}
      <div className="skill-grid">
        {found.map((item) => <CatalogCard key={item.id} item={item} />)}
      </div>
    </>
  );
}

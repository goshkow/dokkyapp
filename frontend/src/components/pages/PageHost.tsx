import {
  Download, FileCode2, FolderOpen, Link as LinkIcon, Plus, RefreshCw, Search, Trash2, Undo2,
} from 'lucide-react';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, del, post } from '../../lib/api';
import { changeDiffModel, diffStats, pruneDiffCache, type Change } from '../../lib/diff';
import { createProject, openFolder } from '../../state/actions';
import { loadLocal } from '../../state/data';
import { confirmDialog, guarded, notify } from '../../state/overlay';
import { newChat, useView, type PageKey } from '../../state/view';
import { LibraryPage } from '../library/LibraryPage';
import { DiffView } from '../chat/DiffView';
import { AutomationsPage } from './AutomationsPage';
import { Empty, Frame, PageRoot, usePage } from './shared';
import { SkillsPage } from './SkillsPage';
import { TasksPage } from './TasksPage';

/* проекты */
interface ProjectItem { id: number; name: string; path: string }

function Projects() {
  const [load, retry] = usePage((signal) => api<ProjectItem[]>('/api/projects', { signal }), 'projects');
  return (
    <Frame title="Проекты" load={load} retry={retry}>
      {(projects) => (
        <PageRoot
          title="Проекты"
          subtitle={`${projects.length} ${projects.length === 1 ? 'проект' : 'проектов'}`}
          commands={(
            <>
              <button type="button" className="secondary" onClick={guarded(openFolder)}><FolderOpen aria-hidden="true" /><span>Открыть папку</span></button>
              <button type="button" className="primary" onClick={guarded(createProject)}><Plus aria-hidden="true" /><span>Новый проект</span></button>
            </>
          )}
        >
          {!projects.length ? <Empty title="Проектов пока нет" /> : projects.map((project) => (
            <div key={project.id} className="project-row">
              <span className="project-symbol"><FolderOpen size={22} aria-hidden="true" /></span>
              <div><b>{project.name}</b><p className="muted">{project.path}</p></div>
              <div className="actions">
                <button type="button" className="secondary" onClick={() => newChat(project)}><FolderOpen aria-hidden="true" /><span>Открыть</span></button>
                <button
                  type="button"
                  className="icon-button"
                  title="Убрать из списка"
                  aria-label="Убрать из списка"
                  onClick={guarded(async () => {
                    if (!(await confirmDialog('Убрать проект?', 'Файлы останутся на диске', 'Убрать'))) return;
                    await del(`/api/projects/${project.id}`);
                    await loadLocal(false);
                    retry();
                  })}
                >
                  <Trash2 aria-hidden="true" />
                </button>
              </div>
            </div>
          ))}
        </PageRoot>
      )}
    </Frame>
  );
}

/* журнал событий */
interface LogRow { level?: string; event?: string; created_at?: string; message?: string; data_json?: string; metadata_json?: string }

function Logs() {
  const [load, retry] = usePage((signal) => api<LogRow[]>('/api/logs?limit=150', { signal }), 'logs');
  const [query, setQuery] = useState('');
  return (
    <Frame title="Журнал" load={load} retry={retry}>
      {(rows) => {
        const shown = rows.filter((row) => JSON.stringify(row).toLowerCase().includes(query.toLowerCase()));
        return (
          <PageRoot
            title="Журнал"
            subtitle={`${rows.length} записей`}
            commands={(
              <>
                <button type="button" className="icon-button" title="Экспорт журнала" aria-label="Экспорт журнала" onClick={() => window.open('/api/logs/export', '_blank')}><Download aria-hidden="true" /></button>
                <button type="button" className="icon-button" title="Обновить" aria-label="Обновить" onClick={retry}><RefreshCw aria-hidden="true" /></button>
              </>
            )}
          >
            <label className="search-field">
              <Search aria-hidden="true" />
              <input type="search" placeholder="Поиск по журналу" aria-label="Поиск по журналу" value={query} onChange={(event) => setQuery(event.target.value)} />
            </label>
            <div className="log-list">
              {!shown.length ? <Empty title="Записей не найдено" /> : shown.map((row, index) => (
                <details key={index} className="log-row">
                  <summary>
                    <span className={`badge ${row.level?.toLowerCase()}`}>{row.level}</span>
                    <span>{row.event}</span>
                    <time>{row.created_at}</time>
                  </summary>
                  <p>{row.message}</p>
                  <pre>{row.data_json || row.metadata_json || ''}</pre>
                </details>
              ))}
            </div>
          </PageRoot>
        );
      }}
    </Frame>
  );
}

/* проверка изменений */
function ReviewFile({ change, onDone }: { change: Change; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const files = useMemo(() => changeDiffModel(change), [change]);
  const counts = useMemo(() => diffStats(files), [files]);
  return (
    <details className="review-file" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        <FileCode2 aria-hidden="true" />
        <span>{change.description}</span>
        <span className="positive">+{counts.add}</span>
        <span className="danger">−{counts.remove}</span>
      </summary>
      {open && <DiffView files={files} id={String(change.id)} />}
      <div className="actions">
        <button
          type="button"
          className="secondary"
          disabled={change.status === 'rolled_back'}
          onClick={guarded(async () => {
            if (!(await confirmDialog('Откатить изменения?', change.description, 'Откатить'))) return;
            const result = await post<{ rolled_back: boolean; reason?: string }>(`/api/changes/${change.id}/rollback`);
            if (!result.rolled_back) throw new Error(result.reason || 'Откат не выполнен');
            notify('Изменения отменены');
            onDone();
          })}
        >
          <Undo2 aria-hidden="true" /><span>{change.status === 'rolled_back' ? 'Откачено' : 'Откатить'}</span>
        </button>
      </div>
    </details>
  );
}

function Review() {
  const project = useView((s) => s.project);
  const [load, retry] = usePage((signal) => api<Change[]>(`/api/changes${project ? `?project_id=${project.id}` : ''}`, { signal }), `review:${project?.id ?? ''}`);
  useEffect(() => { if (load.state === 'ready') pruneDiffCache(new Set(load.value.map((change) => change.id))); }, [load]);
  return (
    <Frame title="Изменения" load={load} retry={retry}>
      {(changes) => (
        <PageRoot title="Изменения" subtitle={project?.name || 'Все проекты'}>
          {!changes.length ? <Empty title="Изменений пока нет" /> : changes.map((change) => <ReviewFile key={change.id} change={change} onDone={retry} />)}
        </PageRoot>
      )}
    </Frame>
  );
}

/* источники: поиск в интернете */
interface WebResult { url: string; title: string; snippet: string }

function Sources() {
  const [query, setQuery] = useState('');
  const [state, setState] = useState<'idle' | 'loading' | 'ready'>('idle');
  const [results, setResults] = useState<WebResult[]>([]);
  return (
    <PageRoot title="Источники">
      <form
        className="web-search-form"
        onSubmit={(event) => {
          event.preventDefault();
          guarded(async () => {
            setState('loading');
            const value = await api<{ results?: WebResult[] }>(`/api/web/search?query=${encodeURIComponent(query)}&limit=8`);
            setResults(value.results ?? []);
            setState('ready');
          })();
        }}
      >
        <label className="search-field">
          <Search aria-hidden="true" />
          <input name="query" type="search" required placeholder="Поиск в интернете" aria-label="Поиск в интернете" value={query} onChange={(event) => setQuery(event.target.value)} />
        </label>
        <button type="submit" className="primary"><Search aria-hidden="true" /><span>Найти</span></button>
      </form>
      <div className="search-results">
        {state === 'loading' && <div className="skeleton file-skeleton" />}
        {state === 'ready' && !results.length && <Empty title="Источников не найдено" />}
        {state === 'ready' && results.map((item) => (
          <article key={item.url} className="source-row">
            <a href={item.url} target="_blank" rel="noopener noreferrer">{item.title} <LinkIcon size={14} aria-hidden="true" /></a>
            <p>{item.snippet}</p>
            <small>{item.url}</small>
          </article>
        ))}
      </div>
    </PageRoot>
  );
}

const pages: Partial<Record<PageKey, () => ReactNode>> = {
  library: () => <LibraryPage />,
  projects: () => <Projects />,
  mission: () => <TasksPage />,
  logs: () => <Logs />,
  review: () => <Review />,
  skills: () => <SkillsPage />,
  automations: () => <AutomationsPage />,
  sources: () => <Sources />,
};

export function PageHost() {
  const page = useView((s) => s.page);
  return <>{pages[page]?.()}</>;
}

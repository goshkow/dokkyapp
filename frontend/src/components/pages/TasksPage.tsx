import { CircleAlert, CircleCheck, Clock, ListChecks, LoaderCircle, Square } from 'lucide-react';
import { useMemo, useState } from 'react';
import { api } from '../../lib/api';
import { relativeTime, serverTime } from '../../lib/format';
import { data, useData } from '../../state/data';
import { guarded, notify } from '../../state/overlay';
import { openBilling } from '../../state/settingsUi';
import { openChat } from '../../state/view';
import { Empty, Frame, Hint, PageRoot, usePage } from './shared';

export interface Run {
  id: string;
  chat_id: number;
  chat_title: string;
  project_id: number | null;
  project_name: string | null;
  status: string;
  request: string;
  started_at: string;
  finished_at: string | null;
  error: string;
  summary?: string;
}

type Group = 'all' | 'active' | 'queued' | 'done' | 'failed';

/** Какой вкладке принадлежит запрос: идёт, ждёт, готов, с ошибкой. Остановленные вручную идут к готовым. */
export function groupOf(status: string): Exclude<Group, 'all'> {
  if (status === 'running') return 'active';
  if (status === 'queued') return 'queued';
  if (status === 'failed') return 'failed';
  return 'done';
}

const STATUS: Record<string, string> = { running: 'Идёт работа', queued: 'В очереди', completed: 'Готово', failed: 'Ошибка', cancelled: 'Остановлено' };

/** Понятное объяснение ошибки вместо кода. */
export function explain(run: Run): string {
  if (run.error === 'plan_required') return 'Докки Code сейчас недоступен на вашем тарифе';
  if (run.error === 'quota_exceeded' || run.error === 'rate_limited') return 'Закончился лимит использования';
  return run.summary || (run.status === 'failed' ? 'Задача остановилась с ошибкой. Подробности в чате' : '');
}

export function duration(run: Run): string {
  const start = serverTime(run.started_at);
  const end = run.finished_at ? serverTime(run.finished_at) : NaN;
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return '';
  const seconds = Math.round((end - start) / 1000);
  if (seconds < 60) return `${Math.max(1, seconds)} с`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} мин` : `${Math.floor(minutes / 60)} ч ${minutes % 60} мин`;
}

const icon = (status: string) => {
  if (status === 'running') return <LoaderCircle className="spin" aria-hidden="true" />;
  if (status === 'queued') return <Clock aria-hidden="true" />;
  if (status === 'failed') return <CircleAlert aria-hidden="true" />;
  if (status === 'cancelled') return <Square aria-hidden="true" />;
  return <CircleCheck aria-hidden="true" />;
};

const TABS: Array<[Group, string]> = [['all', 'Все'], ['active', 'В работе'], ['queued', 'В очереди'], ['done', 'Готово'], ['failed', 'С ошибкой']];

/** «Задачи»: все запросы к агенту в одном списке, с понятным состоянием и переходом в чат. */
export function TasksPage() {
  const [tab, setTab] = useState<Group>('all');
  const [project, setProject] = useState('');
  const projects = useData((s) => s.projects);
  const [load, retry] = usePage((signal) => api<Run[]>(`/api/runs?limit=150${project ? `&project_id=${project}` : ''}`, { signal }), `runs:${project}`, 5000);
  const open = (run: Run) => {
    const found = data.get().localChats.find((chat) => chat.id === run.chat_id);
    if (found) openChat(found);
    else notify('Этот чат уже удалён');
  };
  return (
    <Frame title="Задачи" load={load} retry={retry}>
      {(runs) => <TaskList runs={runs} tab={tab} onTab={setTab} project={project} onProject={setProject} projects={projects} open={open} />}
    </Frame>
  );
}

function TaskList({ runs, tab, onTab, project, onProject, projects, open }: {
  runs: Run[]; tab: Group; onTab: (tab: Group) => void; project: string; onProject: (id: string) => void; projects: Array<{ id: number; name: string }>; open: (run: Run) => void;
}) {
  const counts = useMemo(() => {
    const value: Record<Group, number> = { all: runs.length, active: 0, queued: 0, done: 0, failed: 0 };
    for (const run of runs) value[groupOf(run.status)] += 1;
    return value;
  }, [runs]);
  const shown = runs.filter((run) => tab === 'all' || groupOf(run.status) === tab);
  return (
    <PageRoot
      title="Задачи"
      subtitle={runs.length ? `${counts.active} в работе, ${counts.queued} в очереди, ${counts.done} готово${counts.failed ? `, ${counts.failed} с ошибкой` : ''}` : undefined}
      commands={projects.length > 1 ? (
        <select className="page-select" aria-label="Проект" value={project} onChange={(event) => onProject(event.target.value)}>
          <option value="">Все проекты</option>
          {projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      ) : undefined}
    >
      <Hint>Каждое ваше сообщение агенту в режиме разработки становится задачей. Здесь видно, что идёт сейчас, что ждёт своей очереди и чем закончилось прежнее. Нажмите на задачу, чтобы открыть её чат.</Hint>
      {runs.length > 0 && (
        <div className="task-tabs" role="tablist" aria-label="Состояние задач">
          {TABS.map(([id, label]) => (
            <button key={id} type="button" role="tab" aria-selected={tab === id} data-tab={id} onClick={() => onTab(id)}>
              {label}{counts[id] > 0 && <span className="count">{counts[id]}</span>}
            </button>
          ))}
        </div>
      )}
      {!runs.length && (
        <Empty icon={<ListChecks size={24} aria-hidden="true" />} title="Задач пока нет" detail="Откройте проект и опишите задачу агенту: запрос появится здесь, и вы сможете следить за ходом работы." />
      )}
      {runs.length > 0 && !shown.length && <Empty icon={<ListChecks size={24} aria-hidden="true" />} title="В этой вкладке пусто" />}
      <div className="task-list">
        {shown.map((run) => {
          const note = explain(run);
          const spent = duration(run);
          return (
            <article key={run.id} className="task-card" data-status={run.status}>
              <button type="button" className="task-main" onClick={guarded(() => open(run))}>
                <span className="task-icon" data-status={run.status}>{icon(run.status)}</span>
                <span className="task-copy">
                  <b>{run.request || run.chat_title || 'Запрос без текста'}</b>
                  <small>
                    {run.project_name ? `${run.project_name} · ` : ''}{run.chat_title}
                    {' · '}{relativeTime(run.started_at) || ''}{relativeTime(run.started_at) && relativeTime(run.started_at) !== 'сейчас' ? ' назад' : ''}
                    {spent ? ` · ${spent}` : ''}
                  </small>
                  {note && <small className="task-note">{note}</small>}
                </span>
                <span className={`badge ${run.status}`}>{STATUS[run.status] ?? run.status}</span>
              </button>
              {run.error === 'plan_required' && <button type="button" className="secondary task-upgrade" onClick={() => openBilling()}>Улучшить план</button>}
            </article>
          );
        })}
      </div>
    </PageRoot>
  );
}

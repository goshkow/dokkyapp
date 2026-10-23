import { CalendarClock, Clock, MessageSquare, Pencil, Play, Plus, Sparkles, Terminal, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { ApiError, api, del, patch, post } from '../../lib/api';
import {
  AI_EXAMPLES, DAY_LABELS, DEFAULT_SCHEDULE, TEMPLATES, WHEN_LABELS, parseSchedule, scheduleString, type Kind, type ScheduleForm, type Template, type When,
} from '../../lib/automation';
import { relativeTime } from '../../lib/format';
import { useDeveloperAccess } from '../../state/access';
import { data, loadLocal, useData } from '../../state/data';
import { confirmDialog, guarded, notify } from '../../state/overlay';
import { openBilling } from '../../state/settingsUi';
import { openChat } from '../../state/view';
import { Dialog } from '../ui/Dialog';
import { Empty, Frame, Hint, PageRoot, usePage } from './shared';

interface Automation {
  id: number; name: string; schedule: string; schedule_text: string; enabled: boolean; status: string; last_result: string;
  last_run_at: string | null; next_run_at: string | null; last_chat_id: number | null; kind: Kind | ''; project_id: number | null; check_title: string;
  action: { kind?: string; prompt?: string; command?: string; tool?: string; project_id?: number };
}

interface Form { id: number | null; name: string; kind: Kind; text: string; tool: string; projectId: string; schedule: ScheduleForm }

const KIND_TEXT: Record<string, string> = { agent: 'Задание агенту', command: 'Команда', tool: 'Проверка' };
const STATUS_TEXT: Record<string, string> = { running: 'Выполняется', completed: 'Выполнено', failed: 'Ошибка', cancelled: 'Остановлено', idle: '' };
const TOOLS: Array<[string, string]> = [['diagnostics.collect', 'Диагностика проекта'], ['devserver.detect', 'Поиск сервера разработки']];

const blank = (projectId: string): Form => ({ id: null, name: '', kind: 'agent', text: '', tool: 'diagnostics.collect', projectId, schedule: DEFAULT_SCHEDULE });

const fromTemplate = (template: Template, projectId: string): Form => ({
  id: null, name: template.title, kind: template.kind, text: template.text, tool: template.tool ?? 'diagnostics.collect', projectId, schedule: template.schedule,
});

const fromItem = (item: Automation): Form => ({
  id: item.id, name: item.name, kind: (item.kind || 'tool') as Kind, projectId: item.project_id ? String(item.project_id) : '',
  text: item.action.prompt ?? item.action.command ?? '', tool: item.action.tool ?? 'diagnostics.collect', schedule: parseSchedule(item.schedule),
});

/** Тело автоматизации из формы; сервер проверит всё ещё раз. */
export function actionFrom(form: Form): Record<string, unknown> {
  const project = form.projectId ? Number(form.projectId) : undefined;
  if (form.kind === 'agent') return { kind: 'agent', prompt: form.text.trim(), project_id: project };
  if (form.kind === 'command') return { kind: 'command', command: form.text.trim(), project_id: project };
  return { kind: 'tool', tool: form.tool, arguments: {}, ...(project ? { project_id: project } : {}) };
}

function ScheduleFields({ value, onChange }: { value: ScheduleForm; onChange: (value: ScheduleForm) => void }) {
  const set = (patchValue: Partial<ScheduleForm>) => onChange({ ...value, ...patchValue });
  return (
    <div className="form-row">
      <label>Когда запускать
        <select className="page-select" value={value.when} onChange={(event) => set({ when: event.target.value as When })}>
          {WHEN_LABELS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select>
      </label>
      {value.when === 'weekly' && (
        <label>День
          <select className="page-select" value={value.day} onChange={(event) => set({ day: event.target.value })}>
            {DAY_LABELS.map((label, index) => <option key={label} value={index + 1}>{label}</option>)}
          </select>
        </label>
      )}
      {['daily', 'weekdays', 'weekly'].includes(value.when) && (
        <label>Время
          <input className="page-input" type="time" value={value.time} onChange={(event) => set({ time: event.target.value })} />
        </label>
      )}
      {value.when === 'interval' && (
        <label>Минут между запусками
          <input className="page-input" type="number" min={15} max={1440} value={value.minutes} onChange={(event) => set({ minutes: event.target.value })} />
        </label>
      )}
    </div>
  );
}

function EditDialog({ form, onClose, onSaved }: { form: Form | null; onClose: () => void; onSaved: () => void }) {
  const projects = useData((s) => s.projects);
  const [value, setValue] = useState<Form | null>(form);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { setValue(form); setError(''); }, [form]);
  const open = form !== null;
  const current = value ?? form;
  const needsProject = current ? current.kind !== 'tool' : false;
  const save = async () => {
    if (!current) return;
    setBusy(true);
    setError('');
    try {
      const body = { name: current.name, schedule: scheduleString(current.schedule), action: actionFrom(current) };
      if (current.id == null) await post('/api/automations', body);
      else await patch(`/api/automations/${current.id}`, body);
      onSaved();
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : (failure as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onClose={onClose} className="form-dialog" label={current?.id == null ? 'Новая автоматизация' : 'Изменить автоматизацию'}>
      {current && (
        <form className="form-body" onSubmit={(event) => { event.preventDefault(); void save(); }}>
          <h2>{current.id == null ? 'Новая автоматизация' : 'Изменить автоматизацию'}</h2>
          <label>Название
            <input className="page-input" value={current.name} maxLength={80} required autoFocus placeholder="Например, утренний обзор" onChange={(event) => setValue({ ...current, name: event.target.value })} />
          </label>
          <div className="segmented" role="group" aria-label="Что делать">
            {(['agent', 'command', 'tool'] as Kind[]).map((kind) => (
              <button key={kind} type="button" aria-pressed={current.kind === kind} onClick={() => setValue({ ...current, kind })}>{KIND_TEXT[kind]}</button>
            ))}
          </div>
          {current.kind === 'agent' && (
            <label>Задание агенту
              <textarea className="page-input" rows={5} required value={current.text} placeholder="Опишите, что агент должен сделать. Задание выполняется в новом чате, без прежних разговоров, поэтому укажите всё важное." onChange={(event) => setValue({ ...current, text: event.target.value })} />
            </label>
          )}
          {current.kind === 'command' && (
            <label>Команда
              <input className="page-input mono" required value={current.text} placeholder="pytest -q" onChange={(event) => setValue({ ...current, text: event.target.value })} />
              <small>Выполняется в папке проекта, как в терминале.</small>
            </label>
          )}
          {current.kind === 'tool' && (
            <label>Проверка
              <select className="page-select" value={current.tool} onChange={(event) => setValue({ ...current, tool: event.target.value })}>
                {TOOLS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
              </select>
            </label>
          )}
          <label>Проект{needsProject ? '' : ' (необязательно)'}
            <select className="page-select" required={needsProject} value={current.projectId} onChange={(event) => setValue({ ...current, projectId: event.target.value })}>
              <option value="">{needsProject ? 'Выберите проект' : 'Без проекта'}</option>
              {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
            </select>
          </label>
          <ScheduleFields value={current.schedule} onChange={(schedule) => setValue({ ...current, schedule })} />
          {current.schedule.when !== 'manual' && <Hint>Автоматизации по расписанию работают, пока приложение открыто. Если время пропущено, запуск произойдёт при следующем открытии.</Hint>}
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="form-actions">
            <button type="button" className="secondary" onClick={onClose}>Отмена</button>
            <button type="submit" className="primary" disabled={busy}>{busy ? 'Сохраняю…' : 'Сохранить'}</button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

function AssistDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const projects = useData((s) => s.projects);
  const locked = useDeveloperAccess().locked;
  const [projectId, setProjectId] = useState('');
  const [request, setRequest] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { if (open) { setError(''); setProjectId((current) => current || String(projects[0]?.id ?? '')); } }, [open, projects]);
  const send = async () => {
    setBusy(true);
    setError('');
    try {
      const result = await post<{ chat: import('../../lib/types').Chat }>('/api/automations/assist', { project_id: Number(projectId), request: request.trim() });
      await loadLocal(false);
      setRequest('');
      onClose();
      openChat(result.chat);
      notify('Агент подготовит автоматизацию: подтвердите её создание в чате');
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onClose={onClose} className="form-dialog" label="Создать автоматизацию с помощью ИИ">
      <form className="form-body" onSubmit={(event) => { event.preventDefault(); void send(); }}>
        <h2><Sparkles aria-hidden="true" /> Создать с помощью ИИ</h2>
        <p className="form-lead">Опишите словами, что и когда делать. Агент откроет чат, при необходимости изучит проект и предложит готовую автоматизацию: вам останется подтвердить.</p>
        {!projects.length ? <p className="form-error">Сначала откройте проект: автоматизация привязывается к его папке.</p> : (
          <>
            <label>Проект
              <select className="page-select" value={projectId} onChange={(event) => setProjectId(event.target.value)}>
                {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
              </select>
            </label>
            <label>Что нужно автоматизировать
              <textarea className="page-input" rows={4} required minLength={3} autoFocus value={request} placeholder={AI_EXAMPLES[0]} onChange={(event) => setRequest(event.target.value)} />
            </label>
            <div className="chips" aria-label="Примеры">
              {AI_EXAMPLES.map((example) => <button key={example} type="button" onClick={() => setRequest(example)}>{example}</button>)}
            </div>
          </>
        )}
        {locked && <p className="form-error">Создание с ИИ работает в Докки Code: он включается с подпиской Plus. <button type="button" className="link" onClick={() => { onClose(); openBilling(); }}>Выбрать план</button></p>}
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="form-actions">
          <button type="button" className="secondary" onClick={onClose}>Отмена</button>
          <button type="submit" className="primary" disabled={busy || locked || !projects.length || request.trim().length < 3}>{busy ? 'Открываю чат…' : 'Создать'}</button>
        </div>
      </form>
    </Dialog>
  );
}

function Card({ item, projectName, onEdit, onChange }: { item: Automation; projectName: string; onEdit: () => void; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const chat = item.last_chat_id ? data.get().localChats.find((entry) => entry.id === item.last_chat_id) : undefined;
  const run = guarded(async () => {
    setBusy(true);
    try {
      const result = await post<{ ok: boolean; error?: string; result?: { reason?: string; error?: string } }>(`/api/automations/${item.id}/run`);
      notify(result.ok ? (item.kind === 'agent' ? 'Агент запущен: ход работы в чате' : 'Автоматизация выполнена') : result.error || result.result?.reason || result.result?.error || 'Автоматизация не выполнена', !result.ok);
      await loadLocal(false);
      onChange();
    } finally {
      setBusy(false);
    }
  });
  const toggle = guarded(async () => { await patch(`/api/automations/${item.id}`, { enabled: !item.enabled }); onChange(); });
  const remove = guarded(async () => {
    if (!(await confirmDialog('Удалить автоматизацию?', item.name, 'Удалить'))) return;
    await del(`/api/automations/${item.id}`);
    onChange();
  });
  const body = item.action.prompt ?? item.action.command ?? item.check_title ?? '';
  const status = STATUS_TEXT[item.status] ?? '';
  return (
    <article className="auto-card" data-enabled={item.enabled || undefined} data-status={item.status}>
      <div className="auto-head">
        <span className="auto-kind">{item.kind === 'agent' ? <Sparkles aria-hidden="true" /> : <Terminal aria-hidden="true" />}{KIND_TEXT[item.kind] ?? 'Проверка'}</span>
        <button type="button" role="switch" className="pref-switch" aria-checked={item.enabled} aria-label={item.enabled ? 'Выключить автоматизацию' : 'Включить автоматизацию'} title={item.enabled ? 'Включена' : 'Выключена'} onClick={toggle} />
      </div>
      <h3>{item.name}</h3>
      {body && <p className="auto-body" title={body}>{body}</p>}
      <p className="auto-meta">
        <span><Clock aria-hidden="true" />{item.schedule_text}</span>
        {projectName && <span>{projectName}</span>}
        {item.enabled && item.next_run_at && <span>Следующий запуск: {new Date(item.next_run_at).toLocaleString('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })}</span>}
      </p>
      {(item.last_run_at || status) && (
        <p className="auto-last" data-status={item.status}>
          {status ? `${status}. ` : ''}{item.last_result}{item.last_run_at ? ` · ${relativeTime(item.last_run_at)}${relativeTime(item.last_run_at) === 'сейчас' ? '' : ' назад'}` : ''}
        </p>
      )}
      <div className="auto-actions">
        <button type="button" className="secondary" disabled={busy || item.status === 'running'} onClick={run}><Play aria-hidden="true" /><span>{busy ? 'Запускаю…' : 'Запустить'}</span></button>
        {chat && <button type="button" className="secondary" onClick={() => openChat(chat)}><MessageSquare aria-hidden="true" /><span>Открыть чат</span></button>}
        <button type="button" className="icon-button" title="Изменить" aria-label="Изменить" onClick={onEdit}><Pencil aria-hidden="true" /></button>
        <button type="button" className="icon-button" title="Удалить" aria-label="Удалить" onClick={remove}><Trash2 aria-hidden="true" /></button>
      </div>
    </article>
  );
}

/** «Автоматизации»: задание по расписанию или по кнопке; создаются вручную, из заготовок или с помощью ИИ. */
export function AutomationsPage() {
  const [load, retry] = usePage((signal) => api<Automation[]>('/api/automations', { signal }), 'automations', 8000);
  const projects = useData((s) => s.projects);
  const project = useData((s) => s.projects[0]);
  const [editing, setEditing] = useState<Form | null>(null);
  const [assist, setAssist] = useState(false);
  const projectName = (id: number | null) => projects.find((item) => item.id === id)?.name ?? '';
  const defaultProject = project ? String(project.id) : '';
  return (
    <Frame title="Автоматизации" load={load} retry={retry}>
      {(items) => (
        <PageRoot
          title="Автоматизации"
          subtitle={items.length ? `${items.filter((item) => item.enabled).length} из ${items.length} включено` : undefined}
          commands={(
            <>
              <button type="button" className="primary" onClick={() => setAssist(true)}><Sparkles aria-hidden="true" /><span>Создать с помощью ИИ</span></button>
              <button type="button" className="secondary" onClick={() => setEditing(blank(defaultProject))}><Plus aria-hidden="true" /><span>Вручную</span></button>
            </>
          )}
        >
          <Hint>Автоматизация - это задание, которое выполняется само: по расписанию или по кнопке «Запустить». Агент может проверять проект, запускать тесты и писать отчёты. Расписание работает, пока приложение открыто.</Hint>
          {!items.length && (
            <Empty icon={<CalendarClock size={24} aria-hidden="true" />} title="Автоматизаций пока нет" detail="Начните с готовой заготовки или попросите ИИ создать автоматизацию по вашему описанию." />
          )}
          <div className="auto-list">
            {items.map((item) => <Card key={item.id} item={item} projectName={projectName(item.project_id)} onEdit={() => setEditing(fromItem(item))} onChange={retry} />)}
          </div>
          <h2 className="page-section-title">Заготовки</h2>
          <div className="template-grid">
            {TEMPLATES.map((template) => (
              <button key={template.id} type="button" className="template-card" onClick={() => setEditing(fromTemplate(template, defaultProject))}>
                <b>{template.title}</b>
                <span>{template.detail}</span>
                <small>{KIND_TEXT[template.kind]}</small>
              </button>
            ))}
          </div>
          <EditDialog form={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); retry(); }} />
          <AssistDialog open={assist} onClose={() => setAssist(false)} />
        </PageRoot>
      )}
    </Frame>
  );
}

import {
  Brain, Check, ChevronDown, ChevronLeft, ChevronRight, FileCode2, FolderOpen, Layers, ListTodo, Paperclip, Pencil, RefreshCw, Search, Settings, ShieldCheck, Sparkles, TextCursorInput, X, Zap,
} from 'lucide-react';
import { useEffect, useState, type ReactNode, type RefObject } from 'react';
import { openFolder } from '../../state/actions';
import { changeChatModel } from '../../state/chat';
import { useChat } from '../../state/chatCore';
import { respondRequest } from '../../state/chatAgent';
import type { ActionSummary, AgentRequest } from '../../state/chatTypes';
import { cancelUpload, composer, removeAttachment, restorePastedText, runUpload, useComposer } from '../../state/composer';
import { useData } from '../../state/data';
import { cloudModels, selectedCloudModel } from '../../state/models';
import { guarded } from '../../state/overlay';
import { editQueueItem, removeQueueItem, resumeQueue, runQueueItemNow } from '../../state/queue';
import { attachSkill, detachSkill, groupSkills, loadSkills, useSkills } from '../../state/skills';
import { useUi } from '../../state/ui';
import { useView } from '../../state/view';
import { Popover } from '../ui/Popover';
import { summarizeApproval } from '../../lib/approval';
import { ApprovalPreview } from './ApprovalPreview';

/** Подменю «Навыки»: выбранный навык подключается к запросу и уходит агенту как указание пользователя. */
function SkillPicker({ onBack, onDone }: { onBack: () => void; onDone: () => void }) {
  const items = useSkills((s) => s.items);
  const loaded = useSkills((s) => s.loaded);
  const error = useSkills((s) => s.error);
  const chosen = useComposer((s) => s.skills);
  const [query, setQuery] = useState('');
  useEffect(() => { void loadSkills(); }, []);
  const groups = groupSkills(items, query);
  return (
    <div className="skill-picker">
      <button type="button" role="menuitem" className="menu-item mode-back" onClick={onBack}><ChevronLeft aria-hidden="true" /><span>Навыки</span></button>
      <label className="skill-search"><Search aria-hidden="true" /><input type="search" autoFocus placeholder="Найти навык" aria-label="Найти навык" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      <div className="skill-list" role="group" aria-label="Навыки">
        {!loaded && !error && <span className="skill-empty">Загружаю…</span>}
        {error && <span className="skill-empty">{error}</span>}
        {loaded && !groups.length && <span className="skill-empty">{query ? 'Ничего не найдено' : 'Навыков пока нет'}</span>}
        {groups.map(([category, skills]) => (
          <div key={category} className="skill-group">
            <span className="skill-category">{category}</span>
            {skills.map((skill) => {
              const on = chosen.some((item) => item.id === skill.id);
              return (
                <button
                  key={skill.id}
                  type="button"
                  role="menuitemcheckbox"
                  aria-checked={on}
                  className={`menu-item skill-option${on ? ' selected' : ''}`}
                  onClick={() => { if (on) detachSkill(skill.id); else attachSkill(skill); onDone(); }}
                >
                  <span><b>{skill.name}</b><small>{skill.description}</small></span>
                  <Check aria-hidden="true" />
                </button>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Подключённые навыки над полем ввода: каждый можно убрать до отправки. */
export function SkillChips() {
  const skills = useComposer((s) => s.skills);
  if (!skills.length) return null;
  return (
    <div className="skill-chips" aria-label="Подключённые навыки">
      {skills.map((skill) => (
        <span key={skill.id} className="skill-chip" title="Навык подключён к этому запросу">
          <Layers aria-hidden="true" />
          <span>{skill.name}</span>
          <button type="button" aria-label={`Убрать навык ${skill.name}`} onClick={() => detachSkill(skill.id)}><X aria-hidden="true" /></button>
        </span>
      ))}
    </div>
  );
}

/* вложения над полем ввода, как на сайте */
export function AttachmentStrip() {
  const attachments = useComposer((s) => s.attachments);
  const uploads = useComposer((s) => s.uploads);
  const [leaving, setLeaving] = useState<string | null>(null);
  const remove = (key: string, action: () => void) => {
    setLeaving(key);
    window.setTimeout(() => { action(); setLeaving(null); }, 150);
  };
  const has = attachments.length > 0 || uploads.length > 0;
  return (
    <div id="attachments" className={`attachment-strip${has ? ' has-files' : ''}`} aria-label="Прикреплённые файлы">
      {attachments.map((item) => {
        const name = item.original_name || item.name || 'Вложение';
        const image = item.preview || String(item.media_type || item.mime_type || '').startsWith('image/');
        return (
          <div key={String(item.id)} className={`attachment-card${item.pastedText != null ? ' is-pasted-text' : ''}${leaving === String(item.id) ? ' is-leaving' : ''}`} title={name}>
            {image && item.preview ? <img src={item.preview} alt={name} /> : (
              <>
                <span className="file-glyph"><FileCode2 aria-hidden="true" /></span>
                <span className="file-name">{name}</span>
              </>
            )}
            {item.pastedText != null && <button type="button" className="attachment-as-text" aria-label={`Вставить как текст: ${name}`} title="Вставить как текст" onClick={() => restorePastedText({ attachmentId: item.id })}><TextCursorInput aria-hidden="true" /><span>Вставить как текст</span></button>}
            <button type="button" aria-label={`Удалить ${name}`} onClick={() => remove(String(item.id), () => removeAttachment(item.id))}><X aria-hidden="true" /></button>
          </div>
        );
      })}
      {uploads.map((job) => (
        <div key={job.key} className={`attachment-card is-upload${job.pastedText != null ? ' is-pasted-text' : ''}${job.error ? ' upload-error' : ''}${leaving === job.key ? ' is-leaving' : ''}`} title={job.error || 'Загрузка вложения'} role="status">
          {job.preview ? <img src={job.preview} alt={job.file.name} /> : (
            <>
              <span className="file-glyph"><FileCode2 aria-hidden="true" /></span>
              <span className="file-name">{job.file.name}</span>
            </>
          )}
          <small className="attachment-state">{job.error ? 'Ошибка загрузки' : 'Загрузка…'}</small>
          {job.pastedText != null && <button type="button" className="attachment-as-text" aria-label={`Вставить как текст: ${job.file.name}`} title="Вставить как текст" onClick={() => restorePastedText({ uploadKey: job.key })}><TextCursorInput aria-hidden="true" /><span>Вставить как текст</span></button>}
          {job.error && <button type="button" className="attachment-retry" aria-label="Повторить загрузку" onClick={() => void runUpload(job.key)}><RefreshCw aria-hidden="true" /></button>}
          <button type="button" aria-label="Отменить загрузку" onClick={() => remove(job.key, () => cancelUpload(job.key))}><X aria-hidden="true" /></button>
        </div>
      ))}
    </div>
  );
}

/* очередь запросов */
const QUEUE_LIMIT = 3;

/** Очередь как в проектах Докки: заголовок со счётчиком, карточки по порядку отправки, «Показать все». Ушедший в работу запрос из списка пропадает. */
export function QueuePanel() {
  const queue = useChat((s) => s.queue);
  const [expanded, setExpanded] = useState(false);
  const items = (queue?.items ?? []).filter((item) => item.status !== 'running');
  const working = Boolean(queue?.items.some((item) => item.status === 'running'));
  if (!queue || !items.length) return <section id="local-queue" aria-label="Очередь запросов" hidden />;
  const waiting = items.filter((item) => item.status === 'queued').length;
  const visible = expanded ? items : items.slice(0, QUEUE_LIMIT);
  const title = queue.paused ? 'Очередь приостановлена' : working ? 'Агент работает, дальше по очереди' : 'Очередь';
  return (
    <section id="local-queue" aria-label="Очередь запросов" data-state={queue.paused ? 'paused' : working ? 'working' : 'waiting'}>
      <div className="queue-header">
        <strong>{title}{waiting ? ` · ${waiting} в очереди` : ''}</strong>
        {queue.paused && waiting > 0 && <button type="button" onClick={guarded(resumeQueue)}>Продолжить</button>}
        {items.length > QUEUE_LIMIT && (
          <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? 'Свернуть' : 'Показать все'}</button>
        )}
      </div>
      <div className="queue-rows">
        {visible.map((item, index) => (
          <article key={item.id} className="queue-row" data-status={item.status}>
            <span className="queue-index" aria-hidden="true">{index + 1}</span>
            <span className="queue-copy">
              <strong>{item.status === 'failed' ? 'Не выполнен - проверьте результат' : index === 0 && working ? 'Следующий' : `№ ${index + 1} в очереди`}</strong>
              <span title={item.content}>{item.content}</span>
            </span>
            {item.status === 'queued' && (
              <button type="button" className="queue-action" title="Выполнить сейчас: остановить текущую задачу" aria-label="Выполнить сейчас" onClick={guarded(() => runQueueItemNow(item))}><Zap aria-hidden="true" /></button>
            )}
            {item.status === 'queued' && (
              <button type="button" className="queue-action" title="Изменить запрос" aria-label="Изменить запрос" onClick={guarded(() => editQueueItem(item))}><Pencil aria-hidden="true" /></button>
            )}
            <button type="button" className="queue-action" title="Убрать из очереди" aria-label="Убрать из очереди" onClick={guarded(() => removeQueueItem(item))}><X aria-hidden="true" /></button>
          </article>
        ))}
      </div>
    </section>
  );
}

/* подтверждения и вопросы агента */
function ActionCard({ action }: { action: ActionSummary }) {
  return (
    <div className="approval-action">
      <strong className="approval-title">{action.title}</strong>
      {action.target && <span className="approval-target">{action.target}</span>}
      {action.detail && (action.kind === 'command'
        ? <pre className="approval-command">{action.detail}</pre>
        : <p className="approval-detail">{action.detail}</p>)}
    </div>
  );
}

function Question({ request }: { request: AgentRequest }) {
  const [answer, setAnswer] = useState('');
  const [expanded, setExpanded] = useState(false);
  const payload = request.payload;
  const action = payload.action;
  const summary = summarizeApproval(payload.name, payload.arguments);
  const command = payload.name === 'shell.run' || payload.name === 'shell__run';
  return (
    <section className="approval-panel">
      <b>{request.kind === 'approval' && <ShieldCheck aria-hidden="true" />}{request.kind === 'approval' ? (command ? 'Докки хочет выполнить команду' : `Докки хочет ${summary.title.toLocaleLowerCase('ru-RU')}`) : 'Уточнение от агента'}</b>
      {payload.question && <p className="approval-question">{payload.question}</p>}
      {request.kind === 'approval' && <>
        <p className="approval-scope">{action?.target || summary.scope[0]}{!action?.target && summary.scope.length > 1 ? ` · ещё ${summary.scope.length - 1}` : ''}</p>
        {action?.dangerous && <p className="approval-note warn">Команда может удалить данные или изменить систему.</p>}
        {Boolean(action?.outside_project?.length) && <p className="approval-note">Файл вне папки проекта; изменение сохранится для отката.</p>}
        {payload.review?.reason && <p className="approval-note approval-review">{payload.review.available ? 'Докки: ' : ''}{payload.review.reason}</p>}
        <details className="approval-details" open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}>
          <summary>{expanded ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}<span>Что будет сделано</span></summary>
          <div className="approval-details-scroll" tabIndex={0} aria-label="Список действий и файлов">
            {action ? <ActionCard action={action} /> : <><ul>{summary.scope.map((item) => <li key={item}>{item}</li>)}</ul>{summary.details.map((item) => <p key={item}>{item}</p>)}</>}
            {expanded && action?.kind !== 'command' && <ApprovalPreview name={payload.name} args={payload.arguments} id={String(request.id)} />}
          </div>
        </details>
      </>}
      {request.kind === 'approval' ? (
        <div className="actions">
          <button type="button" className="secondary" onClick={guarded(() => respondRequest(request, { approved: false }))}><X aria-hidden="true" /><span>Отклонить</span></button>
          <button type="button" className="primary" onClick={guarded(() => respondRequest(request, { approved: true }))}><Check aria-hidden="true" /><span>Разрешить</span></button>
        </div>
      ) : (
        <form onSubmit={(event) => { event.preventDefault(); guarded(() => respondRequest(request, { answer }))(); }}>
          <input name="answer" required placeholder="Ваш ответ" value={answer} onChange={(event) => setAnswer(event.target.value)} />
          <button type="submit" className="primary"><span>Ответить</span></button>
        </form>
      )}
    </section>
  );
}

export function RequestPanel() {
  const requests = useChat((s) => s.requests);
  return (
    <section id="request-panel" className="request-panel" aria-label="Запрос подтверждения" aria-live="polite" hidden={!requests.length}>
      {requests.map((request) => <Question key={String(request.id)} request={request} />)}
    </section>
  );
}

/* меню «+» */
const modeDescriptions: Record<string, [string, string, ReactNode]> = {
  'dokky-default': ['Авто', 'Докки выберет подходящий режим', <Sparkles key="a" aria-hidden="true" />],
  'dokky-fast': ['Быстрый', 'Быстрый ответ без раздумий', <Zap key="f" aria-hidden="true" />],
  'dokky-enhanced': ['Усиленный', 'Глубже, умнее и с источниками', <Brain key="e" aria-hidden="true" />],
};

export function AttachMenu({ trigger, open, onClose, fileInput }: { trigger: RefObject<HTMLElement | null>; open: boolean; onClose: () => void; fileInput: RefObject<HTMLInputElement | null> }) {
  const development = useUi((s) => s.scope === 'development');
  const scope = useUi((s) => s.scope);
  const running = useChat((s) => s.running);
  const planning = useComposer((s) => s.agentMode === 'plan');
  const chatModel = useView((s) => s.chat?.model);
  const account = useData((s) => s.account);
  const [page, setPage] = useState<'root' | 'modes' | 'skills'>('root');
  const attached = useComposer((s) => s.skills.length);
  const close = () => { onClose(); window.setTimeout(() => setPage('root'), 160); };
  const choose = (action: () => unknown) => () => { close(); guarded(async () => { await action(); })(); };
  const models = account ? cloudModels(scope) : [];
  const current = selectedCloudModel(scope, chatModel);
  const currentLabel = modeDescriptions[current]?.[0] ?? models.find((model) => model.id === current)?.name.replaceAll('Dokky', 'Докки') ?? 'Авто';
  const item = (label: string, icon: ReactNode, action: () => unknown, extra?: { hint?: string; role?: string; checked?: boolean; disabled?: boolean }) => (
    <button
      key={label}
      type="button"
      role={extra?.role ?? 'menuitem'}
      aria-checked={extra?.checked}
      className="menu-item"
      disabled={extra?.disabled}
      onClick={choose(action)}
    >
      {icon}
      <span>{label}</span>
      {extra?.hint && <span className="mode-label">{extra.hint}</span>}
    </button>
  );
  return (
    <Popover open={open} anchor={trigger} onClose={close} side="above" className="composer-actions" label="Добавить" minWidth={development && page === 'skills' ? 340 : 250}>
      {development && page === 'skills' ? (
        <SkillPicker onBack={() => setPage('root')} onDone={close} />
      ) : development ? (
        <>
          {item('Добавить файлы или фото', <Paperclip aria-hidden="true" />, () => fileInput.current?.click())}
          {item('Открыть папку', <FolderOpen aria-hidden="true" />, openFolder)}
          <button type="button" role="menuitem" className="menu-item" onClick={() => { void loadSkills(); setPage('skills'); }}>
            <Layers aria-hidden="true" />
            <span>Навыки</span>
            {attached > 0 && <span className="mode-label">{attached}</span>}
            <ChevronRight className="menu-chevron" aria-hidden="true" />
          </button>
          {item('Планирование', <ListTodo aria-hidden="true" />, () => composer.set({ agentMode: planning ? 'development' : 'plan' }), { role: 'menuitemcheckbox', checked: planning, disabled: running })}
        </>
      ) : page === 'root' ? (
        <>
          {item('Прикрепить файл', <Paperclip aria-hidden="true" />, () => fileInput.current?.click())}
          <button type="button" role="menuitem" className="menu-item" onClick={() => setPage('modes')}>
            <Settings aria-hidden="true" />
            <span>Режим</span>
            <span className="mode-label">{currentLabel}</span>
          </button>
        </>
      ) : (
        <>
          <button type="button" role="menuitem" className="menu-item mode-back" onClick={() => setPage('root')}><ChevronLeft aria-hidden="true" /><span>Режим Докки</span></button>
          {models.map((model) => {
            const [name, text, icon] = modeDescriptions[model.id] ?? [model.name.replaceAll('Dokky', 'Докки'), '', <Sparkles key="s" aria-hidden="true" />];
            return (
              <button
                key={model.id}
                type="button"
                role="menuitemradio"
                aria-checked={model.id === current}
                disabled={!model.available}
                className={`menu-item mode-option${model.id === current ? ' selected' : ''}`}
                onClick={() => { close(); if (model.id !== current) guarded(() => changeChatModel(model.id))(); }}
              >
                {icon}
                <span><b>{name}</b><small>{text}</small></span>
                <Check aria-hidden="true" />
              </button>
            );
          })}
        </>
      )}
    </Popover>
  );
}

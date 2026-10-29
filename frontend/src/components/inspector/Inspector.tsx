import {
  ChevronDown, ChevronRight, Eye, FileCode2, Folder, FolderGit2, Files, Gauge, GitCompare, Globe, History, ListChecks, Code2, Network, ShieldCheck, Save, Undo2, X, type LucideIcon,
} from 'lucide-react';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { changeDiffModel, diffStats, pruneDiffCache, type DiffFile } from '../../lib/diff';
import { compactPath, numberText } from '../../lib/format';
import { permissionLabel } from '../../lib/permissions';
import { restoreTurn, rollbackChange, selectChange } from '../../state/remote';
import { useChat } from '../../state/chatCore';
import { data, useData } from '../../state/data';
import { guarded } from '../../state/overlay';
import { setPanel } from '../../state/panels';
import { useUi } from '../../state/ui';
import { useView } from '../../state/view';
import {
  collapseFocus, editFile, loadFiles, openFile, previewFile, saveFile, selectInspector, showAllFiles, toggleOverview, useWorkspace, type Focus,
} from '../../state/workspace';
import { DiffView } from '../chat/DiffView';
import { Markdown } from '../chat/Markdown';
import { PlanList } from '../chat/Plan';
import { currentReasoningMode } from '../../state/codingSession';
import { useBrowser } from '../../state/browser';
import { BrowserView } from './BrowserCard';
import { ContextBreakdown } from './ContextCard';
import { ProjectMapView } from './ProjectMap';
import { useProjectMap } from '../../state/projectMap';
import { RemoteAccess } from './RemoteAccess';
import { QualityView } from './QualityCard';
import { latestByGate, useQuality } from '../../state/quality';

function Card({ id, name, icon: Icon, title, meta, focus, open, children }: {
  id: string; name: string; icon: LucideIcon; title: string; meta?: ReactNode; focus: boolean; open: boolean; children: ReactNode;
}) {
  const current = useWorkspace((s) => s.focus);
  const onHead = () => {
    if (focus) {
      if (open) collapseFocus();
      else void guarded(() => selectInspector(name as Focus))();
    } else toggleOverview(name as 'environment' | 'context');
  };
  return (
    <section className={`inspector-card ${focus ? 'focus-card' : 'overview-card'}${open ? ' open' : ''}`} id={id} data-card={name} data-glass={`card-${name}`} inert={!focus && Boolean(current)}>
      <button className="card-head" type="button" aria-expanded={open} aria-controls={`${id}-body`} onClick={onHead}>
        <Icon aria-hidden="true" />
        <span>{title}</span>
        {meta !== undefined && <small className="card-meta">{meta}</small>}
        <ChevronDown className="card-chevron" aria-hidden="true" />
      </button>
      <div className="card-body" id={`${id}-body`} inert={!open}>
        <div className="card-inner" data-overlay-scroll={focus ? "" : undefined}>{children}</div>
      </div>
    </section>
  );
}

const finite = (...values: unknown[]): number | undefined => values.map(Number).find((value) => Number.isFinite(value) && value > 0);

/** Окно контекста: у каждого режима разработки своё, поэтому предел берётся по выбранному (в том числе ещё не применённому). */
function useContext() {
  const usageState = useChat((s) => s.usage ?? s.coding?.last_usage ?? null);
  const coding = useChat((s) => s.coding);
  const models = useData((s) => s.models);
  const settings = useData((s) => s.settings);
  const chatModel = useView((s) => s.chat?.model);
  return useMemo(() => {
    const usage = (usageState?.usage ?? {}) as Record<string, number>;
    const model = models.find((item) => item.tag === (chatModel || settings.selected_model)) ?? models[0];
    const exact = finite(usage.context_tokens, usage.prompt_tokens, usage.input_tokens);
    const used = exact ?? finite(coding?.estimated_context_tokens);
    const approx = exact === undefined && used !== undefined;
    const mode = coding?.pending_reasoning_mode || coding?.reasoning_mode || settings.coding_reasoning_mode;
    const limit = finite(model?.reasoning_modes?.find((item) => item.id === mode)?.compact_at_tokens, usage.context_limit, model?.context_tokens);
    if (!used) return { percent: null as number | null, text: 'Данных об окне контекста пока нет.', level: '' };
    const prefix = approx ? '≈ ' : '';
    if (!limit) return { percent: null, text: `Использовано ${prefix}${numberText(used)} токенов.`, level: '' };
    const percent = Math.min(100, Math.round((used / limit) * 100));
    return {
      percent,
      text: `Использовано ${prefix}${numberText(used)} из ${numberText(limit)} токенов. При заполнении Докки сожмёт историю сам.`,
      level: percent >= 90 ? 'high' : percent >= 75 ? 'warn' : '',
    };
  }, [usageState, coding, models, settings, chatModel]);
}

function Environment() {
  const project = useView((s) => s.project);
  const chatModel = useView((s) => s.chat?.model);
  const models = useData((s) => s.models);
  const settings = useData((s) => s.settings);
  const model = models.find((item) => item.tag === (chatModel || settings.selected_model));
  return (
    <dl className="environment-list">
      {project?.path && (<><dt>Папка</dt><dd className="path-value" title={project.path}>{compactPath(project.path)}</dd></>)}
      <dt>Проект</dt><dd>{project?.name || 'Не выбран'}</dd>
      <dt>Модель</dt><dd>{model?.name || chatModel || 'Не выбрана'}</dd>
      <dt>Подтверждения</dt><dd>{permissionLabel(settings.permission_profile)}</dd>
    </dl>
  );
}

function Changes() {
  const changes = useChat((s) => s.changes);
  const selection = useChat((s) => s.changeSelection);
  const entries = useMemo(() => {
    pruneDiffCache(new Set(changes.map((change) => change.id)));
    return changes.flatMap((change) => {
      const files: DiffFile[] = [...changeDiffModel(change)];
      if (!files.length) files.push({ path: change.description, hunks: [], added: 0, removed: 0 });
      return files.map((file) => ({ change, file, key: `${change.id}:${file.path}`, fileCount: files.length }));
    });
  }, [changes]);
  const totals = useMemo(() => diffStats(entries.map((entry) => entry.file)), [entries]);
  const [opened, setOpened] = useState<Set<string>>(() => new Set(selection ? [selection] : []));
  const toggle = (key: string) => setOpened((value) => {
    const next = new Set(value);
    if (!next.delete(key)) next.add(key);
    return next;
  });
  if (!entries.length) return <section id="turn"><div id="change-summary" className="change-summary">Изменений пока нет</div></section>;
  const current = entries.find((entry) => entry.key === selection) ?? (entries[0] as (typeof entries)[number]);
  const rolled = current.change.status === 'rolled_back';
  const turnId = current.change.turn_id ?? null;
  const turnSets = turnId == null ? 0 : changes.filter((change) => change.turn_id === turnId && change.status !== 'rolled_back').length;
  return (
    <section id="turn">
      <div id="change-summary" className="change-summary">
        <span>Изменения чата</span><span className="positive">+{totals.add}</span><span className="danger">−{totals.remove}</span>
      </div>
      <div id="change-files" className="change-list">
        {entries.map((entry) => {
          const open = opened.has(entry.key);
          const slash = entry.file.path.lastIndexOf('/');
          const name = slash >= 0 ? entry.file.path.slice(slash + 1) : entry.file.path;
          const folder = slash >= 0 ? entry.file.path.slice(0, slash) : '';
          return (
            <div key={entry.key} className={`change-item${open ? ' open' : ''}${entry.key === current.key ? ' active' : ''}`}>
              <button
                type="button"
                className="change-file"
                title={entry.file.path}
                aria-expanded={open}
                onClick={() => { selectChange(entry.key); toggle(entry.key); }}
              >
                <ChevronRight className="change-chevron" size={13} aria-hidden="true" />
                <FileCode2 size={15} aria-hidden="true" />
                <span className="change-name">{name}</span>
                {folder && <span className="change-folder">{folder}</span>}
                <small className="positive">+{entry.file.added}</small>
                <small className="danger">−{entry.file.removed}</small>
              </button>
              {open && <DiffView files={[entry.file]} id={entry.key} bare />}
            </div>
          );
        })}
      </div>
      <div className="actions">
        <button
          id="rollback"
          type="button"
          className="secondary"
          disabled={rolled}
          title={current.fileCount > 1 ? `Откатить весь набор (файлов: ${current.fileCount})` : 'Откатить изменение файла'}
          onClick={guarded(() => rollbackChange(current.change.id, current.fileCount))}
        >
          <Undo2 aria-hidden="true" /><span>Откатить изменения</span>
        </button>
        {turnId != null && turnSets > 1 && (
          <button
            type="button"
            className="secondary"
            title={`Вернуть все файлы хода к состоянию до него (наборов изменений: ${turnSets})`}
            onClick={guarded(() => restoreTurn(turnId))}
          >
            <History aria-hidden="true" /><span>Откатить весь ход</span>
          </button>
        )}
      </div>
    </section>
  );
}

function FilesCard() {
  const project = useView((s) => s.project);
  const tree = useWorkspace((s) => s.tree);
  const editor = useWorkspace((s) => s.editor);
  const folder = useWorkspace((s) => s.folder);
  const files = folder ? tree.files.filter((path) => path.startsWith(`${folder}/`)) : tree.files;
  const body = !project ? (
    <div className="empty-list"><FolderGit2 size={24} aria-hidden="true" /><h3>Проект не выбран</h3></div>
  ) : tree.state === 'loading' ? (
    <div className="skeleton file-skeleton" />
  ) : tree.state === 'failed' ? (
    <div className="empty-list">
      <FolderGit2 size={24} aria-hidden="true" /><h3>Не удалось загрузить файлы</h3><p>{tree.error}</p>
      <button type="button" className="secondary" onClick={guarded(loadFiles)}><span>Повторить</span></button>
    </div>
  ) : tree.state === 'ready' && !files.length ? (
    <div className="empty-list"><FolderGit2 size={24} aria-hidden="true" /><h3>Папка пуста</h3></div>
  ) : (
    files.map((path) => (
      <button key={path} type="button" className="file-row" title={path} onClick={guarded(() => openFile(path))}>
        <FileCode2 size={15} aria-hidden="true" /><span>{folder ? path.slice(folder.length + 1) : path}</span>
      </button>
    ))
  );
  return (
    <>
      {folder && project && (
        <div className="file-folder">
          <Folder size={14} aria-hidden="true" /><span title={folder}>{folder}</span>
          <button type="button" className="icon-button" title="Показать все файлы" aria-label="Показать все файлы" onClick={showAllFiles}><X aria-hidden="true" /></button>
        </div>
      )}
      <nav id="file-tree" className="file-tree" aria-label={folder ? `Файлы папки ${folder}` : 'Файлы проекта'}>{body}</nav>
      {editor && (
        <div id="editor-view" className="editor-view" aria-label="Содержимое файла">
          <div className="editor-header">
            <span>{editor.path}</span>
            {/\.(md|markdown|mdown|mkd)$/i.test(editor.path) && (
              <div className="editor-modes" role="group" aria-label="Вид Markdown">
                <button type="button" className="icon-button" title="Код" aria-label="Код" aria-pressed={!editor.preview} onClick={() => previewFile(false)}><Code2 aria-hidden="true" /></button>
                <button type="button" className="icon-button" title="Предпросмотр" aria-label="Предпросмотр" aria-pressed={editor.preview} onClick={() => previewFile(true)}><Eye aria-hidden="true" /></button>
              </div>
            )}
            <button type="button" className="icon-button" title="Сохранить файл" aria-label="Сохранить файл" disabled={editor.readOnly || editor.saving} onClick={guarded(saveFile)}><Save aria-hidden="true" /></button>
          </div>
          <textarea className="file-editor" spellCheck={false} aria-label="Содержимое файла" value={editor.content} readOnly={editor.readOnly} hidden={editor.preview} onChange={(event) => editFile(event.target.value)} />
          {editor.preview && <div className="file-preview message-body" tabIndex={0} aria-label="Предпросмотр Markdown"><Markdown text={editor.content} className="" /></div>}
        </div>
      )}
    </>
  );
}

export function Inspector() {
  const focus = useWorkspace((s) => s.focus);
  const overview = useWorkspace((s) => s.overview);
  const tree = useWorkspace((s) => s.tree);
  const rightOpen = useUi((s) => s.rightOpen);
  const project = useView((s) => s.project);
  const plan = useChat((s) => s.plan);
  const changes = useChat((s) => s.changes);
  const context = useContext();
  const browserCount = useBrowser((state) => state.sessions.length);
  const mapFiles = useProjectMap((state) => state.tree?.totals.files);
  const gateRuns = useQuality((state) => state.runs);
  const failedGates = useMemo(() => latestByGate(gateRuns).filter((run) => run.status === 'failed' || run.status === 'error').length, [gateRuns]);
  const steps = plan?.tracking_steps ?? plan?.steps ?? [];
  const done = steps.filter((step) => step.status === 'completed').length;
  const stats = useMemo(() => diffStats(changes.flatMap((change) => changeDiffModel(change))), [changes]);
  const count = useMemo(() => changes.flatMap((change) => changeDiffModel(change)).length || changes.length, [changes]);

  // у другого проекта свой набор файлов
  useEffect(() => {
    if (focus === 'files' && rightOpen && tree.state !== 'loading' && tree.projectId !== (project?.id ?? null)) void loadFiles();
  }, [focus, rightOpen, project?.id, tree.projectId, tree.state]);
  void currentReasoningMode;
  void data;

  return (
    <>
      <div className="inspector-heading">
        <h2>Рабочая область</h2>
        <button id="close-inspector" className="icon-button" type="button" title="Закрыть инспектор" aria-label="Закрыть инспектор" onClick={() => setPanel('right', false)}><X aria-hidden="true" /></button>
      </div>
      <div id="inspector-content" className="inspector-cards" data-focus={focus}>
        <Card id="card-environment" name="environment" icon={FolderGit2} title="Среда" focus={false} open={overview.environment}>
          <Environment />
          <RemoteAccess />
        </Card>
        <Card id="card-context" name="context" icon={Gauge} title="Контекст" meta={context.percent == null ? '—' : `${context.percent}%`} focus={false} open={overview.context}>
          <div className="context-meter" role="progressbar" aria-label="Заполнено окно контекста" aria-valuemin={0} aria-valuemax={100} aria-valuenow={context.percent ?? 0} data-level={context.level || undefined}>
            <i style={{ width: `${context.percent ?? 0}%` }} />
          </div>
          <p id="context-detail" className="muted">{context.text}</p>
          <ContextBreakdown active={overview.context} />
        </Card>
        <Card
          id="inspector-diff"
          name="diff"
          icon={GitCompare}
          title="Изменения"
          meta={changes.length ? (<>{count} · <span className="positive">+{stats.add}</span> <span className="danger">−{stats.remove}</span></>) : ''}
          focus
          open={focus === 'diff'}
        >
          <Changes />
        </Card>
        <Card id="inspector-files" name="files" icon={Files} title="Файлы" focus open={focus === 'files'}>
          <FilesCard />
        </Card>
        <Card id="inspector-map" name="map" icon={Network} title="Карта проекта" meta={mapFiles ? String(mapFiles) : ''} focus open={focus === 'map'}>
          <ProjectMapView active={focus === 'map' && rightOpen} />
        </Card>
        <Card id="inspector-quality" name="quality" icon={ShieldCheck} title="Качество" meta={failedGates ? <span className="danger">{failedGates}</span> : ''} focus open={focus === 'quality'}>
          <QualityView active={focus === 'quality' && rightOpen} />
        </Card>
        <Card id="inspector-browser" name="browser" icon={Globe} title="Браузер" meta={browserCount ? String(browserCount) : ''} focus open={focus === 'browser'}>
          <BrowserView active={focus === 'browser' && rightOpen} />
        </Card>
        <Card id="card-plan" name="plan" icon={ListChecks} title="План" meta={steps.length ? `${done}/${steps.length}` : ''} focus open={focus === 'plan'}>
          <section id="task-plan" className="task-plan">
            {plan?.document ? <div className="plan-document-body message-body"><Markdown text={plan.document} className="" highlight={false} /></div> : null}
            {steps.length ? <PlanList steps={steps} status={plan?.tracking_status} /> : null}
          </section>
        </Card>
      </div>
    </>
  );
}

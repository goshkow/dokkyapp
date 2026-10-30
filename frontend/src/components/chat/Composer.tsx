import { ArrowDown, ChevronRight, Folder, ListTodo, X } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { useNativeTiles } from '../../lib/glass';
import { PERMISSION_OPTIONS } from '../../lib/permissions';
import { useFeedUi } from '../../state/feedUi';
import { changeChatModel, chooseWorkspace, savePermission } from '../../state/chat';
import { releaseFeed, useChat } from '../../state/chatCore';
import { reducedMotion } from '../../lib/motion';
import { changeReasoningMode, currentReasoningMode } from '../../state/codingSession';
import { composer, upload, useComposer } from '../../state/composer';
import { useData } from '../../state/data';
import { guarded } from '../../state/overlay';
import { fieldHost, useFieldHost } from '../../state/field';
import { useUi } from '../../state/ui';
import { ComposerField } from './ComposerField';
import { useView } from '../../state/view';
import { Select } from '../ui/Select';
import { cloudModels, selectedCloudModel } from '../../state/models';
import { AttachMenu, AttachmentStrip, QueuePanel, RequestPanel, SkillChips } from './ComposerParts';
import { useDeveloperAccess } from '../../state/access';
import { LockedComposer } from './DevGate';
import { UsageNotice } from './UsageNotice';

export function Composer() {
  const scope = useUi((s) => s.scope);
  const development = scope === 'development';
  const search = scope === 'search';
  const planning = useComposer((s) => s.agentMode === 'plan');
  const recording = useComposer((s) => s.recording);
  const transcribing = useComposer((s) => s.transcribing);
  const voicePreview = useComposer((s) => s.voicePreview);
  const dragging = useComposer((s) => s.dragging);
  const running = useChat((s) => s.running);
  const busy = useChat((s) => s.reasoningBusy);
  const load = useChat((s) => s.load);
  const hasSession = useChat((s) => s.coding !== null);
  const status = useChat((s) => s.status);
  const project = useView((s) => s.project);
  const chatOpen = useView((s) => s.chat !== null);
  const chatModel = useView((s) => s.chat?.model);
  const account = useData((s) => s.account);
  const settings = useData((s) => s.settings);
  const jump = useFeedUi((s) => s.jump);
  const files = useRef<HTMLInputElement>(null);
  const attach = useRef<HTMLButtonElement>(null);
  const slot = useRef<HTMLDivElement>(null);
  // со стеклом само поле живёт в отдельном виде над лентой, здесь остаётся пустая подложка того же размера
  const native = useNativeTiles();
  const menu = useFieldHost((s) => s.menu);
  const expanded = useFieldHost((s) => s.expanded);
  const height = useFieldHost((s) => s.height);
  const access = useDeveloperAccess();

  useEffect(() => { document.body.classList.toggle('agent-working', running); return () => document.body.classList.remove('agent-working'); }, [running]);

  // файлы можно перетащить в любое место окна
  useEffect(() => {
    const over = (event: DragEvent) => {
      if (!event.dataTransfer?.types.includes('Files')) return;
      event.preventDefault();
      composer.set({ dragging: true });
    };
    const drop = (event: DragEvent) => {
      event.preventDefault();
      composer.set({ dragging: false });
      if (event.dataTransfer?.files.length) guarded(() => upload(event.dataTransfer!.files, !development))();
    };
    const leave = (event: DragEvent) => { if (!event.relatedTarget) composer.set({ dragging: false }); };
    document.addEventListener('dragover', over);
    document.addEventListener('drop', drop);
    document.addEventListener('dragleave', leave);
    return () => {
      document.removeEventListener('dragover', over);
      document.removeEventListener('drop', drop);
      document.removeEventListener('dragleave', leave);
    };
  }, [development]);

  const models = !development && account ? cloudModels(scope) : [];
  const reasoningDisabled = running || busy || load !== 'ready' || (chatOpen && !hasSession);

  // у Free, пробного периода и Base Докки Code закрыт: вместо поля ввода приглашение, чат остаётся доступным для чтения
  if (development && access.locked) return <LockedComposer />;

  return (
    <section id="composer" aria-label="Новое сообщение" className={`${expanded ? 'expanded' : ''}${dragging ? ' file-drop' : ''}${recording ? ' recording' : ''}${transcribing ? ' transcribing' : ''}`} inert={search} data-product-scope={scope}>
      <UsageNotice />
      <QueuePanel />
      <button
        id="jump-bottom"
        type="button"
        className="icon-button jump-bottom"
        title="К последнему сообщению"
        aria-label="К последнему сообщению"
        data-shown={jump || undefined}
        aria-hidden={!jump || undefined}
        tabIndex={jump ? undefined : -1}
        onClick={() => { releaseFeed(); document.getElementById('feed')?.scrollTo({ top: document.getElementById('feed')!.scrollHeight, behavior: reducedMotion() ? 'auto' : 'smooth' }); }}
      >
        <ArrowDown aria-hidden="true" />
      </button>
      <RequestPanel />
      <SkillChips />
      <AttachmentStrip />
      {(recording || transcribing) && voicePreview && <div className="voice-preview" role="status" aria-live="polite">{voicePreview}</div>}
      {native ? (
        <div ref={slot} className={`composer-field field-slot${expanded ? ' expanded' : ''}`} data-glass="composer" style={{ minHeight: height || undefined }} />
      ) : (
        <ComposerField attach={attach} menuOpen={menu} />
      )}
      <input
        id="files"
        ref={files}
        type="file"
        multiple
        hidden
        aria-label="Выбрать файлы"
        onChange={(event) => { const picked = [...(event.target.files ?? [])]; event.target.value = ''; guarded(() => upload(picked, !development))(); }}
      />
      <footer className="composer-toolbar">
        {development && (
          <button id="workspace-selector" type="button" className="workspace-selector chat-control" title="Выбрать рабочую папку" aria-label="Выбрать рабочую папку" aria-haspopup="dialog" onClick={guarded(chooseWorkspace)}>
            <Folder size={15} aria-hidden="true" />
            <span>{project?.name || 'Выбрать проект'}</span>
            <ChevronRight size={12} aria-hidden="true" />
          </button>
        )}
        <div className="agent-controls">
          {development && (
            <Select
              label="Разрешения действий"
              side="above"
              value={settings.permission_profile === 'trusted' || settings.permission_profile === 'full' ? settings.permission_profile : 'ask'}
              disabled={planning}
              options={PERMISSION_OPTIONS}
              onChange={(value) => guarded(() => savePermission(value as 'ask' | 'trusted' | 'full'))()}
            />
          )}
          {!development && models.length > 1 && (
            <Select
              label="Модель"
              side="above"
              value={selectedCloudModel(scope, chatModel)}
              options={models.map((model) => ({ value: model.id, label: model.name.replaceAll('Dokky', 'Докки'), disabled: !model.available }))}
              onChange={(value) => guarded(() => changeChatModel(value))()}
            />
          )}
          {development && (
            <Select
              label="Режим разработки"
              side="above"
              value={currentReasoningMode()}
              disabled={reasoningDisabled}
              options={[{ value: 'light', label: 'Light' }, { value: 'pro', label: 'Pro' }, { value: 'max', label: 'Max' }]}
              onChange={(value) => guarded(() => changeReasoningMode(value))()}
            />
          )}
        </div>
      </footer>
      <div className="composer-meta">
        <span id="status" role="status" aria-live="polite" title={status} data-idle={String(!status || status === 'Готово')}>{status}</span>
      </div>
      {development && (
        <button
          id="planning-chip"
          type="button"
          className="planning-chip"
          title="Выключить планирование"
          hidden={!planning}
          disabled={running}
          onClick={() => composer.set({ agentMode: 'development' })}
        >
          <ListTodo aria-hidden="true" />
          <span>Планирование</span>
          <X aria-hidden="true" />
        </button>
      )}
      <AttachMenu trigger={native ? slot : attach} open={menu} onClose={() => fieldHost.set({ menu: false })} fileInput={files} />
    </section>
  );
}


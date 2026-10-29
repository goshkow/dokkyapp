import { Copy, Layers, Pencil, Play, Plus, Sparkles, Trash2, Zap } from 'lucide-react';
import { useEffect, useState } from 'react';
import { ApiError, del, post, put } from '../../lib/api';
import { loadLocal, useData } from '../../state/data';
import { confirmDialog, guarded, inputDialog } from '../../state/overlay';
import { attachSkill, groupSkills, loadSkills, useSkills, type Skill } from '../../state/skills';
import { focusComposer } from '../../state/composer';
import { lastProject } from '../../state/data';
import { newChat, openChat } from '../../state/view';
import { Dialog } from '../ui/Dialog';
import { Empty, Hint, PageRoot } from './shared';
import { PluginCatalog, PluginsPanel } from './PluginsPanel';
import { pluginFile } from '../../state/plugins';

interface Form { id: number | null; name: string; description: string; instructions: string }

const LIMIT = 20000;
const blank: Form = { id: null, name: '', description: '', instructions: '' };

export const instructionOf = (skill: Skill): string => {
  const text = skill.workflow.instructions?.trim();
  if (text) return text;
  const steps = skill.workflow.checklist ?? skill.workflow.steps ?? [];
  return steps.map((step, index) => `${index + 1}. ${typeof step === 'string' ? step : step.title}`).join('\n');
};

function Editor({ form, onClose, onSaved }: { form: Form | null; onClose: () => void; onSaved: () => void }) {
  const [value, setValue] = useState<Form | null>(form);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { setValue(form); setError(''); }, [form]);
  const current = value ?? form;
  const save = async () => {
    if (!current) return;
    setBusy(true);
    setError('');
    try {
      const body = { name: current.name.trim(), description: current.description.trim(), workflow: { mode: 'development', instructions: current.instructions.trim() } };
      if (current.id == null) await post('/api/skills', body);
      else await put(`/api/skills/${current.id}`, body);
      onSaved();
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : (failure as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={form !== null} onClose={onClose} className="form-dialog wide" label="Навык">
      {current && (
        <form className="form-body" onSubmit={(event) => { event.preventDefault(); void save(); }}>
          <h2>{current.id == null ? 'Новый навык' : 'Изменить навык'}</h2>
          <label>Название
            <input className="page-input" value={current.name} maxLength={80} required autoFocus placeholder="Например, ревью моих pull request" onChange={(event) => setValue({ ...current, name: event.target.value })} />
          </label>
          <label>Коротко: для чего
            <input className="page-input" value={current.description} maxLength={200} placeholder="Одна строка, она видна в списке" onChange={(event) => setValue({ ...current, description: event.target.value })} />
          </label>
          <label>Инструкция для агента
            <textarea className="page-input" rows={12} required value={current.instructions} placeholder={'Опишите порядок работы, что проверять, чего не делать и в каком виде отдать результат.\nИнструкция добавляется к вашему запросу как указание пользователя.'} onChange={(event) => setValue({ ...current, instructions: event.target.value })} />
            <small data-over={current.instructions.length > LIMIT || undefined}>{current.instructions.length} / {LIMIT}</small>
          </label>
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="form-actions">
            <button type="button" className="secondary" onClick={onClose}>Отмена</button>
            <button type="submit" className="primary" disabled={busy || current.instructions.length > LIMIT}>{busy ? 'Сохраняю…' : 'Сохранить'}</button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

/** Текст навыка плагина читается из его файла (с проверкой хэша) только когда человек раскрыл карточку. */
function PluginInstructions({ skill }: { skill: Skill }) {
  const [text, setText] = useState<string | null>(null);
  const load = () => {
    if (text !== null || !skill.plugin) return;
    setText('Загружаю…');
    pluginFile(skill.plugin.id, skill.plugin.path).then(setText, (error: Error) => setText(error.message));
  };
  return (
    <details className="skill-details" onToggle={(event) => { if ((event.target as HTMLDetailsElement).open) load(); }}>
      <summary>Инструкция агенту</summary>
      <pre>{text ?? ''}</pre>
    </details>
  );
}

function Card({ skill, onEdit, onCopy, onChange }: { skill: Skill; onEdit: () => void; onCopy: () => void; onChange: () => void }) {
  const projects = useData((s) => s.projects);
  const text = instructionOf(skill);
  const plugin = skill.plugin;
  const use = () => {
    attachSkill(skill);
    newChat(lastProject());
    focusComposer();
  };
  const execute = guarded(async () => {
    if (!projects.length) throw new Error('Сначала откройте проект');
    const value = await inputDialog('Запустить навык', [
      { name: 'project_id', label: 'Проект', type: 'select', options: projects.map((project) => ({ value: project.id, label: project.name })) },
      { name: 'task', label: 'Задание', type: 'textarea', value: skill.description || skill.name, required: true },
    ], 'Запустить');
    if (!value) return;
    const result = await post<{ chat: import('../../lib/types').Chat }>(`/api/skills/${skill.id}/execute`, { request_id: crypto.randomUUID(), project_id: Number(value.project_id), task: value.task });
    await loadLocal(false);
    openChat(result.chat);
  });
  const automatic = skill.enabled !== false;
  const toggleAutomatic = guarded(async () => {
    await post(`/api/skills/${skill.id}/enabled`, { enabled: !automatic });
    onChange();
  });
  const remove = guarded(async () => {
    if (!(await confirmDialog('Удалить навык?', skill.name, 'Удалить'))) return;
    await del(`/api/skills/${skill.id}`);
    onChange();
  });
  if (plugin) {
    return (
      <article className="skill-card" data-plugin>
        <div className="skill-head">
          <h3>{skill.name}</h3>
          <span className="skill-badge">{plugin.kind === 'command' ? 'Команда плагина' : 'Плагин'}</span>
        </div>
        {skill.description && <p>{skill.description}</p>}
        <PluginInstructions skill={skill} />
        <p className="plugin-hint">
          {plugin.kind === 'command' || !plugin.model_invocation
            ? `Запускает только человек: «Использовать» или /${skill.name} в начале сообщения.`
            : `Агент подключает сам, когда навык подходит к задаче, или вы: /${skill.name}.`}
        </p>
        <div className="skill-actions">
          <button type="button" className="primary" onClick={use} title="Подключить к новому запросу"><Zap aria-hidden="true" /><span>Использовать</span></button>
        </div>
      </article>
    );
  }
  return (
    <article className="skill-card" data-builtin={skill.builtin || undefined}>
      <div className="skill-head">
        <h3>{skill.name}</h3>
        {skill.builtin && <span className="skill-badge">Стандартный</span>}
      </div>
      {skill.description && <p>{skill.description}</p>}
      <details className="skill-details">
        <summary>Инструкция агенту</summary>
        <pre>{text || 'Инструкция не задана'}</pre>
      </details>
      <div className="skill-actions">
        <button type="button" className="primary" onClick={use} title="Подключить к новому запросу"><Zap aria-hidden="true" /><span>Использовать</span></button>
        <button type="button" className="secondary" onClick={execute} title="Создать чат в проекте и сразу начать"><Play aria-hidden="true" /><span>В проекте</span></button>
        <button
          type="button"
          className="icon-button"
          aria-pressed={automatic}
          title={automatic ? 'Агент сам подключает навык, когда он подходит к задаче. Нажмите, чтобы выключить' : 'Агент не подключает навык сам. Нажмите, чтобы включить автоподбор'}
          aria-label="Автоподбор навыка"
          data-off={automatic ? undefined : true}
          onClick={toggleAutomatic}
        >
          <Sparkles aria-hidden="true" />
        </button>
        <button type="button" className="icon-button" title="Сделать свою копию" aria-label="Сделать свою копию" onClick={onCopy}><Copy aria-hidden="true" /></button>
        {!skill.builtin && <button type="button" className="icon-button" title="Изменить" aria-label="Изменить" onClick={onEdit}><Pencil aria-hidden="true" /></button>}
        {!skill.builtin && <button type="button" className="icon-button" title="Удалить" aria-label="Удалить" onClick={remove}><Trash2 aria-hidden="true" /></button>}
      </div>
    </article>
  );
}

type Tab = 'skills' | 'plugins' | 'catalog';
const TABS: Array<[Tab, string]> = [['skills', 'Навыки'], ['plugins', 'Плагины'], ['catalog', 'Каталог плагинов']];

/** «Навыки»: готовые инструкции для агента. Подключаются к запросу через «+» в поле ввода или кнопкой «Использовать». */
export function SkillsPage() {
  const items = useSkills((s) => s.items);
  const loaded = useSkills((s) => s.loaded);
  const error = useSkills((s) => s.error);
  const [query, setQuery] = useState('');
  const [editing, setEditing] = useState<Form | null>(null);
  const [tab, setTab] = useState<Tab>('skills');
  useEffect(() => { void loadSkills(true); }, []);
  const groups = groupSkills(items, query);
  const reload = () => { void loadSkills(true); };
  const tabs = (
    <div className="lib-tabs" role="tablist" aria-label="Навыки и плагины">
      {TABS.map(([id, label]) => (
        <button key={id} type="button" role="tab" aria-selected={id === tab} onClick={() => setTab(id)}>{label}</button>
      ))}
    </div>
  );
  if (tab !== 'skills') {
    return (
      <PageRoot title="Навыки" subtitle="Плагины Claude Code и Codex с GitHub">
        {tabs}
        {tab === 'plugins' ? <PluginsPanel onCatalog={() => setTab('catalog')} /> : <PluginCatalog />}
      </PageRoot>
    );
  }
  return (
    <PageRoot
      title="Навыки"
      subtitle={loaded ? `${items.length} ${items.length === 1 ? 'навык' : 'навыков'}` : undefined}
      commands={<button type="button" className="primary" onClick={() => setEditing(blank)}><Plus aria-hidden="true" /><span>Новый навык</span></button>}
    >
      {tabs}
      <Hint>Навык - это готовая инструкция для агента: как делать ревью, искать баги, писать тесты. Подключите его к запросу кнопкой «+» в поле ввода, пункт «Навыки», или кнопкой «Использовать» здесь. Инструкция уйдёт агенту вместе с вашим сообщением как ваше собственное указание.</Hint>
      <label className="page-search"><span className="sr-only">Найти навык</span>
        <input className="page-input" type="search" placeholder="Найти навык" value={query} onChange={(event) => setQuery(event.target.value)} />
      </label>
      {error && !loaded && <Empty title="Не удалось загрузить навыки" detail={error} />}
      {loaded && !groups.length && <Empty icon={<Layers size={24} aria-hidden="true" />} title={query ? 'Ничего не найдено' : 'Навыков пока нет'} />}
      {groups.map(([category, skills]) => (
        <section key={category} className="skill-section">
          <h2 className="page-section-title">{category}</h2>
          <div className="skill-grid">
            {skills.map((skill) => (
              <Card
                key={skill.id}
                skill={skill}
                onChange={reload}
                onEdit={() => setEditing({ id: Number(skill.id), name: skill.name, description: skill.description, instructions: instructionOf(skill) })}
                onCopy={() => setEditing({ id: null, name: `${skill.name} (мой)`.slice(0, 80), description: skill.description, instructions: instructionOf(skill) })}
              />
            ))}
          </div>
        </section>
      ))}
      <Editor form={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload(); }} />
    </PageRoot>
  );
}

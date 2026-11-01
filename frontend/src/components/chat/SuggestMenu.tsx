import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useChat } from '../../state/chatCore';
import { useComposer } from '../../state/composer';
import { fieldHost, useFieldHost } from '../../state/field';
import { loadSkills, useSkills } from '../../state/skills';
import { liveToken, loadProjectCommands, pick, suggestions, useProjectCommands } from '../../state/suggest';
import { useView } from '../../state/view';
import { loadFiles, useWorkspace } from '../../state/workspace';

/** Список подсказок над полем. Фокус остаётся в поле: стрелки, Enter и Esc поле пересылает сюда (suggestActions). */
export function SuggestMenu() {
  const raw = useFieldHost((s) => s.suggest);
  const index = useFieldHost((s) => s.suggestIndex);
  const text = useComposer((s) => s.text);
  const token = liveToken(raw, text);
  // от этих хранилищ зависит список: при их смене он пересчитывается
  const skills = useSkills((s) => s.items);
  const loaded = useSkills((s) => s.loaded);
  const tree = useWorkspace((s) => s.tree);
  const project = useView((s) => s.project);
  const running = useChat((s) => s.running);
  const planning = useComposer((s) => s.agentMode);
  const commands = useProjectCommands((s) => s.items);
  const items = useMemo(() => suggestions(token), [token?.kind, token?.query, skills, tree.files, running, planning, commands]);
  const panel = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState<{ left: number; bottom: number; width: number } | null>(null);

  useEffect(() => {
    if (token?.kind === '/' && !loaded) void loadSkills();
    if (token?.kind === '@' && project && (tree.projectId !== project.id || tree.state === 'idle')) void loadFiles();
  }, [token?.kind, loaded, project?.id, tree.projectId, tree.state]);

  // один запрос на открытие меню: подтверждённая в настройках команда появляется без перезапуска
  const slashOpen = token?.kind === '/';
  useEffect(() => {
    if (slashOpen && project) loadProjectCommands(project.id);
  }, [slashOpen, project?.id]);

  useEffect(() => {
    const count = token ? items.length : 0;
    const { suggestCount, suggestIndex } = fieldHost.get();
    if (count !== suggestCount || suggestIndex >= Math.max(1, count)) fieldHost.set({ suggestCount: count, suggestIndex: Math.min(suggestIndex, Math.max(0, count - 1)) });
  }, [token, items.length]);

  // над полем, не заходя на него: под стеклом поле - отдельный вид поверх этой страницы
  useLayoutEffect(() => {
    if (!token) return undefined;
    const measure = () => {
      const field = document.querySelector<HTMLElement>('#composer .composer-field');
      if (!field) return;
      const rect = field.getBoundingClientRect();
      setPlace({ left: rect.left, bottom: window.innerHeight - rect.top + 8, width: Math.min(rect.width, 560) });
    };
    measure();
    const composer = document.getElementById('composer');
    const observer = new ResizeObserver(measure);
    if (composer) observer.observe(composer);
    window.addEventListener('resize', measure);
    return () => { observer.disconnect(); window.removeEventListener('resize', measure); };
  }, [Boolean(token)]);

  useEffect(() => {
    panel.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [index]);

  if (!token || !place) return null;
  const empty = token.kind === '@' ? (project ? (tree.state === 'loading' ? 'Загружаю файлы…' : 'Файл не найден') : 'Откройте проект, чтобы упоминать файлы')
    : loaded ? 'Нет такой команды или навыка' : 'Загружаю…';
  return (
    <div
      ref={panel}
      className="composer-suggest"
      role="listbox"
      aria-label={token.kind === '@' ? 'Файлы проекта' : 'Команды и навыки'}
      style={{ left: place.left, bottom: place.bottom, width: place.width }}
      onMouseDown={(event) => event.preventDefault()}
    >
      {items.length === 0 && <div className="suggest-empty">{empty}</div>}
      {items.map((item, position) => (
          <div
            key={item.key}
            role="option"
            aria-selected={position === index}
            aria-disabled={item.disabled || undefined}
            className="suggest-item"
            data-kind={item.kind}
            onMouseEnter={() => { if (position !== index) fieldHost.set({ suggestIndex: position }); }}
            onClick={() => pick(position)}
          >
            <span className="suggest-label">{item.label}</span>
            <span className="suggest-hint">{item.hint}</span>
          </div>
      ))}
      <div className="suggest-keys" aria-hidden="true">↑↓ выбрать · Enter или Tab вставить · Esc закрыть</div>
    </div>
  );
}

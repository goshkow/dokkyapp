import { useEffect, useState } from 'react';
import { api, post, put } from '../../lib/api';
import type { Project } from '../../lib/types';
import { confirmDialog, guarded } from '../../state/overlay';
import { Block, Pick, Row, Section, type Say } from './parts';

type ReviewMode = 'auto' | 'always' | 'off';
interface QaCommand { kind: 'lint' | 'typecheck' | 'test' | 'build'; command: string; cwd: string }
interface ProjectSkill { path: string; name: string; description: string; sha256: string; status: 'pending' | 'trusted' | 'rejected'; instructions: string }
interface Knowledge { location: 'app' | 'workspace' | 'off'; folder: string | null; notes: unknown[] }

const KINDS = new Set(['lint', 'typecheck', 'test', 'build']);
const STATUS = { pending: 'Ждёт решения', trusted: 'Подтверждён', rejected: 'Отклонён' } as const;

/** «вид: команда» или «вид @папка: команда» построчно. */
export function parseCommands(text: string): QaCommand[] {
  return text.split('\n').map((line) => line.trim()).filter(Boolean).map((line, index) => {
    const match = /^(lint|typecheck|test|build)(?:\s*@\s*([^:]+))?\s*:\s*(.+)$/.exec(line);
    if (!match || !KINDS.has(match[1] ?? '')) throw new Error(`Строка ${index + 1}: нужно «test: команда» (виды: lint, typecheck, test, build)`);
    return { kind: match[1] as QaCommand['kind'], cwd: (match[2] ?? '').trim(), command: (match[3] ?? '').trim() };
  });
}

const formatCommands = (items: QaCommand[]) => items.map((item) => `${item.kind}${item.cwd ? ` @${item.cwd}` : ''}: ${item.command}`).join('\n');

type Budget = 'auto' | 'economy' | 'strict' | 'off';
interface Policy { version?: number; layers: Layer[]; ignore?: string[]; allow_type_imports?: boolean }
interface Layer { name: string; paths: string[] }
interface Cleanup { removed: { path: string; branch: string | null }[]; kept: { path: string; reason: string }[]; stale_records: string[]; base: string }

const IGNORE = 'ignore';
const masks = (text: string) => text.split(',').map((item) => item.trim()).filter(Boolean);

/** Слои построчно от внутреннего к внешнему: «имя: маска, маска»; строка «ignore: маска» - пути вне проверки. */
export function parsePolicy(text: string, previous: Policy | null): Policy | null {
  const layers: Layer[] = [];
  let ignore: string[] = [];
  text.split('\n').map((line) => line.trim()).filter((line) => line && !line.startsWith('#')).forEach((line, index) => {
    const at = line.indexOf(':');
    const name = at > 0 ? line.slice(0, at).trim() : '';
    const paths = masks(at > 0 ? line.slice(at + 1) : '');
    if (!name || !paths.length) throw new Error(`Строка ${index + 1}: нужно «слой: маска, маска», например «domain: src/domain/**»`);
    if (name.toLowerCase() === IGNORE) ignore = [...ignore, ...paths];
    else layers.push({ name, paths });
  });
  if (!layers.length) return null;
  if (layers.length < 2) throw new Error('Нужно хотя бы два слоя: внутренний и внешний');
  return { layers, ignore, allow_type_imports: previous?.allow_type_imports ?? false };
}

export const formatPolicy = (policy: Policy | null): string => !policy ? '' : [
  ...policy.layers.map((layer) => `${layer.name}: ${layer.paths.join(', ')}`),
  ...(policy.ignore?.length ? [`${IGNORE}: ${policy.ignore.join(', ')}`] : []),
].join('\n');

/** Настройки проверки результата: независимое ревью, команды проверок, навыки и знания проекта. */
export function QualitySettings({ project, say }: { project: Project | null; say: Say }) {
  const [review, setReview] = useState<ReviewMode | null>(null);
  const [commands, setCommands] = useState<string | null>(null);
  const [skills, setSkills] = useState<ProjectSkill[] | null>(null);
  const [knowledge, setKnowledge] = useState<Knowledge | null>(null);
  const [budget, setBudget] = useState<Budget | null>(null);
  const [policy, setPolicy] = useState<Policy | null | undefined>(undefined);
  const [cleanup, setCleanup] = useState<Cleanup | null>(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => { api<{ mode: ReviewMode }>('/api/settings/ai-review').then((value) => setReview(value.mode)).catch(() => setReview('auto')); }, []);
  useEffect(() => { api<{ mode: Budget }>('/api/settings/quality-budget').then((value) => setBudget(value.mode)).catch(() => setBudget('auto')); }, []);
  // данные другого проекта не должны остаться в полях: иначе «Сохранить» записало бы их в текущий
  useEffect(() => {
    setCommands(null);
    setSkills(null);
    setKnowledge(null);
    setPolicy(undefined);
    setCleanup(null);
  }, [project?.id]);
  useEffect(() => {
    if (!project) return;
    let current = true;
    const keep = <T,>(apply: (value: T) => void) => (value: T) => { if (current) apply(value); };
    setError('');
    Promise.all([
      api<{ commands: QaCommand[] }>(`/api/projects/${project.id}/qa-commands`).then(keep((value) => setCommands(formatCommands(value.commands)))),
      api<{ skills: ProjectSkill[] }>(`/api/projects/${project.id}/skills`).then(keep((value) => setSkills(value.skills))),
      api<Knowledge>(`/api/projects/${project.id}/knowledge`).then(keep(setKnowledge)),
      api<{ policy: Policy | null }>(`/api/projects/${project.id}/architecture`).then(keep((value) => setPolicy(value.policy))),
    ]).catch(keep((reason: Error) => setError(reason.message)));
    return () => { current = false; };
  }, [project, attempt]);
  const decide = (skill: ProjectSkill, trust: boolean) => guarded(async () => {
    if (trust && !(await confirmDialog(`Доверять навыку «${skill.name}»?`, `${skill.path}\n\n${skill.instructions.slice(0, 1500)}`, 'Доверять'))) return;
    await post(`/api/projects/${project?.id}/skills/decision`, { path: skill.path, sha256: skill.sha256, trust });
    setAttempt((value) => value + 1);
  });
  const suggest = guarded(async () => {
    if (!project) return;
    const found = await post<{ layers: Layer[]; note?: string; evaluation?: { violations: number } | null }>(`/api/projects/${project.id}/architecture/suggest`);
    if (found.layers.length < 2) {
      say(found.note || 'По графу импортов слои не выделяются - задай их вручную');
      return;
    }
    const next: Policy = { layers: found.layers, ignore: policy?.ignore ?? [], allow_type_imports: policy?.allow_type_imports ?? false };
    const violations = found.evaluation ? `\n\nНарушений с этой разметкой сейчас: ${found.evaluation.violations}` : '';
    if (!(await confirmDialog('Применить разметку слоёв?', `${formatPolicy(next)}${violations}`, 'Применить'))) return;
    const saved = await put<{ policy: Policy | null }>(`/api/projects/${project.id}/architecture/policy`, { policy: next });
    setPolicy(saved.policy);
    say('Политика слоёв сохранена');
  });
  const inspectWorktrees = guarded(async () => {
    if (!project) return;
    setCleanup(await post<Cleanup>(`/api/projects/${project.id}/worktrees/cleanup`, { dry_run: true }));
  });
  const removeWorktrees = guarded(async () => {
    if (!project || !cleanup) return;
    const list = [...cleanup.removed.map((item) => item.path), ...cleanup.stale_records.map((path) => `${path} (папки уже нет)`)].join('\n');
    const count = cleanup.removed.length + cleanup.stale_records.length;
    if (!(await confirmDialog(`Убрать рабочие копии (${count})?`, `${list}\n\nВетки остаются; удаляются только чистые копии без своих коммитов.`, 'Убрать'))) return;
    const result = await post<Cleanup>(`/api/projects/${project.id}/worktrees/cleanup`, { dry_run: false });
    // повторная проверка на сервере: копия, которая успела измениться, останется в «оставлено»
    setCleanup({ ...result, removed: [], stale_records: [] });
    say(`Удалено рабочих копий: ${result.removed.length}`);
  });
  return (
    <>
      <Section
        title="Проверка результата"
        hint="Перед завершением задачи агент запускает проверки проекта (lint, типы, тесты) и чинит найденное, не больше трёх попыток. Если проверить не удалось, итог так и скажет."
      >
        {review && (
          <Pick
            title="Независимое ревью"
            detail="Отдельный запрос к модели смотрит итоговые изменения без истории диалога и ищет ошибки. «Авто» - для заметных правок и мест с доступом, данными и API. Ревью расходует лимит."
            options={[['auto', 'Авто'], ['always', 'Всегда'], ['off', 'Выключено']]}
            value={review}
            save={async (value) => { await post('/api/settings/ai-review', { mode: value }); setReview(value); }}
            say={say}
          />
        )}
        {project && commands !== null && (
          <Block
            title="Команды проверок проекта"
            detail="Свои команды заменяют найденные автоматически того же вида. Строка: «вид: команда» или «вид @папка: команда». Виды: lint, typecheck, test, build."
            value={commands}
            placeholder={'test: npm run test:unit\ntypecheck @frontend: npm run typecheck'}
            max={8000}
            rows={4}
            save={async (value) => {
              const saved = await put<{ commands: QaCommand[] }>(`/api/projects/${project.id}/qa-commands`, { commands: parseCommands(value) });
              setCommands(formatCommands(saved.commands));
            }}
            say={say}
          />
        )}
        {budget && (
          <Pick
            title="Объективные гейты"
            detail="После правок кода Докки меряет сложность (CRAP), дубли, слои и мутационную устойчивость тестов. «Авто» - глубина по режиму рассуждения; мутации запускают тесты проекта в изолированной копии и только если запуск команд разрешён."
            options={[['auto', 'Авто'], ['economy', 'Экономно'], ['strict', 'Строго'], ['off', 'Выключено']]}
            value={budget}
            save={async (value) => { await post('/api/settings/quality-budget', { mode: value }); setBudget(value); }}
            say={say}
          />
        )}
      </Section>
      {project && policy !== undefined && (
        <Section title="Слои архитектуры" hint="Внутренний слой не должен импортировать внешний. Агент только предлагает разметку - применяете её вы.">
          <Block
            title="Политика слоёв"
            detail="Строка - слой, от внутреннего к внешнему: «имя: маска, маска». Строка «ignore: маска» исключает пути. Пустое поле выключает проверку."
            value={formatPolicy(policy)}
            placeholder={'domain: src/domain/**\napplication: src/app/**\ninfrastructure: src/infra/**, src/api/**'}
            max={8000}
            rows={4}
            save={async (value) => {
              const saved = await put<{ policy: Policy | null }>(`/api/projects/${project.id}/architecture/policy`, { policy: parsePolicy(value, policy) });
              setPolicy(saved.policy);
            }}
            say={say}
          />
          <Row title="Разметка по графу импортов" detail="Слои по направлению зависимостей; циклы попадают в один слой. Перед сохранением покажу, сколько нарушений даст разметка.">
            <button type="button" className="pref-button" onClick={suggest}>Предложить</button>
          </Row>
        </Section>
      )}
      {project && (
        <Section title="Рабочие копии (worktree)" hint="Удаляются только копии, созданные Докки: без незакоммиченных правок, без коммитов сверх текущей ветки и не занятые агентом. Ветки не удаляются.">
          <Row
            title="Очистка"
            detail={cleanup
              ? `Можно удалить: ${cleanup.removed.length}; оставлено: ${cleanup.kept.length}${cleanup.stale_records.length ? `; устаревших записей: ${cleanup.stale_records.length}` : ''}`
              : 'Сначала покажу, что будет удалено'}
          >
            <button type="button" className="pref-button" onClick={inspectWorktrees}>Проверить</button>
            {cleanup && (cleanup.removed.length > 0 || cleanup.stale_records.length > 0) && (
              <button type="button" className="pref-button" onClick={removeWorktrees}>Удалить</button>
            )}
          </Row>
          {cleanup?.kept.slice(0, 8).map((item) => <Row key={item.path} title={item.path} detail={item.reason} />)}
        </Section>
      )}
      {project && error && (
        <Section>
          <Row title="Не удалось загрузить настройки проекта" detail={error}>
            <button type="button" className="pref-button" onClick={() => setAttempt((value) => value + 1)}>Повторить</button>
          </Row>
        </Section>
      )}
      {project && knowledge && (
        <Section title="Знания проекта" hint="Решения, соглашения и особенности проекта, которые агент запоминает и проверяет по коду. Ваши правки агент не перезаписывает.">
          <Pick
            title="Где хранить заметки"
            detail={knowledge.folder ? `Markdown-файлы: ${knowledge.folder}` : 'Заметки остаются только в данных Докки'}
            options={[['app', 'В данных Докки'], ['workspace', 'В папке проекта'], ['off', 'Не выгружать']]}
            value={knowledge.location}
            save={async (value) => {
              await post(`/api/projects/${project.id}/knowledge/settings`, { location: value });
              setAttempt((count) => count + 1);
            }}
            say={say}
          />
          <Row title="Заметок" detail={String(knowledge.notes.length)}>
            <button type="button" className="pref-button" disabled={knowledge.location === 'off'} onClick={guarded(async () => {
              await post(`/api/projects/${project.id}/knowledge/sync`);
              say('Знания синхронизированы');
              setAttempt((count) => count + 1);
            })}>Синхронизировать</button>
          </Row>
        </Section>
      )}
      {project && skills && skills.length > 0 && (
        <Section title="Навыки проекта" hint="Файлы SKILL.md из папок .dokky, .claude и .agents проекта. Агент использует навык только после вашего подтверждения именно этой версии файла.">
          {skills.map((skill) => (
            <Row key={skill.path} title={`${skill.name} · ${STATUS[skill.status]}`} detail={`${skill.path}${skill.description ? ` - ${skill.description}` : ''}`}>
              {skill.status !== 'trusted' && <button type="button" className="pref-button" onClick={decide(skill, true)}>Доверять</button>}
              {skill.status !== 'rejected' && <button type="button" className="pref-button" onClick={decide(skill, false)}>{skill.status === 'trusted' ? 'Отозвать' : 'Отклонить'}</button>}
            </Row>
          ))}
        </Section>
      )}
    </>
  );
}

/* Подсказки в поле ввода, как в Claude Code: «/» в начале - команды и навыки, «@» - файлы проекта.
   Токен находит поле (под стеклом оно живёт в своём виде), список, выбор и вставку ведёт главный вид. */
import { api } from '../lib/api';
import { exposeActions, remote } from '../lib/mirror';
import { createStore, useStore } from '../lib/store';
import { chat } from './chatCore';
import { composer, focusComposer, setText } from './composer';
import { fieldHost, type SuggestToken } from './field';
import { attachSkill, skillsStore } from './skills';
import { startChat } from './surface';
import { view } from './view';
import { selectInspector, workspace } from './workspace';

export interface Suggestion {
  key: string;
  kind: 'command' | 'skill' | 'file';
  label: string;
  hint: string;
  disabled?: boolean;
  /** что встанет на место токена; null - выбор не прошёл */
  run: () => string | null;
}

const LIMIT = 8;

const COMMANDS: Array<{ name: string; label: string; disabled?: () => boolean; run: () => void }> = [
  { name: 'new', label: 'Новый чат', run: () => startChat(view.get().project ?? null) },
  {
    name: 'plan', label: 'Планирование: сначала план, потом работа',
    disabled: () => chat.get().running, run: () => composer.set({ agentMode: 'plan' }),
  },
  { name: 'files', label: 'Файлы проекта', run: () => void selectInspector('files') },
  { name: 'changes', label: 'Изменения агента', run: () => void selectInspector('diff') },
];

/** Команды проекта (`.claude/commands`), которые человек подтвердил: текст подставит сервер при отправке. */
export interface ProjectCommand { name: string; description: string; argument_hint: string }
export const projectCommands = createStore<{ projectId: number | null; items: ProjectCommand[] }>({ projectId: null, items: [] });
export const useProjectCommands = <S,>(selector: (state: { items: ProjectCommand[] }) => S): S => useStore(projectCommands, selector);

/** Перечитывается при каждом открытии меню: подтверждение в настройках сразу видно в списке. */
export function loadProjectCommands(projectId: number): void {
  api<{ commands: ProjectCommand[] }>(`/api/projects/${projectId}/commands`)
    .then((value) => { if (view.get().project?.id === projectId) projectCommands.set({ projectId, items: value.commands }); })
    .catch(() => { if (view.get().project?.id === projectId) projectCommands.set({ projectId, items: [] }); });
}

/** Совпадение получше - выше: с начала названия, потом внутри, потом по описанию. */
function rank(query: string, name: string, extra = ''): number {
  const needle = query.toLowerCase();
  const value = name.toLowerCase();
  if (!needle) return 3;
  if (value.startsWith(needle)) return 0;
  // внутри имени - только с начала слова, иначе «/pl» цепляет «principles»
  if (value.split(/[\s\-_.()/]+/).some((word) => word.startsWith(needle))) return 1;
  // по описанию - только с трёх букв: иначе «/pl» цепляет случайные слова
  if (needle.length >= 3 && extra.toLowerCase().includes(needle)) return 2;
  return -1;
}

function slashItems(query: string): Suggestion[] {
  const commands = COMMANDS.flatMap((command) => {
    const score = rank(query, command.name, command.label);
    return score < 0 ? [] : [{
      score,
      item: {
        key: `command:${command.name}`, kind: 'command' as const, label: `/${command.name}`, hint: command.label,
        disabled: command.disabled?.() ?? false,
        run: () => { command.run(); return ''; },
      },
    }];
  });
  const { projectId, items: own } = projectCommands.get();
  const project = projectId != null && projectId === view.get().project?.id ? own : [];
  const projectItems = project.flatMap((command) => {
    const score = rank(query, command.name, command.description);
    return score < 0 ? [] : [{
      score,
      item: {
        key: `project:${command.name}`, kind: 'command' as const, label: `/${command.name}`,
        hint: [command.argument_hint, command.description].filter(Boolean).join(' · ') || 'Команда проекта',
        // аргументы человек допишет после имени, как в Claude Code
        run: () => `/${command.name} `,
      },
    }];
  });
  const skills = skillsStore.get().items.filter((skill) => skill.enabled !== false).flatMap((skill) => {
    const name = skill.name.trim();
    const score = rank(query, name.replace(/\s+/g, '-'), skill.description);
    return score < 0 ? [] : [{
      score,
      item: {
        key: `skill:${skill.id}`, kind: 'skill' as const, label: `/${name}`, hint: skill.description || skill.category,
        run: () => { attachSkill(skill); return ''; },
      },
    }];
  });
  return [...commands, ...projectItems, ...skills].sort((a, b) => a.score - b.score).slice(0, LIMIT).map(({ item }) => item);
}

/** Файлы по имени: сначала совпадение с начала имени файла, потом внутри имени, потом в пути. */
export function rankFiles(files: string[], query: string): string[] {
  const needle = query.toLowerCase();
  const scored = files.flatMap((path) => {
    const lower = path.toLowerCase();
    const name = lower.slice(lower.lastIndexOf('/') + 1);
    const score = !needle ? 3 : name.startsWith(needle) ? 0 : name.includes(needle) ? 1 : lower.includes(needle) ? 2 : -1;
    return score < 0 ? [] : [{ path, score }];
  });
  return scored.sort((a, b) => a.score - b.score || a.path.length - b.path.length || a.path.localeCompare(b.path)).slice(0, LIMIT).map((item) => item.path);
}

function fileItems(query: string): Suggestion[] {
  return rankFiles(workspace.get().tree.files, query).map((path) => ({
    key: `file:${path}`, kind: 'file' as const, label: path.slice(path.lastIndexOf('/') + 1), hint: path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '',
    run: () => `@${path} `,
  }));
}

export function suggestions(token: SuggestToken | null): Suggestion[] {
  if (!token) return [];
  return token.kind === '/' ? slashItems(token.query) : fileItems(token.query);
}

/** Токен ещё стоит в тексте: после смены чата или отправки старый токен не держит меню открытым. */
export function liveToken(token: SuggestToken | null, text: string): SuggestToken | null {
  return token && text.slice(token.start, token.end) === token.kind + token.query ? token : null;
}

function setToken(token: SuggestToken | null): void {
  const current = fieldHost.get().suggest;
  if (JSON.stringify(current) === JSON.stringify(token)) return;
  fieldHost.set({ suggest: token, suggestIndex: 0 });
}

function move(step: number): void {
  const { suggestIndex, suggestCount } = fieldHost.get();
  if (suggestCount) fieldHost.set({ suggestIndex: (suggestIndex + step + suggestCount) % suggestCount });
}

export function pick(index = fieldHost.get().suggestIndex): void {
  const text = composer.get().text;
  const token = liveToken(fieldHost.get().suggest, text);
  if (!token) return void fieldHost.set({ suggest: null });
  const item = suggestions(token)[index];
  if (!item || item.disabled) return;
  const replacement = item.run();
  if (replacement === null) return;
  // ни убранный токен, ни вставка перед уже стоящим пробелом не оставляют двойного пробела
  const spaced = text[token.end] === ' ';
  const end = !replacement && spaced ? token.end + 1 : token.end;
  const insert = spaced && replacement.endsWith(' ') ? replacement.slice(0, -1) : replacement;
  // команда могла сама сменить чат и очистить поле: тогда вставлять уже некуда
  if (composer.get().text === text) setText(text.slice(0, token.start) + insert + text.slice(end));
  const placeAt = token.start + insert.length + (insert !== replacement ? 1 : 0);
  fieldHost.set({ suggest: null, suggestIndex: 0, suggestCount: 0, placeAt, place: fieldHost.get().place + 1 });
  focusComposer();
}

const table = { fieldSuggest: setToken, fieldSuggestMove: move, fieldSuggestPick: () => pick() };
exposeActions(table as unknown as Record<string, (...args: never[]) => unknown>);

export const suggestActions = {
  set: remote('fieldSuggest', table.fieldSuggest),
  move: remote('fieldSuggestMove', table.fieldSuggestMove),
  pick: remote('fieldSuggestPick', table.fieldSuggestPick),
};

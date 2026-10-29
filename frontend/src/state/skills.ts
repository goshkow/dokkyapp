import { api } from '../lib/api';
import { createStore, useStore } from '../lib/store';
import { composer } from './composer';
import { notify } from './overlay';

export interface SkillPlugin { id: number; name: string; title: string; kind: 'skill' | 'command'; path: string; model_invocation: boolean; argument_hint: string }

export interface Skill {
  /** навыки и команды плагинов: строка plugin:<плагин>:<имя> */
  id: number | string;
  name: string;
  description: string;
  builtin: boolean;
  category: string;
  /** false - агент не получает навык в подсказках и не может загрузить его сам */
  enabled?: boolean;
  workflow: { instructions?: string; checklist?: Array<string | { title: string }>; steps?: string[] };
  /** навык или команда установленного плагина: текст лежит в файле плагина */
  plugin?: SkillPlugin;
}

interface SkillsState { items: Skill[]; loaded: boolean; error: string }

export const skillsStore = createStore<SkillsState>({ items: [], loaded: false, error: '' });
export const useSkills = <S,>(selector: (state: SkillsState) => S): S => useStore(skillsStore, selector);

const MAX_SELECTED = 5;
let pending: Promise<void> | null = null;

/** Список навыков: стандартные и свои. Грузится один раз, дальше обновляется после правок. */
export function loadSkills(force = false): Promise<void> {
  if (pending) return pending;
  if (skillsStore.get().loaded && !force) return Promise.resolve();
  pending = api<Skill[]>('/api/skills')
    .then((items) => skillsStore.set({ items, loaded: true, error: '' }))
    .catch((error: Error) => skillsStore.set({ error: error.message || 'Не удалось загрузить навыки' }))
    .finally(() => { pending = null; });
  return pending;
}

/** Навык подключается к следующему запросу: его инструкции уйдут агенту как указание пользователя. */
export function attachSkill(skill: Pick<Skill, 'id' | 'name'>): void {
  const current = composer.get().skills;
  if (current.some((item) => item.id === skill.id)) return;
  if (current.length >= MAX_SELECTED) return notify(`Можно подключить не больше ${MAX_SELECTED} навыков`, true);
  composer.set({ skills: [...current, { id: skill.id, name: skill.name }] });
}

export function detachSkill(id: number | string): void {
  composer.set({ skills: composer.get().skills.filter((item) => item.id !== id) });
}

/** Навыки группами по категориям в порядке каталога; поиск по названию и описанию. */
export function groupSkills(items: Skill[], query: string): Array<[string, Skill[]]> {
  const needle = query.trim().toLowerCase();
  const found = items.filter((item) => !needle || `${item.name} ${item.description} ${item.category}`.toLowerCase().includes(needle));
  const groups = new Map<string, Skill[]>();
  for (const item of found) groups.set(item.category, [...(groups.get(item.category) ?? []), item]);
  // «Мои навыки» стоят первыми: их человек создал сам и достаёт чаще
  return [...groups.entries()].sort(([a], [b]) => (a === 'Мои навыки' ? -1 : b === 'Мои навыки' ? 1 : a.localeCompare(b, 'ru')));
}

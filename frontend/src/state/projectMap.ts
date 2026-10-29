/* Карта проекта в правой панели: мозаика файлов с сервера. Карту строит и обновляет сервер
   (при открытии проекта и после изменений файлов); здесь только показ. */
import { api } from '../lib/api';
import { createStore, useStore } from '../lib/store';
import type { TreeFile, TreeRest } from '../lib/treemap';
import { view } from './view';

export interface MapTree {
  version: number;
  files: TreeFile[];
  rest: TreeRest[];
  totals: { files: number; dirs: number; api: number; notes: number };
  state: { status: string; files: number; truncated: boolean; built_at: string | null; error: string | null };
  watch: string | null;
}

interface MapState {
  projectId: number | null;
  load: 'idle' | 'loading' | 'ready' | 'failed';
  error: string;
  tree: MapTree | null;
}

const initial = (): MapState => ({ projectId: null, load: 'idle', error: '', tree: null });

export const projectMap = createStore<MapState>(initial());
export const useProjectMap = <S,>(selector: (state: MapState) => S): S => useStore(projectMap, selector);

let request = 0;
let active = false;

/** Мозаика открытого проекта; повторная загрузка при новой версии не сбрасывает показанную карту. */
export async function loadMap(): Promise<void> {
  const project = view.get().project;
  const ticket = ++request;
  if (!project) {
    projectMap.set(initial());
    return;
  }
  const state = projectMap.get();
  if (state.projectId !== project.id) projectMap.set({ ...initial(), projectId: project.id });
  projectMap.set({ load: projectMap.get().tree ? 'ready' : 'loading', error: '' });
  try {
    const tree = await api<MapTree>(`/api/projects/${project.id}/map`);
    if (ticket !== request || view.get().project?.id !== project.id) return;
    if (!Array.isArray(tree?.files) || !Array.isArray(tree?.rest)) throw new Error('Сервер вернул карту в неизвестном виде');
    projectMap.set({ tree, load: 'ready', error: '' });
  } catch (error) {
    if (ticket !== request || (error as Error).name === 'AbortError') return;
    projectMap.set({ load: 'failed', error: (error as Error).message });
  }
}

/** Карточка карты открыта: только тогда карта подтягивается по событиям сервера. */
export function setMapActive(value: boolean): void {
  active = value;
  if (value) void loadMap();
}

let refreshTimer = 0;
/** Сервер обновил карту (правка файла, заметка агента): открытая карточка перечитывает мозаику. */
export function onMapUpdated(data: { project_id?: unknown; version?: unknown }): void {
  const project = view.get().project;
  if (!project || data.project_id !== project.id) return;
  if (!active) {
    if (projectMap.get().projectId === project.id) projectMap.set({ tree: null, load: 'idle' });
    return;
  }
  if (typeof data.version === 'number' && projectMap.get().tree?.version === data.version) return;
  window.clearTimeout(refreshTimer);
  refreshTimer = window.setTimeout(() => { void loadMap(); }, 400);
}

// открытие проекта в окне: сервер начинает сборку карты или проверку изменений, не дожидаясь вопроса агенту
let lastProject: number | null | undefined;
view.subscribe(() => {
  const id = view.get().project?.id ?? null;
  if (id === lastProject) return;
  lastProject = id;
  request += 1;
  projectMap.set({ ...initial(), projectId: id });
  if (id !== null) {
    void api(`/api/projects/${id}/map/state`).catch(() => undefined);
    if (active) void loadMap();
  }
});

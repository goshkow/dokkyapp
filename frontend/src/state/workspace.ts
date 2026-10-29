/* Правая панель: выбранная карточка, дерево файлов проекта и редактор файла. */
import { api } from '../lib/api';
import { createStore, useStore } from '../lib/store';
import { chat, stale } from './chatCore';
import { confirmDialog, notify } from './overlay';
import { setPanel } from './panels';
import { view } from './view';

export type Focus = '' | 'diff' | 'files' | 'plan' | 'browser' | 'map' | 'quality';

export interface EditorFile {
  projectId: number;
  path: string;
  hash: string;
  original: string;
  content: string;
  readOnly: boolean;
  saving: boolean;
  preview: boolean;
}

export interface TreeState {
  state: 'idle' | 'loading' | 'ready' | 'failed';
  files: string[];
  error: string;
  projectId: number | null;
}

interface WorkspaceState {
  focus: Focus;
  overview: { environment: boolean; context: boolean };
  tree: TreeState;
  editor: EditorFile | null;
  // папка, открытая с карты проекта: список файлов показывает только её
  folder: string | null;
}

export const workspace = createStore<WorkspaceState>({
  focus: '',
  overview: { environment: true, context: true },
  tree: { state: 'idle', files: [], error: '', projectId: null },
  editor: null,
  folder: null,
});
export const useWorkspace = <S,>(selector: (state: WorkspaceState) => S): S => useStore(workspace, selector);

const drafts = new Map<string, { content: string; hash: string }>();
let treeRequest = 0;
let editorRequest = 0;

function stash(): void {
  const editor = workspace.get().editor;
  if (!editor || editor.readOnly) return;
  const key = `${editor.projectId}:${editor.path}`;
  if (editor.content !== editor.original) drafts.set(key, { content: editor.content, hash: editor.hash });
  else drafts.delete(key);
}

export function clearEditor(): void {
  stash();
  editorRequest += 1;
  workspace.set({ editor: null });
}

/** Новый экран или другой проект: дерево и редактор принадлежат прежнему. */
export function resetWorkspace(): void {
  clearEditor();
  treeRequest += 1;
  workspace.set({ tree: { state: 'idle', files: [], error: '', projectId: null }, folder: null });
}

export async function loadFiles(): Promise<void> {
  const project = view.get().project;
  const version = view.get().version;
  const request = ++treeRequest;
  if (!project) {
    workspace.set({ tree: { state: 'ready', files: [], error: '', projectId: null } });
    return;
  }
  workspace.set({ tree: { state: 'loading', files: [], error: '', projectId: project.id } });
  try {
    const result = await api<{ items?: unknown[]; entries?: unknown[]; files?: unknown[] }>(`/api/projects/${project.id}/files`);
    if (request !== treeRequest || stale(version) || view.get().project?.id !== project.id) return;
    const entries = (result.items ?? result.entries ?? result.files ?? []) as Array<string | { path?: string; is_dir?: boolean; type?: string }>;
    const files = entries.flatMap((entry) => {
      const path = typeof entry === 'string' ? entry : entry.path;
      if (!path || (typeof entry !== 'string' && (entry.is_dir || ['directory', 'dir'].includes(entry.type ?? '')))) return [];
      return [path];
    });
    workspace.set({ tree: { state: 'ready', files, error: '', projectId: project.id } });
  } catch (error) {
    if (request !== treeRequest || (error as Error).name === 'AbortError') return;
    workspace.set({ tree: { state: 'failed', files: [], error: (error as Error).message, projectId: project.id } });
  }
}

export async function selectInspector(key: Focus): Promise<void> {
  workspace.set({ focus: key });
  setPanel('right', true, false);
  if (key === 'files') await loadFiles();
}

/** Папка с карты проекта: карточка «Файлы» со списком только этой папки. */
export async function openFolder(path: string): Promise<void> {
  workspace.set({ folder: path.replace(/\/+$/, '') || null });
  await selectInspector('files');
}

export const showAllFiles = (): void => workspace.set({ folder: null });

export const collapseFocus = (): void => workspace.set({ focus: '' });

export function toggleOverview(name: 'environment' | 'context'): void {
  const overview = workspace.get().overview;
  workspace.set({ overview: { ...overview, [name]: !overview[name] } });
}

export async function openFile(path: string): Promise<void> {
  const project = view.get().project;
  if (!project) return;
  stash();
  const version = view.get().version;
  const request = ++editorRequest;
  let value: { content: string; hash: string; read_only?: boolean };
  try {
    value = await api(`/api/projects/${project.id}/file?path=${encodeURIComponent(path)}`);
  } catch (error) {
    if ((error as Error).name === 'AbortError' || stale(version) || view.get().project?.id !== project.id || request !== editorRequest) return;
    throw error;
  }
  if (stale(version) || view.get().project?.id !== project.id || request !== editorRequest) return;
  const key = `${project.id}:${path}`;
  const draft = drafts.get(key);
  let content = value.content;
  if (draft && !value.read_only) {
    const restore = draft.hash === value.hash || (await confirmDialog(
      'Восстановить несохранённый текст?',
      'Файл на диске изменился после создания черновика. Если восстановить черновик и затем сохранить, новая версия файла будет заменена.',
      'Восстановить',
    ));
    if (restore) content = draft.content;
  }
  if (stale(version) || view.get().project?.id !== project.id || request !== editorRequest) return;
  stash();
  workspace.set({
    editor: { projectId: project.id, path, hash: value.hash, original: value.content, content, readOnly: Boolean(value.read_only), saving: false, preview: false },
  });
  await selectInspector('files');
}

export function editFile(content: string): void {
  const editor = workspace.get().editor;
  if (editor) workspace.set({ editor: { ...editor, content } });
}

export function previewFile(preview: boolean): void {
  const editor = workspace.get().editor;
  if (editor) workspace.set({ editor: { ...editor, preview } });
}

export async function saveFile(): Promise<void> {
  const editor = workspace.get().editor;
  if (!editor || editor.saving || editor.readOnly) return;
  const { projectId, path, content, hash } = editor;
  const version = view.get().version;
  workspace.set({ editor: { ...editor, saving: true } });
  const key = `${projectId}:${path}`;
  try {
    const result = await api<{ hash: string }>(`/api/projects/${projectId}/file`, { method: 'PUT', body: JSON.stringify({ path, content, hash }) });
    const now = workspace.get().editor;
    if (!stale(version) && now && now.projectId === projectId && now.path === path) {
      workspace.set({ editor: { ...now, hash: result.hash, original: content, saving: false } });
      if (now.content === content) drafts.delete(key);
      else drafts.set(key, { content: now.content, hash: result.hash });
      notify('Файл сохранён');
    } else {
      const draft = drafts.get(key);
      if (draft?.hash === hash) {
        if (draft.content === content) drafts.delete(key);
        else draft.hash = result.hash;
      }
    }
  } finally {
    const now = workspace.get().editor;
    if (now && now.path === path && now.saving) workspace.set({ editor: { ...now, saving: false } });
  }
}

// у открытого проекта дерево и редактор свои: при смене проекта сбрасываются
let lastProject: number | null | undefined;
view.subscribe(() => {
  const id = view.get().project?.id ?? null;
  if (lastProject === id) return;
  lastProject = id;
  const state = workspace.get();
  if (state.editor && state.editor.projectId !== id) clearEditor();
  if (state.tree.projectId !== id) {
    treeRequest += 1;
    workspace.set({ tree: { state: 'idle', files: [], error: '', projectId: null } });
  }
});
void chat;

/* Git проекта в окне: ветка в шапке, коммит и отправка в «Изменениях». Состоянием владеет главный вид, плитка правой панели показывает зеркало. */
import { api, post } from '../lib/api';
import { isMirror } from '../lib/mirror';
import { createStore, useStore } from '../lib/store';
import { chat } from './chatCore';
import { confirmDialog, notify } from './overlay';
import { ui } from './ui';
import { view } from './view';
import { reloadFiles } from './workspaceRemote';

export interface GitFile {
  path: string;
  status: 'new' | 'modified' | 'deleted';
  suggested: boolean;
}

export interface GitChecks {
  state: 'none' | 'success' | 'pending' | 'failure';
  total: number;
  passed: number;
  failed: number;
  pending: number;
  failed_names: string[];
}

export interface GitPr {
  number: number;
  title: string;
  state: string;
  draft: boolean;
  url: string;
  checks: GitChecks;
}

/** Сводка GitHub для ветки: available=false - gh нет, не вошёл или репозиторий не на GitHub (блок скрыт). */
export interface GitHub {
  available: boolean;
  pr: GitPr | null;
}

export interface GitState {
  projectId: number | null;
  repo: boolean;
  branch: string | null;
  head: string;
  branches: string[];
  files: GitFile[];
  truncated: boolean;
  remote: string | null;
  remote_url: string;
  upstream: string | null;
  ahead: number | null;
  behind: number | null;
  suggestion: string;
  github: GitHub | null;
}

const empty = (): GitState => ({
  projectId: null, repo: false, branch: null, head: '', branches: [], files: [], truncated: false, remote: null, remote_url: '', upstream: null, ahead: null, behind: null, suggestion: '', github: null,
});

export const git = createStore<GitState>(empty());
export const useGit = <S,>(selector: (state: GitState) => S): S => useStore(git, selector);

/** Отправлять есть что: новые коммиты или ветка, которой ещё нет на сервере. */
export const canPush = (state: GitState): boolean => Boolean(state.branch && state.remote && state.head && (state.upstream ? (state.ahead ?? 0) > 0 : true));

let request = 0;

export async function refreshGit(): Promise<void> {
  const { project, chat: current } = view.get();
  const token = ++request;
  if (!project || ui.get().scope !== 'development') {
    git.set(empty());
    return;
  }
  const query = typeof current?.id === 'number' ? `?chat_id=${current.id}` : '';
  try {
    const state = await api<Partial<GitState>>(`/api/projects/${project.id}/git${query}`);
    if (token === request) {
      const before = git.get();
      // PR той же ветки остаётся на экране, пока сводка обновляется
      const github = before.projectId === project.id && before.branch === (state.branch ?? null) ? before.github : null;
      git.set({ ...empty(), ...state, projectId: project.id, github });
      if (state.repo && state.remote && state.branch) void loadGithub(false);
    }
  } catch {
    // папка недоступна: блока git просто нет, остальной проект работает
    if (token === request) git.set({ ...empty(), projectId: project.id });
  }
}

let githubRequest = 0;

/** PR текущей ветки и его проверки; refresh обходит кэш сервера (кнопка «Обновить»). */
export async function loadGithub(refresh: boolean): Promise<void> {
  const { projectId: id, branch } = git.get();
  const token = ++githubRequest;
  if (id === null || !branch) return;
  try {
    const github = await api<GitHub>(`/api/projects/${id}/github${refresh ? '?refresh=true' : ''}`);
    if (token === githubRequest && git.get().projectId === id) git.set({ github });
  } catch {
    // сервер не ответил: блока PR просто нет, остальной git работает
    if (token === githubRequest && git.get().projectId === id) git.set({ github: { available: false, pr: null } });
  }
}

function projectId(): number {
  const id = git.get().projectId;
  if (id === null) throw new Error('Проект не выбран');
  return id;
}

interface GitResult { ok: boolean; error?: string; commit?: string; target?: string }

export async function switchBranch(branch: string, create = false): Promise<void> {
  const result = await post<GitResult>(`/api/projects/${projectId()}/git/switch`, { branch, create });
  if (!result.ok) throw new Error(result.error || 'Ветку не удалось переключить');
  await refreshGit();
  // список файлов обновляется в своём виде: ждать его здесь незачем
  void Promise.resolve(reloadFiles()).catch(() => undefined);
  notify(create ? `Создана ветка ${branch}` : `Ветка ${branch}`);
}

export async function commitFiles(message: string, paths: string[]): Promise<void> {
  const result = await post<GitResult>(`/api/projects/${projectId()}/git/commit`, { message, paths });
  if (!result.ok) throw new Error(result.error || 'Коммит не создан');
  await refreshGit();
  notify(`Коммит ${result.commit ?? ''} создан`);
}

export async function pushBranch(): Promise<void> {
  const state = git.get();
  if (!state.branch || !state.remote) throw new Error('Отправлять некуда: у проекта нет удалённого репозитория');
  const what = state.upstream ? `Новых коммитов: ${state.ahead ?? 0}.` : 'Ветка ещё не связана с сервером: там появится ветка с тем же именем.';
  const where = state.remote_url ? `${state.remote} (${state.remote_url})` : state.remote;
  if (!(await confirmDialog(`Отправить ветку ${state.branch}?`, `${what}\nКуда: ${where}, ветка ${state.branch}.`, 'Отправить'))) return;
  const result = await post<GitResult>(`/api/projects/${projectId()}/git/push`, { branch: state.branch, remote: state.remote, confirm: true });
  if (!result.ok) throw new Error(result.error || 'Отправка не удалась');
  await refreshGit();
  notify(`Ветка ${state.branch} отправлена`);
}

if (!isMirror) {
  let lastKey = '';
  let wasRunning = false;
  const check = () => {
    const key = `${ui.get().scope}:${view.get().project?.id ?? ''}:${view.get().chat?.id ?? ''}`;
    if (key !== lastKey) {
      lastKey = key;
      void refreshGit();
    }
  };
  // сборки идут и заканчиваются без действий в окне; кэш сервера держит запросы редкими
  window.setInterval(() => { if (document.visibilityState === 'visible' && git.get().github?.available) void loadGithub(false); }, 60_000);
  view.subscribe(check);
  ui.subscribe(check);
  // ход закончился: агент мог поправить файлы или сам переключить ветку
  chat.subscribe(() => {
    const { running } = chat.get();
    if (wasRunning && !running) void refreshGit();
    wasRunning = running;
  });
  // человек мог переключить ветку в терминале
  window.addEventListener('focus', () => { if (git.get().projectId !== null) void refreshGit(); });
}

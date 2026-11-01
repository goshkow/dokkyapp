/**
 * Действия, которые вызывают плитки Liquid Glass (вид sidebar). В главном виде это обычные функции,
 * в плитке - вызов того же действия у главного вида через хост (lib/mirror.ts).
 */
import { del } from '../lib/api';
import { exposeActions, remote } from '../lib/mirror';
import type { Chat, Project } from '../lib/types';
import { deleteChat as realDeleteChat, renameChat as realRenameChat, switchScope as realSwitchScope, togglePin as realTogglePin } from './actions';
import { loadCloud as realLoadCloud, loadLocal as realLoadLocal, saveSettings, setProjectOpen as realSetProjectOpen } from './data';
import { openLibrary as realOpenLibrary } from './library';
import { loadLimits as realLoadLimits } from './limits';
import { relocateProject as realRelocateProject } from './actions';
import { confirmDialog } from './overlay';
import { setPanel as realSetPanel, togglePanel as realTogglePanel } from './panels';
import { syncChat } from './chat';
import { restoreTurn as realRestoreTurn, rollbackChange as realRollbackChange, rollbackHunk as realRollbackHunk, selectChange as realSelectChange } from './chatAgent';
import type { Change } from './chatTypes';
import { commitFiles as realCommitFiles, pushBranch as realPushBranch, refreshGit as realRefreshGit } from './git';
import { openBilling as realOpenBilling, openSettings as realOpenSettings } from './settingsUi';
import { startChat, surfaceActions } from './surface';
import { downloadUpdate as realDownloadUpdate, restartForUpdate as realRestartForUpdate } from './updates';
import { goBack as realGoBack, goForward as realGoForward, newChat as realNewChat, openChat as realOpenChat, openPage as realOpenPage, view } from './view';

/** Убрать проект из списка: подтверждение показывает главный вид, папка на диске остаётся. */
async function realRemoveProject(project: Project): Promise<void> {
  if (!(await confirmDialog('Убрать проект из списка?', `«${project.name}»: папка и файлы останутся на диске, чаты проекта перейдут в «Недавнее».`, 'Убрать'))) return;
  await del(`/api/projects/${project.id}`);
  if (view.get().project?.id === project.id) realNewChat(null);
  await realLoadLocal(false);
}

/** Откат изменения агента: подтверждение показывает главный вид. */
const syncAfterRollback = async (): Promise<void> => { await syncChat(); await realRefreshGit(); };
const realRollback = (id: Change['id'], fileCount: number): Promise<void> => realRollbackChange(id, fileCount, syncAfterRollback);
const realHunk = (id: Change['id'], path: string, hunk: string): Promise<void> => realRollbackHunk(id, path, hunk, syncAfterRollback);
const realRestore = (turnId: number): Promise<void> => realRestoreTurn(turnId, syncAfterRollback);
/** Порядок блоков правой панели сохраняет главный вид: его данные зеркалят плитки. */
const realSaveInspectorOrder = async (order: string[]): Promise<void> => { await saveSettings({ inspector_order: order }); };

const table = {
  downloadUpdate: realDownloadUpdate,
  restartForUpdate: realRestartForUpdate,
  openChat: realOpenChat,
  newChat: startChat,
  openPage: realOpenPage,
  goBack: realGoBack,
  goForward: realGoForward,
  switchScope: realSwitchScope,
  deleteChat: realDeleteChat,
  renameChat: realRenameChat,
  togglePin: realTogglePin,
  removeProject: realRemoveProject,
  relocateProject: realRelocateProject,
  setProjectOpen: realSetProjectOpen,
  loadCloud: realLoadCloud,
  loadLocal: realLoadLocal,
  loadLimits: realLoadLimits,
  openBilling: realOpenBilling,
  openSettings: realOpenSettings,
  openLibrary: realOpenLibrary,
  togglePanel: realTogglePanel,
  setPanel: realSetPanel,
  selectChange: realSelectChange,
  rollbackChange: realRollback,
  rollbackHunk: realHunk,
  commitFiles: realCommitFiles,
  pushBranch: realPushBranch,
  refreshGit: realRefreshGit,
  restoreTurn: realRestore,
  saveInspectorOrder: realSaveInspectorOrder,
  ...surfaceActions,
};
exposeActions(table as unknown as Record<string, (...args: never[]) => unknown>);

export const openChat = remote('openChat', (chat: Chat) => realOpenChat(chat));
export const newChat = remote('newChat', (project: Project | null = null) => startChat(project));
export const openPage = remote('openPage', realOpenPage);
export const goBack = remote('goBack', realGoBack);
export const goForward = remote('goForward', realGoForward);
export const switchScope = remote('switchScope', realSwitchScope);
export const deleteChat = remote('deleteChat', realDeleteChat);
export const renameChat = remote('renameChat', realRenameChat);
export const togglePin = remote('togglePin', realTogglePin);
export const removeProject = remote('removeProject', realRemoveProject);
export const relocateProject = remote('relocateProject', realRelocateProject);
export const setProjectOpen = remote('setProjectOpen', realSetProjectOpen);
export const loadCloud = remote('loadCloud', realLoadCloud);
export const loadLocal = remote('loadLocal', realLoadLocal);
export const loadLimits = remote('loadLimits', realLoadLimits);
export const openBilling = remote('openBilling', realOpenBilling);
export const openSettings = remote('openSettings', realOpenSettings);
export const openLibrary = remote('openLibrary', realOpenLibrary);
export const togglePanel = remote('togglePanel', realTogglePanel);
export const selectChange = remote('selectChange', realSelectChange);
export const rollbackChange = remote('rollbackChange', realRollback);
export const rollbackHunk = remote('rollbackHunk', realHunk);
export const commitFiles = remote('commitFiles', realCommitFiles);
export const pushBranch = remote('pushBranch', realPushBranch);
export const refreshGit = remote('refreshGit', realRefreshGit);
export const restoreTurn = remote('restoreTurn', realRestore);
export const saveInspectorOrder = remote('saveInspectorOrder', realSaveInspectorOrder);

export const downloadUpdate = remote('downloadUpdate', realDownloadUpdate);
export const restartForUpdate = remote('restartForUpdate', realRestartForUpdate);

import { api, del, post } from '../lib/api';
import { createStore, useStore } from '../lib/store';
import { loadSkills } from './skills';

export interface PluginHook { event: string; matcher: string; type: string; command: string; timeout: number; supported: boolean; source: string }
export interface PluginVar { name: string; value: string }
export interface PluginMcp { name: string; type: string; command: string; args: string[]; cwd: string; url: string; env: PluginVar[]; headers: PluginVar[] }

export interface Plugin {
  id: number;
  name: string;
  title: string;
  description: string;
  version: string;
  repo: string;
  path: string;
  ref: string;
  commit: string;
  catalog_id: string | null;
  formats: string[];
  license: string;
  author: string;
  enabled: boolean;
  url: string;
  skills: Array<{ name: string; description: string; model_invocation: boolean }>;
  commands: Array<{ name: string; description: string; argument_hint: string }>;
  agents: Array<{ name: string; description: string }>;
  hooks: PluginHook[];
  mcp: PluginMcp[];
  /** есть хуки или MCP-серверы: они работают только после разрешения человека для этой версии */
  executable: boolean;
  exec_digest: string;
  exec_approved: boolean;
  skipped_links: string[];
  file_count: number;
}

export interface CatalogItem {
  id: string;
  name: string;
  title: string;
  repo: string;
  path: string;
  ref: string;
  commit: string;
  format: string;
  description: string;
  stars: number;
  license: string;
  marketplace: string;
  components: { skills: number; commands: number; agents: number; hooks: boolean; mcp: boolean };
  installed_id: number | null;
  update: boolean;
}

export interface PluginChoice { name: string; path: string; description: string }

interface InstallResult { plugin?: Plugin; installed?: boolean; needs_choice?: boolean; message?: string; choices?: PluginChoice[] }

interface PluginsState {
  items: Plugin[];
  catalog: CatalogItem[];
  checkedAt: string;
  loaded: boolean;
  catalogLoaded: boolean;
  error: string;
  /** что сейчас ставится: id каталога или источник */
  busy: string[];
}

export const pluginsStore = createStore<PluginsState>({ items: [], catalog: [], checkedAt: '', loaded: false, catalogLoaded: false, error: '', busy: [] });
export const usePlugins = <S,>(selector: (state: PluginsState) => S): S => useStore(pluginsStore, selector);

export async function loadPlugins(): Promise<void> {
  try {
    const result = await api<{ plugins: Plugin[] }>('/api/plugins');
    pluginsStore.set({ items: result.plugins, loaded: true, error: '' });
  } catch (error) {
    pluginsStore.set({ loaded: true, error: (error as Error).message || 'Не удалось загрузить плагины' });
  }
}

export async function loadCatalog(): Promise<void> {
  try {
    const result = await api<{ items: CatalogItem[]; checked_at: string }>('/api/plugins/catalog');
    pluginsStore.set({ catalog: result.items, checkedAt: result.checked_at, catalogLoaded: true, error: '' });
  } catch (error) {
    pluginsStore.set({ catalogLoaded: true, error: (error as Error).message || 'Не удалось загрузить каталог' });
  }
}

/** После любой правки плагинов меняется и список навыков: навыки и команды плагинов идут в общий каталог. */
async function refresh(): Promise<void> {
  await Promise.all([loadPlugins(), loadCatalog(), loadSkills(true)]);
}

async function withBusy<T>(key: string, action: () => Promise<T>): Promise<T> {
  pluginsStore.set({ busy: [...pluginsStore.get().busy, key] });
  try {
    return await action();
  } finally {
    pluginsStore.set({ busy: pluginsStore.get().busy.filter((item) => item !== key) });
  }
}

export function installFromCatalog(id: string): Promise<Plugin | undefined> {
  return withBusy(id, async () => {
    const result = await post<InstallResult>('/api/plugins/install', { catalog_id: id });
    await refresh();
    return result.plugin;
  });
}

/** Установка по ссылке: если в репозитории маркетплейс, возвращается список плагинов для выбора. */
export function installFromSource(source: string, path = ''): Promise<InstallResult> {
  return withBusy(`source:${source}:${path}`, async () => {
    const result = await post<InstallResult>('/api/plugins/install', { source, path });
    if (!result.needs_choice) await refresh();
    return result;
  });
}

export async function setPluginEnabled(plugin: Plugin, enabled: boolean): Promise<void> {
  await post(`/api/plugins/${plugin.id}/enabled`, { enabled });
  await refresh();
}

export async function removePlugin(plugin: Plugin): Promise<void> {
  await del(`/api/plugins/${plugin.id}`);
  await refresh();
}

/** Решение по хукам и MCP именно этой версии: digest не совпадёт, если плагин успели обновить. */
export async function decideExec(plugin: Plugin, allow: boolean): Promise<void> {
  await post(`/api/plugins/${plugin.id}/exec`, { digest: plugin.exec_digest, allow });
  await refresh();
}

export async function pluginFile(pluginId: number, path: string): Promise<string> {
  const result = await api<{ text: string }>(`/api/plugins/${pluginId}/file?path=${encodeURIComponent(path)}`);
  return result.text;
}

export function filterCatalog(items: CatalogItem[], query: string): CatalogItem[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return items;
  return items.filter((item) => `${item.title} ${item.name} ${item.repo} ${item.description}`.toLowerCase().includes(needle));
}

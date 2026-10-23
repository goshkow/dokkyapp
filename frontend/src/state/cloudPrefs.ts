import { createStore, useStore } from '../lib/store';
import { data } from './data';

/** Настройки ответов Докки с сервера: как показывать источники, память. Обновляются из окна настроек. */
export type SourcesMode = 'both' | 'inline' | 'end' | 'none';

interface Prefs {
  value: Record<string, unknown> | null;
}

export const cloudPrefs = createStore<Prefs>({ value: null });

export function setCloudPreferences(value: Record<string, unknown>): void {
  const base = cloudPrefs.get().value ?? (data.get().account?.bootstrap?.preferences as Record<string, unknown> | undefined) ?? {};
  cloudPrefs.set({ value: { ...base, ...value } });
}

export function resetCloudPreferences(): void {
  cloudPrefs.set({ value: null });
}

const read = (): SourcesMode => {
  const value = (cloudPrefs.get().value ?? data.get().account?.bootstrap?.preferences ?? {}) as { sources?: SourcesMode };
  return value.sources ?? 'both';
};

export const sourcesMode = read;
export const useSourcesMode = (): SourcesMode => useStore(cloudPrefs, () => read());

import { createStore, useStore } from '../lib/store';

export type SettingsSection = 'general' | 'account' | 'billing' | 'usage' | 'conversation' | 'development' | 'remote' | 'system' | 'updates' | 'about';

interface SettingsUiState { open: boolean; section: SettingsSection; /** План, чья вкладка открывается на экране подписки (из приглашения или подсказки). */ plan: string }

export const settingsUi = createStore<SettingsUiState>({ open: false, section: 'general', plan: '' });
export const useSettingsUi = <S,>(selector: (state: SettingsUiState) => S): S => useStore(settingsUi, selector);

export const openSettings = (section: SettingsSection = 'general') => settingsUi.set({ open: true, section: section === 'usage' ? 'billing' : section, plan: '' });
/** Экран подписки сразу на вкладке нужного плана. */
export const openBilling = (plan = '') => settingsUi.set({ open: true, section: 'billing', plan });
export const closeSettings = () => settingsUi.set({ open: false });

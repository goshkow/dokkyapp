import { call, isMirror } from '../lib/mirror';
import { ui, type UiState } from './ui';

let ready = false;
export const markShellReady = () => { ready = true; };
export const isShellReady = () => ready;

type Persist = (name: 'left' | 'right', open: boolean) => void;
let persist: Persist = () => undefined;
export const setPanelPersistence = (fn: Persist) => { persist = fn; };

/** Запоминает, открыта ли панель. Окно растёт и панель выезжает в `state/shellMotion.ts`, когда панель реально показана. */
export function setPanel(name: 'left' | 'right', open: boolean, save = true): void {
  // вид плитки состоянием панелей не владеет: просит главный вид
  if (isMirror) { void call('main', 'setPanel', [name, open, save]).catch(() => undefined); return; }
  const key: keyof UiState = name === 'left' ? 'leftOpen' : 'rightOpen';
  if (ui.get()[key] === open) return;
  ui.set({ [key]: open } as Partial<UiState>);
  if (save) persist(name, open);
}

export const togglePanel = (name: 'left' | 'right') => setPanel(name, !ui.get()[name === 'left' ? 'leftOpen' : 'rightOpen']);

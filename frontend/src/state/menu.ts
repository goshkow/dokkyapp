import { isReady, lastProject, saveSettings, data } from './data';
import { switchScope } from './actions';
import { guarded } from './overlay';
import { togglePanel } from './panels';
import { signOut } from './session';
import { openBilling, openSettings } from './settingsUi';
import { ui, type Scope } from './ui';
import { openSection, startChat, surface } from './surface';
import { goBack, goForward, openPage, view, type PageKey } from './view';

declare global {
  interface Window {
    /** Команды строки меню macOS и меню значка в трее; вызывает хост окна. */
    koddiMenu?: (action: string) => void;
  }
}

type Page = Exclude<PageKey, 'chat'>;
const SCOPES: Record<string, Scope> = { 'scope-chat': 'chat', 'scope-search': 'search', 'scope-development': 'development' };
/** Разделы режима разработки; «Библиотека» принадлежит режиму чата. */
const DEV_PAGES: Record<string, Page> = {
  'page-projects': 'projects', 'page-mission': 'mission', 'page-automations': 'automations',
  'page-review': 'review', 'page-skills': 'skills', 'page-sources': 'sources', 'page-logs': 'logs',
};

async function goToPage(page: Page, scope: Scope): Promise<void> {
  if (ui.get().scope !== scope) await switchScope(scope);
  openPage(page);
}

async function resizeText(change: number | 'reset'): Promise<void> {
  const current = data.get().settings.text_size || 14;
  const next = change === 'reset' ? 14 : Math.min(20, Math.max(8, current + change));
  if (next !== current) await saveSettings({ text_size: next });
}

async function run(action: string): Promise<void> {
  // без входа доступны только настройки, сведения о программе и размер текста
  const ready = isReady(data.get().account);
  switch (action) {
    case 'about': return openSettings('about');
    case 'settings': return openSettings('general');
    case 'text-bigger': return resizeText(1);
    case 'text-smaller': return resizeText(-1);
    case 'text-reset': return resizeText('reset');
    default:
  }
  if (!ready) return;
  const scope = SCOPES[action];
  if (scope) return switchScope(scope);
  const page = DEV_PAGES[action];
  if (page) return goToPage(page, 'development');
  switch (action) {
    case 'page-library':
      if (surface.get().status === 'off') return goToPage('library', 'chat');
      if (ui.get().scope === 'development') await switchScope('chat');
      return openSection('library');
    case 'billing': return openBilling();
    case 'usage': return openSettings('usage');
    case 'sign-out': return signOut();
    case 'new-chat': return startChat(ui.get().scope === 'development' ? view.get().project ?? lastProject() : null);
    case 'toggle-left': return togglePanel('left');
    case 'toggle-right': return togglePanel('right');
    case 'back': return goBack();
    case 'forward': return goForward();
    default:
  }
}

window.koddiMenu = guarded(run);

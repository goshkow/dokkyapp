/** Вид плитки Liquid Glass: зеркало состояния главного вида и действия через него (lib/mirror.ts). */
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { TileApp } from './components/shell/TileApp';
import { startTileGlass } from './lib/glass';
import { exposeActions, mirrorStore, role } from './lib/mirror';
import { initOverlayScroll } from './lib/overlayScroll';
import { chat } from './state/chatCore';
import { data } from './state/data';
import { fieldHost, fieldMirror, voiceMeter } from './state/field';
import { limitsStore } from './state/limits';
import { plansStore } from './state/plans';
import { applyAppearance, ui } from './state/ui';
import { nav, view } from './state/view';
import { openFile, selectInspector } from './state/workspace';

for (const [name, store] of [['data', data], ['view', view], ['ui', ui], ['limits', limitsStore], ['plans', plansStore], ['nav', nav]] as const) mirrorStore(name, store as never);
mirrorStore('chat', chat);
mirrorStore('fieldMirror', fieldMirror);
mirrorStore('fieldHost', fieldHost);
mirrorStore('voiceMeter', voiceMeter);
// рабочая область (файлы и редактор) принадлежит правой панели: главный вид открывает файлы и карточки через неё
if (role === 'inspector') {
  exposeActions({ openFile, selectInspector });
  // у каждой карточки правой панели своё нативное стекло
  startTileGlass();
}
document.documentElement.classList.add('tile', 'glass', 'native-tiles');
ui.subscribe(() => applyAppearance());
applyAppearance();

createRoot(document.getElementById('root')!).render(<StrictMode><TileApp role={role} /></StrictMode>);
document.body.classList.remove('booting');
initOverlayScroll();

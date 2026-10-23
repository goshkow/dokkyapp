import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { markShellReady } from './state/panels';
import { initOverlayScroll } from './lib/overlayScroll';
import './state/hooks';
import './state/access';
import './state/subscriptionSync';
import './state/menu';
import { initEdgeFade } from './lib/edgeFade';
import { appColor, initGlass } from './lib/glass';
import { announceAppearance, announceOverlay, announceProduct } from './lib/bridge';
import { applySettings, data } from './state/data';
import { exposeStore } from './lib/mirror';
import { limitsStore } from './state/limits';
import { plansStore } from './state/plans';
import './state/remote';
import { nav, view } from './state/view';
import { chat } from './state/chatCore';
import { startFieldMirror } from './state/field';
import { applyAppearance, ui } from './state/ui';

// плитки Liquid Glass (виды sidebar) показывают зеркало этого состояния
for (const [name, store] of [['data', data], ['view', view], ['ui', ui], ['limits', limitsStore], ['plans', plansStore], ['nav', nav]] as const) exposeStore(name, store as never);
// правой панели из состояния чата нужны план, изменения, выбранное изменение, сессия и расход
exposeStore('chat', chat, ['plan', 'changes', 'changeSelection', 'coding', 'usage', 'running', 'stopping']);
startFieldMirror();

applyAppearance();
initGlass();
initEdgeFade();
ui.subscribe(() => applyAppearance());
// заголовок окна: «Докки Code» только в режиме разработки
let lastScope = '';
ui.subscribe(() => {
  const { scope } = ui.get();
  if (scope !== lastScope) {
    lastScope = scope;
    announceProduct(scope);
  }
});
// системная настройка «уменьшить движение» меняется на лету
matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', () => applySettings(data.get().settings));
matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => { if (data.get().settings.theme === 'system') applySettings(data.get().settings); });

// кольцо фокуса только при работе с клавиатуры
document.addEventListener('keydown', (event) => {
  if (['Tab', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) document.documentElement.dataset.input = 'keyboard';
}, true);
document.addEventListener('pointerdown', () => { delete document.documentElement.dataset.input; }, true);

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
document.body.classList.remove('booting');
initOverlayScroll();
setTimeout(markShellReady, 700);

// меню поля располагаются выше нативной плитки; её порядок меняем только для модальных окон
let overlayOpen = false;
const syncOverlay = () => {
  const open = Boolean(document.querySelector('dialog[open]'));
  if (open === overlayOpen) return;
  overlayOpen = open;
  announceOverlay(open);
};
new MutationObserver(syncOverlay).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['open', 'data-state'] });

// тема и цвет фона запоминаются хостом для заставки следующего запуска
const rememberLook = () => announceAppearance(document.documentElement.dataset.theme ?? 'dark', appColor());
const startLook = () => {
  rememberLook();
  new MutationObserver(rememberLook).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-surface'] });
};
if (window.koddiWindowBridge) startLook();
else addEventListener('koddi-bridge', startLook, { once: true });

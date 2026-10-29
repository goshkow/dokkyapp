import { ArrowLeft, ArrowRight, PanelLeft } from 'lucide-react';
import { useEffect, useState } from 'react';
import { startMirror, type Role } from '../../lib/mirror';
import { guarded } from '../../state/overlay';
import { goBack, goForward, togglePanel } from '../../state/remote';
import { canGoBack, canGoForward, useNav } from '../../state/view';
import { DialogHost, ToastHost } from '../ui/DialogHost';
import { TileComposer } from '../chat/ComposerField';
import { TileProduct } from '../chat/SiteSurface';
import { Inspector } from '../inspector/Inspector';
import { Sidebar } from '../sidebar/Sidebar';

/** Содержимое нативной плитки Liquid Glass: левое меню и кнопки навигации, которые в главном виде при открытом меню прячутся под плиткой. */
export function TileApp({ role }: { role: Role }) {
  const [ready, setReady] = useState(false);
  useEffect(() => startMirror(() => setReady(true)), []);
  const back = useNav(canGoBack);
  const forward = useNav(canGoForward);
  if (role === 'composer') return <>{ready && <TileComposer />}<ToastHost /></>;
  if (role === 'product') return <>{ready && <TileProduct />}</>;
  if (role === 'inspector') {
    return (
      <div id="tile-inspector" className="tile-inspector">
        <div className="panel-inner inspector-panel">{ready && <Inspector />}</div>
        <DialogHost />
        <ToastHost />
      </div>
    );
  }
  return (
    <div id="tile-sidebar" className="tile-sidebar">
      <div className="shell-controls shell-controls-left" role="toolbar" aria-label="Навигация">
        <button id="toggle-left" className="shell-button" type="button" title="Главное меню" aria-label="Главное меню" aria-expanded="true" onClick={guarded(() => togglePanel('left'))}>
          <PanelLeft aria-hidden="true" />
        </button>
        <button id="nav-back" className="shell-button" type="button" title="Назад" aria-label="Назад" disabled={!back} onClick={guarded(goBack)}>
          <ArrowLeft aria-hidden="true" />
        </button>
        <button id="nav-forward" className="shell-button" type="button" title="Вперёд" aria-label="Вперёд" disabled={!forward} onClick={guarded(goForward)}>
          <ArrowRight aria-hidden="true" />
        </button>
      </div>
      <div className="panel-inner sidebar-panel">{ready && <Sidebar />}</div>
      <ToastHost />
    </div>
  );
}

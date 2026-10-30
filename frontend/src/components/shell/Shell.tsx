import { ArrowLeft, ArrowRight, PanelLeft, PanelRight } from 'lucide-react';
import type { ReactNode } from 'react';
import { guarded } from '../../state/overlay';
import { togglePanel } from '../../state/panels';
import { useNativeTiles } from '../../lib/glass';
import { PanelResizer } from './PanelResizer';
import { useShellMotion, useShown } from '../../state/shellMotion';
import { useUi } from '../../state/ui';
import { canGoBack, canGoForward, goBack, goForward, useNav, useView } from '../../state/view';

interface ShellProps {
  sidebar: ReactNode;
  main: ReactNode;
  inspector: ReactNode;
}

/** Каркас окна: сайдбар, чат и правая панель в одной сетке; кнопки навигации лежат поверх страницы. */
export function Shell({ sidebar, main, inspector }: ShellProps) {
  const { left: leftOpen, right: rightOpen, rightAvailable } = useShown();
  const scope = useUi((s) => s.scope);
  useShellMotion(leftOpen, rightOpen, scope);
  const page = useView((s) => s.page);
  const back = useNav(canGoBack);
  const forward = useNav(canGoForward);
  // левое меню - нативная плитка стекла со своим видом: здесь остаётся пустая колонка, а кнопки навигации при открытом меню лежат в плитке
  const tiles = useNativeTiles();
  const className = [leftOpen ? '' : 'left-collapsed', rightOpen ? '' : 'right-collapsed', tiles && leftOpen ? 'tile-left' : '', tiles && rightOpen ? 'tile-right' : ''].filter(Boolean).join(' ');
  const mainClass = [page === 'chat' ? 'chat-page' : '', scope === 'search' ? 'ai-search-active' : ''].filter(Boolean).join(' ');
  return (
    <div id="shell" className={className}>
      <div className="shell-controls shell-controls-left" role="toolbar" aria-label="Навигация">
        <button id="toggle-left" className="shell-button" type="button" title="Главное меню" aria-label="Главное меню"
          aria-controls="left" aria-expanded={leftOpen} onClick={() => togglePanel('left')}>
          <PanelLeft aria-hidden="true" />
        </button>
        <button id="nav-back" className="shell-button" type="button" title="Назад" aria-label="Назад" disabled={!back} onClick={guarded(goBack)}>
          <ArrowLeft aria-hidden="true" />
        </button>
        <button id="nav-forward" className="shell-button" type="button" title="Вперёд" aria-label="Вперёд" disabled={!forward} onClick={guarded(goForward)}>
          <ArrowRight aria-hidden="true" />
        </button>
      </div>
      <div className="shell-controls shell-controls-right">
        <button id="toggle-right" className="shell-button" type="button" title="Изменения и файлы" aria-label="Изменения и файлы"
          aria-controls="right" aria-expanded={rightOpen} hidden={!rightAvailable} onClick={() => togglePanel('right')}>
          <PanelRight aria-hidden="true" />
        </button>
      </div>
      <aside id="left" aria-label="Главное меню"><div className="panel-inner sidebar-panel">{tiles ? null : sidebar}</div></aside>
      <main id="main" className={mainClass || undefined}>{main}</main>
      <aside id="right" aria-label="Инспектор проекта"><PanelResizer /><div className="panel-inner inspector-panel">{tiles ? null : inspector}</div></aside>
    </div>
  );
}

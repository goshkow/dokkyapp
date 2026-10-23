import { useState, type UIEvent } from 'react';
import { useLimits } from '../../state/limits';
import { openBilling } from '../../state/remote';
import { useUi } from '../../state/ui';
import { AccountRow } from './AccountRow';
import { ChatLists } from './ChatLists';
import { Nav } from './Nav';

/** Остаток почти закончился: кнопка под плитками, как на сайте, ведёт к сбросу использования. */
function ResetButton() {
  const available = useLimits((state) => state.value?.usageReset?.available === true);
  if (!available) return null;
  return <button type="button" className="side-reset" onClick={() => openBilling()}>Сбросить использование</button>;
}

/** Левое меню по образцу нового меню сайта: название с переключателем «Code», плитки разделов, список чатов и нижний блок с аккаунтом. */
export function Sidebar() {
  const development = useUi((state) => state.scope === 'development');
  const [scrolled, setScrolled] = useState(false);
  const onScroll = (event: UIEvent<HTMLDivElement>) => {
    const next = event.currentTarget.scrollTop > 2;
    if (next !== scrolled) setScrolled(next);
  };
  return (
    <>
      <div className={`side-top${scrolled ? ' is-scrolled' : ''}`}>
        <span className="brand">
          <img className="brand-mark" src="/ui/assets/brand-mark.png" alt="" draggable={false} />
          <span className="brand-word">Докки</span>
          {/* подпись показывает текущий режим; переключает режим плитка в ряду разделов */}
          <span className="mode-badge" data-active={development || undefined}>{development ? 'Code' : 'Чат'}</span>
        </span>
      </div>
      <div className="sidebar-scroll" onScroll={onScroll}>
        <Nav />
        <ResetButton />
        <ChatLists />
      </div>
      <AccountRow />
    </>
  );
}

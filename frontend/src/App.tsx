import { useEffect, useState } from 'react';
import { AuthScreen } from './components/auth/AuthScreen';
import { SourcePopover } from './components/chat/Viewers';
import { MediaViewer } from './components/viewer/MediaViewer';
import { PaymentDialog } from './components/billing/PaymentDialog';
import { Chat } from './components/chat/Chat';
import { SiteSurface } from './components/chat/SiteSurface';
import { Inspector } from './components/inspector/Inspector';
import { SettingsDialog } from './components/settings/SettingsDialog';
import { Shell } from './components/shell/Shell';
import { Sidebar } from './components/sidebar/Sidebar';
import { ContextMenu } from './components/ui/ContextMenu';
import { DialogHost, ToastHost } from './components/ui/DialogHost';
import { bindHostCallbacks } from './state/actions';
import { bootApp, isReady, lastProject, useData } from './state/data';
import { guarded } from './state/overlay';
import { ui, useUi } from './state/ui';
import { restartEvents, startEvents } from './state/events';
import { surfaceShown, useSurface } from './state/surface';
import { newChat, view } from './state/view';

// медленный сайт не держит приложение закрытым: дальше спиннер в самой колонке
const SPLASH_HOLD_MS = 8000;

/** Колонка чата: в режиме чата - страница сайта, в режиме кода (или без поддержки на сервере) - свой чат приложения. */
function Main() {
  const scope = useUi((state) => state.scope);
  const available = useSurface((state) => state.status !== 'off');
  const shown = useSurface((state) => surfaceShown(state, scope));
  return (
    <>
      {!shown && <Chat />}
      {available && <SiteSurface shown={shown} />}
    </>
  );
}

export function App() {
  const booted = useData((state) => state.booted);
  const ready = useData((state) => isReady(state.account));
  const signedIn = useData((state) => state.account?.authenticated === true);
  const scope = useUi((state) => state.scope);
  const siteLoading = useSurface((state) => state.status === 'loading' && surfaceShown(state, scope));
  // заставка держится, пока не загрузится и сайт режима чата: иначе после неё мелькал бы второй спиннер
  const [waited, setWaited] = useState(false);
  useEffect(() => {
    if (!ready || !booted) return;
    const timer = window.setTimeout(() => setWaited(true), SPLASH_HOLD_MS);
    return () => window.clearTimeout(timer);
  }, [ready, booted]);
  const holding = ready && booted && siteLoading && !waited;

  useEffect(() => {
    bindHostCallbacks();
    guarded(async () => {
      await bootApp();
      startEvents();
      // первый экран: новый чат (в разработке - в последнем проекте)
      if (view.get().version === 0) newChat(ui.get().scope === 'development' ? lastProject() : null);
    })();
  }, []);

  useEffect(() => {
    if (signedIn && booted) restartEvents();
  }, [signedIn, booted]);

  return (
    <>
      {ready && <Shell sidebar={<Sidebar />} main={<Main />} inspector={<Inspector />} />}
      {(!ready || !booted || holding) && <AuthScreen pending={!booted || holding} />}
      <SettingsDialog />
      <PaymentDialog />
      <SourcePopover />
      <MediaViewer />
      <ContextMenu />
      <DialogHost />
      <ToastHost />
    </>
  );
}

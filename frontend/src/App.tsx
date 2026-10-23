import { useEffect } from 'react';
import { AuthScreen } from './components/auth/AuthScreen';
import { SourcePopover } from './components/chat/Viewers';
import { MediaViewer } from './components/viewer/MediaViewer';
import { PaymentDialog } from './components/billing/PaymentDialog';
import { Chat } from './components/chat/Chat';
import { Inspector } from './components/inspector/Inspector';
import { SettingsDialog } from './components/settings/SettingsDialog';
import { Shell } from './components/shell/Shell';
import { Sidebar } from './components/sidebar/Sidebar';
import { ContextMenu } from './components/ui/ContextMenu';
import { DialogHost, ToastHost } from './components/ui/DialogHost';
import { bindHostCallbacks } from './state/actions';
import { bootApp, isReady, lastProject, useData } from './state/data';
import { guarded } from './state/overlay';
import { ui } from './state/ui';
import { restartEvents, startEvents } from './state/events';
import { newChat, view } from './state/view';

export function App() {
  const booted = useData((state) => state.booted);
  const ready = useData((state) => isReady(state.account));
  const signedIn = useData((state) => state.account?.authenticated === true);

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
      {ready && <Shell sidebar={<Sidebar />} main={<Chat />} inspector={<Inspector />} />}
      {(!ready || !booted) && <AuthScreen pending={!booted} />}
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

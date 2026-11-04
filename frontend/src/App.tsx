import { useEffect, useState } from 'react';
import { AuthScreen } from './components/auth/AuthScreen';
import { askFirstRunQuestions } from './state/firstRun';
import { SourcePopover } from './components/chat/Viewers';
import { MediaViewer } from './components/viewer/MediaViewer';
import { PaymentDialog } from './components/billing/PaymentDialog';
import { Chat } from './components/chat/Chat';
import { SiteSurface } from './components/chat/SiteSurface';
import { Inspector } from './components/inspector/Inspector';
import { SettingsDialog } from './components/settings/SettingsDialog';
import { Shell } from './components/shell/Shell';
import { Sidebar } from './components/sidebar/Sidebar';
import { ConnectionNotice } from './components/ui/ConnectionNotice';
import { ContextMenu } from './components/ui/ContextMenu';
import { CommandPalette } from './components/shell/CommandPalette';
import { DialogHost, ToastHost } from './components/ui/DialogHost';
import { bindHostCallbacks } from './state/actions';
import { bootApp, data, isReady, lastProject, useData } from './state/data';
import { guarded } from './state/overlay';
import { ui, useUi } from './state/ui';
import { restartEvents, startEvents } from './state/events';
import { surfaceShown, useSurface } from './state/surface';
import { newChat, view } from './state/view';

// медленный сайт не держит приложение закрытым: дальше спиннер в самой колонке, а меню уже со списком
const SPLASH_HOLD_MS = 12_000;

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

const start = guarded(async () => {
  await bootApp();
  if (data.get().bootFailed) return;
  startEvents();
  // первый экран: новый чат (в разработке - в последнем проекте)
  if (view.get().version === 0) newChat(ui.get().scope === 'development' ? lastProject() : null);
});

// пока идёт повтор, на экране заставка, а не кнопка входа
const retry = () => {
  data.set({ booted: false });
  start();
};

export function App() {
  const booted = useData((state) => state.booted);
  const failed = useData((state) => state.bootFailed !== '');
  const ready = useData((state) => isReady(state.account));
  const signedIn = useData((state) => state.account?.authenticated === true);
  const owner = useData((state) => String(state.account?.user?.id ?? ''));
  const scope = useUi((state) => state.scope);
  // пока сервер не сказал, есть ли страница, статус ещё off: без этого между заставкой и страницей мелькала пустая оболочка
  const siteLoading = useSurface((state) => scope !== 'development' && state.status !== 'failed' && (state.checking || (state.status === 'loading' && surfaceShown(state, scope))));
  // заставка держится, пока не загрузится и сайт режима чата: иначе после неё мелькал бы второй спиннер
  const [waited, setWaited] = useState(false);
  useEffect(() => {
    if (!ready || !booted || !signedIn) return;
    const timer = window.setTimeout(() => setWaited(true), SPLASH_HOLD_MS);
    return () => { window.clearTimeout(timer); setWaited(false); };
  }, [ready, booted, signedIn]);
  // заставка закрывается один раз за вход: повторная проверка страницы (вернулась сеть, режим чата) идёт уже в открытом окне
  const [released, setReleased] = useState(false);
  const holding = ready && booted && signedIn && siteLoading && !waited && !released;
  useEffect(() => {
    if (!signedIn) setReleased(false);
    else if (ready && booted && !holding) setReleased(true);
  }, [signedIn, ready, booted, holding]);

  useEffect(() => {
    bindHostCallbacks();
    start();
  }, []);

  useEffect(() => {
    if (signedIn && booted) restartEvents();
    if (signedIn && booted && !failed && owner) void askFirstRunQuestions(owner);
  }, [signedIn, booted, failed, owner]);

  return (
    <>
      {ready && <Shell sidebar={<Sidebar />} main={<Main />} inspector={<Inspector />} />}
      {(!ready || !booted || holding || failed) && <AuthScreen pending={(!booted || holding) && !failed} onRetry={retry} />}
      <SettingsDialog />
      <PaymentDialog />
      <SourcePopover />
      <MediaViewer />
      <ContextMenu />
      <DialogHost />
      <CommandPalette />
      <ToastHost />
      {ready && booted && <ConnectionNotice />}
    </>
  );
}

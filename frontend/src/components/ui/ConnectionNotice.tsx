import { useConnection } from '../../state/connection';
import { surfaceShown, useSurface } from '../../state/surface';
import { useUi } from '../../state/ui';

/** Постоянная подсказка без сети: всплывающее уведомление пропало бы, а связи всё нет. */
export function ConnectionNotice() {
  const online = useConnection();
  // в чате об этом уже говорит экран страницы сайта
  const scope = useUi((state) => state.scope);
  const covered = useSurface((state) => state.status === 'failed' && surfaceShown(state, scope));
  if (online || covered) return null;
  return <div className="connection-notice" role="status">Нет подключения к интернету. Ответы Докки вернутся вместе со связью.</div>;
}

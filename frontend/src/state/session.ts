import { resetCloudPreferences } from './cloudPrefs';
import { logout } from './data';
import { confirmDialog } from './overlay';

/** Выход из аккаунта с подтверждением; `beforeLogout` вызывается после согласия (например, закрыть окно настроек). */
export async function signOut(beforeLogout?: () => void): Promise<void> {
  if (!(await confirmDialog('Выйти из Докки?', 'Работающие задачи будут остановлены. Кэш серверных чатов будет удалён, локальные чаты разработки сохранятся.', 'Выйти'))) return;
  beforeLogout?.();
  await logout();
  resetCloudPreferences();
}

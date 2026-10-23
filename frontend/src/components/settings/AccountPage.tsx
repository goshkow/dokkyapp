import { ExternalLink, LogIn, LogOut, RefreshCw } from 'lucide-react';
import { api, post } from '../../lib/api';
import { openExternal, SITE } from '../../lib/external';
import { data, login, useData } from '../../state/data';
import { signOut } from '../../state/session';
import { useLimits } from '../../state/limits';
import { planName, usePlans } from '../../state/plans';
import { guarded } from '../../state/overlay';
import { useAvatar } from '../sidebar/AccountRow';
import { Action, Row, Section, type Say } from './parts';

export function AccountPage({ say, close }: { say: Say; close: () => void }) {
  const account = useData((s) => s.account);
  const busy = useData((s) => s.loginBusy);
  const plan = useLimits((s) => s.value?.plan);
  const catalog = usePlans((s) => s.catalog);
  const avatar = useAvatar();
  if (!account?.authenticated) {
    return (
      <Section title="Профиль">
        <Row title="Вход в Докки" detail="Войдите, чтобы пользоваться чатами, поиском и режимом разработки.">
          <button type="button" className="pref-button primary" disabled={busy} onClick={guarded(login)}><LogIn aria-hidden="true" /><span>Войти с помощью Докки</span></button>
        </Row>
      </Section>
    );
  }
  const name = account.user?.display_name?.trim() || 'Аккаунт Докки';
  return (
    <>
      <Section title="Профиль">
        <div className="pref-profile">
          <span className="avatar">{avatar ? <img src={avatar} alt="" draggable={false} /> : Array.from(name)[0]?.toLocaleUpperCase()}</span>
          <div>
            <strong>{name}</strong>
            <span>{plan ? `Докки · ${planName(plan, catalog)}` : 'Аккаунт Докки'}</span>
          </div>
        </div>
        <Action title="Имя, фото и вход" detail="Имя, фото, почта и привязка Telegram меняются на сайте Докки." label="Открыть на сайте" icon={<ExternalLink aria-hidden="true" />} onClick={() => openExternal(SITE)} />
      </Section>
      <Section title="Аккаунт">
        <Action
          title="Обновить данные аккаунта"
          detail="Перечитать профиль, доступные режимы и лимиты с сервера."
          label="Обновить"
          icon={<RefreshCw aria-hidden="true" />}
          onClick={guarded(async () => {
            const bootstrap = await post<NonNullable<typeof account>['bootstrap']>('/api/dokky/bootstrap');
            const fresh = await api<NonNullable<typeof account>>('/api/dokky/session');
            data.set({ account: { ...fresh, bootstrap } });
            say('Аккаунт обновлён');
          })}
        />
        <Action
          title="Выйти из аккаунта"
          detail="Работающие задачи остановятся. Серверные чаты Докки, файлы проектов и локальные чаты разработки сохранятся."
          label="Выйти"
          danger
          icon={<LogOut aria-hidden="true" />}
          onClick={guarded(async () => {
            await signOut(close);
          })}
        />
      </Section>
    </>
  );
}

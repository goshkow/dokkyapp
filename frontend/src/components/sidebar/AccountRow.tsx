import { ChartNoAxesColumn, CircleUserRound, CreditCard, Gauge, RefreshCw, Settings, SquarePen } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { cloudPath } from '../../lib/api';
import type { Limits } from '../../lib/types';
import { lastProject, useData } from '../../state/data';
import { useLimits } from '../../state/limits';
import { planName, usePlans } from '../../state/plans';
import { downloadUpdate, loadLimits, newChat, openBilling, openSettings, restartForUpdate } from '../../state/remote';
import { guarded } from '../../state/overlay';
import { useUi } from '../../state/ui';
import { useView } from '../../state/view';
import { hasUpdate, useUpdates } from '../../state/updates';
import { Popover } from '../ui/Popover';

const avatarCache = new Map<string, string>();

export function useAvatar(): string {
  const account = useData((state) => state.account);
  const key = String(account?.user?.id ?? account?.user?.display_name ?? '');
  const [url, setUrl] = useState(avatarCache.get(key) ?? '');
  useEffect(() => {
    if (!account?.authenticated) { setUrl(''); return undefined; }
    if (avatarCache.has(key)) { setUrl(avatarCache.get(key) ?? ''); return undefined; }
    let alive = true;
    void (async () => {
      try {
        const response = await fetch(cloudPath('me/avatar'));
        if (!response.ok) return;
        const blob = await response.blob();
        if (!blob.type.startsWith('image/') || blob.size > 1_500_000) return;
        const value = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result));
          reader.onerror = reject;
          reader.readAsDataURL(blob);
        });
        avatarCache.set(key, value);
        if (alive) setUrl(value);
      } catch {
        /* без аватарки остаётся буква имени */
      }
    })();
    return () => { alive = false; };
  }, [account?.authenticated, key]);
  return url;
}

/** Кольцо остатка: SVG с гладким краем (раньше было маской на conic-gradient с рваным краем). */
function UsageRing({ percent }: { percent: number }) {
  const value = Math.max(0, Math.min(100, percent));
  return (
    <svg className="usage-ring" viewBox="0 0 16 16" aria-hidden="true" data-low={value <= 15 ? true : undefined}>
      <circle className="ring-track" cx="8" cy="8" r="6" pathLength="100" />
      <circle className="ring-value" cx="8" cy="8" r="6" pathLength="100" strokeDasharray={`${value} 100`} />
    </svg>
  );
}

/** Сброс окна всегда со временем (окно 5 ч часто переходит через полночь), подписка - датой. */
export const moment = (value: string | null, withTime = false): string => {
  const date = value ? new Date(value) : null;
  if (!date || !Number.isFinite(date.getTime())) return '';
  const time = date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  if (date.toDateString() === new Date().toDateString()) return time;
  const day = date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
  return withTime ? `${day}, ${time}` : day;
};

function LimitRows({ limits, loading, error }: { limits: Limits | null; loading: boolean; error: boolean }) {
  const rows: [string, number | null | undefined, string | null | undefined, boolean][] = [
    [`${limits?.windowHours ?? 5} ч`, limits?.windowRemaining, limits?.windowResetsAt, true],
    ['Подписка', limits?.remaining, limits?.expiresAt, false],
  ];
  return (
    <div className="limit-list">
      {rows.map(([name, percent, at, withTime]) => {
        const known = percent != null;
        return (
          <div key={name} className="limit-row" data-low={known && percent <= 15 ? true : undefined}>
            <strong>{name}</strong>
            <span className="limit-value">{known ? `${percent}%` : loading ? '…' : error ? 'нет данных' : '—'}</span>
            <span className="limit-time">{known && at ? moment(at, withTime) : ''}</span>
            <i className="limit-bar"><b style={{ width: `${known ? percent : 0}%` }} /></i>
          </div>
        );
      })}
    </div>
  );
}

function UpdateNotice() {
  const state = useUpdates((value) => value);
  if (!state?.available || !['available', 'downloading', 'preparing', 'ready', 'installing', 'error'].includes(state.status)) return null;
  const automatic = ['bundle', 'portable', 'setup', 'setup_elevated'].includes(state.method);
  const working = ['downloading', 'preparing', 'installing'].includes(state.status);
  const progress = state.progress ? Math.min(100, Math.round(state.progress.done / Math.max(1, state.progress.total) * 100)) : null;
  const label = state.status === 'ready' ? 'Обновление готово' : state.status === 'downloading' ? `Загрузка${progress === null ? '…' : ` · ${progress}%`}` : state.status === 'preparing' ? 'Подготовка…' : state.status === 'installing' ? 'Перезапуск…' : state.status === 'error' ? 'Не удалось обновить' : `Докки ${state.available.version}`;
  return (
    <div className="side-update" aria-live="polite">
      <div className="side-update-text"><strong>{label}</strong><span>{state.status === 'error' ? state.error : state.status === 'ready' ? `Версия ${state.available.version}` : 'Обновление Докки'}</span></div>
      {working ? <RefreshCw className="side-update-spinner" aria-hidden="true" /> : <button type="button" onClick={guarded(state.status === 'ready' ? restartForUpdate : automatic ? downloadUpdate : () => openSettings('updates'))}>{state.status === 'ready' ? 'Перезапустить' : state.status === 'error' ? 'Повторить' : 'Обновить'}</button>}
      {state.status === 'downloading' && progress !== null && <div className="side-update-progress" role="progressbar" aria-label="Загрузка обновления" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><i style={{ width: `${progress}%` }} /></div>}
    </div>
  );
}

/** Нижний блок меню как на сайте: аватар с именем открывают настройки, строка «лимит» - лимиты и аккаунт, круглая кнопка - новый чат. */
export function AccountRow() {
  const account = useData((state) => state.account);
  const name = account?.user?.display_name?.trim() || (account?.authenticated ? 'Докки' : 'Войти в Докки');
  const avatar = useAvatar();
  const limits = useLimits((state) => state.value);
  const loading = useLimits((state) => state.loading);
  const error = useLimits((state) => state.error);
  const catalog = usePlans((state) => state.catalog);
  const development = useUi((state) => state.scope === 'development');
  const project = useView((state) => state.project);
  const [open, setOpen] = useState(false);
  const update = useUpdates((state) => (hasUpdate(state) ? state : null));
  const toggle = useRef<HTMLButtonElement>(null);
  const letter = Array.from(name)[0]?.toLocaleUpperCase() ?? 'Д';
  // в разработке важнее более строгий из двух лимитов, в чате остаток подписки, как на сайте
  const both = [limits?.windowRemaining, limits?.remaining].filter((value): value is number => value != null);
  const left = development && both.length ? Math.min(...both) : limits?.remaining ?? limits?.windowRemaining ?? null;
  const plan = limits?.plan ?? null;
  const free = plan === 'free';
  return (
    <footer className="side-bottom">
      <UpdateNotice />
      <div className="side-profile">
        <div className="side-id">
          <button id="connection-badge" type="button" className="side-account" data-state={account?.authenticated ? 'ready' : 'offline'} aria-label="Открыть настройки" onClick={() => openSettings('general')}>
            <span className="avatar">{avatar ? <img src={avatar} alt="" draggable={false} /> : letter}</span>
            {update && <i className="update-dot" data-state={update.status} role="img" aria-label="Доступно обновление" />}
            <span className="account-name"><b>{name}</b></span>
          </button>
          <button
            id="account-menu-toggle"
            ref={toggle}
            type="button"
            className="side-plan"
            title="Лимиты и аккаунт"
            aria-label="Лимиты и аккаунт"
            aria-haspopup="menu"
            aria-expanded={open}
            onClick={() => { setOpen((state) => !state); if (!open) void loadLimits(); }}
          >
            {!free && left != null && <UsageRing percent={left} />}
            {plan && <span className="plan-badge" data-plan={plan}>{planName(plan, catalog)}</span>}
            {!free && plan && <span className="plan-sep" aria-hidden="true">·</span>}
            {!free && <span className="usage-percent">{left != null ? `${left}%` : loading ? '…' : '—'}</span>}
          </button>
        </div>
        <button id="new-chat" type="button" className="new-chat" aria-label="Новый чат" title="Новый чат" onClick={() => newChat(development ? project ?? lastProject() : null)}>
          <SquarePen aria-hidden="true" />
        </button>
      </div>
      <Popover open={open} anchor={toggle} onClose={() => setOpen(false)} label="Аккаунт" side="above" className="account-menu">
        <strong className="account-menu-name">{account?.user?.display_name || 'Аккаунт Докки'}</strong>
        {update && (
          <button type="button" role="menuitem" className="menu-item update-item" onClick={() => { setOpen(false); openSettings('updates'); }}>
            <RefreshCw aria-hidden="true" /><span>{update.status === 'ready' ? `Обновить до ${update.available?.version}` : `Доступна версия ${update.available?.version}`}</span>
          </button>
        )}
        <section className="account-limits" aria-label="Оставшийся лимит">
          <h3><Gauge aria-hidden="true" /><span>Оставшийся лимит</span></h3>
          <LimitRows limits={limits} loading={loading} error={error} />
        </section>
        {limits?.usageReset?.available && (
          <button type="button" role="menuitem" className="menu-item" onClick={() => { setOpen(false); openBilling(); }}>
            <Gauge aria-hidden="true" /><span>Сбросить использование</span>
          </button>
        )}
        {([['Аккаунт', CircleUserRound, 'account'], ['Подписка и оплата', CreditCard, 'billing'], ['Настройки', Settings, 'general'], ['Использование', ChartNoAxesColumn, 'usage']] as const).map(([title, Icon, section]) => (
          <button key={section} type="button" role="menuitem" className="menu-item" onClick={() => { setOpen(false); openSettings(section); }}>
            <Icon aria-hidden="true" /><span>{title}</span>
          </button>
        ))}
      </Popover>
    </footer>
  );
}

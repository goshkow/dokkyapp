import { ExternalLink, RefreshCw } from 'lucide-react';
import { useEffect } from 'react';
import { numberText } from '../../lib/format';
import { openExternal, SITE } from '../../lib/external';
import { useChat } from '../../state/chatCore';
import { loadLimits, useLimits } from '../../state/limits';
import { loadPlans, planName, resetFromCatalog, usePlans } from '../../state/plans';
import { useSettingsUi } from '../../state/settingsUi';
import { PlanName } from '../billing/planUi';
import { PlansView } from '../billing/PlansView';
import { UsageResetPanel } from '../billing/UsageResetPanel';
import { Action, Meter, Row, Section, type Say } from './parts';

const date = (value?: string | null, time = false): string => {
  const parsed = value ? new Date(value) : null;
  return parsed && Number.isFinite(parsed.getTime()) ? parsed.toLocaleString('ru-RU', { day: 'numeric', month: 'long', ...(time ? { hour: '2-digit', minute: '2-digit' } as const : { year: 'numeric' } as const) }) : '';
};

function UsageSummary() {
  const limits = useLimits((state) => state.value);
  const loading = useLimits((state) => state.loading);
  const error = useLimits((state) => state.error);
  const cached = useChat((state) => (state.usage?.dokky?.usage ?? state.coding?.last_usage?.dokky?.usage)?.cached_tokens);
  // без данных и без загрузки (безлимит, вход не выполнен) вечный скелет только сбивает с толку
  if (!limits && !loading && !error) return null;
  if (!limits) return <Section>
    {error ? <Row title="Не удалось загрузить использование"><button type="button" className="pref-button" onClick={() => void loadLimits()}>Повторить</button></Row>
      : <div className="settings-loading skeleton" aria-label="Загрузка использования" />}
  </Section>;
  return <div className="billing-usage" aria-label="Использование подписки">
    <div className="billing-limit" data-setting="Лимит разработки 5 часов">
      <div><strong>Разработка · {limits.windowHours} ч</strong><span>{limits.windowRemaining == null ? 'Нет данных' : `Осталось ${limits.windowRemaining}%`}</span></div>
      <Meter percent={limits.windowRemaining} low={limits.windowRemaining != null && limits.windowRemaining <= 15} />
      {limits.windowResetsAt && <small>Освободится {date(limits.windowResetsAt, true)}</small>}
    </div>
    <div className="billing-limit" data-setting="Общий остаток использования">
      <div><strong>Общее использование</strong><span>{limits.remaining == null ? 'Нет данных' : `Осталось ${limits.remaining}%`}</span></div>
      <Meter percent={limits.remaining} low={limits.remaining != null && limits.remaining <= 15} />
    </div>
    {limits.developer.message && <p className="pref-hint">{limits.developer.message}</p>}
    {Number.isSafeInteger(cached) && (cached as number) >= 0 && <details className="billing-cache"><summary>Кэш текущего диалога</summary><p>Прочитано токенов из кэша: {numberText(cached as number)}</p></details>}
  </div>;
}

export function BillingPage({ say }: { say: Say }) {
  const catalog = usePlans((state) => state.catalog);
  const loading = usePlans((state) => state.loading);
  const error = usePlans((state) => state.error);
  const remaining = useLimits((state) => state.value?.remaining ?? null);
  const hint = useSettingsUi((state) => state.plan);
  // цены и действия персональные; остаток обновляется независимо от каталога
  useEffect(() => { void loadPlans(); void loadLimits(); }, []);
  const current = catalog?.current;
  const id = current?.plan ?? '';
  const name = current?.plan_name || planName(id, catalog);
  const left = remaining ?? current?.remaining_percent ?? null;
  const reset = resetFromCatalog(catalog);
  const note = current?.unlimited ? 'Без ограничений' : current?.trial ? 'Пробный период' : current?.expires_at && current.paid_active ? `Действует до ${date(current.expires_at)}` : id === 'free' ? 'Бесплатный тариф' : undefined;
  return <>
    {current && <div className="billing-current"><div>{name && <PlanName id={id} name={name} />}{note && <span>{note}</span>}</div></div>}
    <UsageSummary />
    {reset?.available && catalog && <Section><UsageResetPanel reset={reset} remaining={left} currency={catalog.currency} /></Section>}
    {catalog ? <Section><PlansView key={`${catalog.catalog_version}:${id}:${hint}`} catalog={catalog} initial={hint || undefined} /></Section>
      : loading || !error ? <div className="settings-loading skeleton" aria-label="Загрузка планов" />
      : <Section><Row title="Не удалось загрузить планы" detail={error}><button type="button" className="pref-button" onClick={() => void loadPlans()}><RefreshCw aria-hidden="true" /><span>Повторить</span></button></Row></Section>}
    <Section title="Оплата и управление" hint="Оплата, автопродление, возвраты и промокоды доступны на сайте Докки.">
      <Action title="Управление подпиской" label="Открыть сайт" icon={<ExternalLink aria-hidden="true" />} onClick={() => { if (!openExternal(SITE)) say('Не удалось открыть сайт'); }} />
    </Section>
  </>;
}

import { useEffect, useId, useState } from 'react';
import { guarded } from '../../state/overlay';
import { startPayment } from '../../state/payment';
import { money, planButton, planName, type Catalog, type Plan } from '../../state/plans';
import { FeatureList, PlanName, Price } from './planUi';

const day = (value?: string | null): string => {
  const date = value ? new Date(value) : null;
  return date && Number.isFinite(date.getTime()) ? date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }) : '';
};

/** Экран планов как на сайте: заголовок, «Ваш план», вкладки, карточка выбранного плана и сноски. */
export function PlansView({ catalog, initial }: { catalog: Catalog; initial?: string }) {
  const startIndex = () => {
    const wanted = initial ? catalog.plans.findIndex((plan) => plan.id === initial) : -1;
    if (wanted >= 0) return wanted;
    const current = catalog.plans.findIndex((plan) => plan.action === 'current');
    if (current >= 0) return current;
    return Math.max(0, catalog.plans.findIndex((plan) => plan.action === 'upgrade' || plan.action === 'subscribe'));
  };
  const tabsId = useId();
  const [selected, setSelected] = useState(startIndex);
  // каталог обновился (смена плана, сокращение списка): выбранная вкладка не уходит за его пределы
  useEffect(() => { setSelected((value) => Math.min(value, catalog.plans.length - 1)); }, [catalog.plans.length]);
  const plan: Plan = catalog.plans[selected] ?? (catalog.plans[0] as Plan);
  const currentId = catalog.current.plan ?? '';
  const currentName = catalog.current.plan_name || planName(currentId, catalog);
  const button = planButton(plan, { expiresAt: catalog.current.expires_at, name: currentName });
  const blocked = button.disabled || !catalog.payment_available;
  const pay = plan.checkout_price ?? null;
  const renewal = catalog.current.renewal;
  const renewalLine = plan.action === 'current' && renewal?.available
    ? renewal.enabled && renewal.next_charge_at ? `Следующее списание: ${day(renewal.next_charge_at)}` : catalog.current.expires_at ? `Активно до ${day(catalog.current.expires_at)}` : ''
    : '';
  return (
    <div className="plans-view" data-testid="plans-view">
      <div className="plans-head">
        <h3>Откройте возможности Докки</h3>
        <p>Ваш план: {currentName ? <PlanName id={currentId} name={currentName} /> : '—'}</p>
      </div>
      <div className="plan-tabs" role="tablist" aria-label="Планы">
        {catalog.plans.map((item, index) => (
          <button key={item.id} type="button" role="tab" id={`${tabsId}-tab-${index}`} aria-controls={`${tabsId}-panel`} tabIndex={index === selected ? 0 : -1} aria-selected={index === selected} data-plan-tab={item.id} onClick={() => setSelected(index)} onKeyDown={(event) => {
            const next = event.key === 'ArrowRight' ? (index + 1) % catalog.plans.length : event.key === 'ArrowLeft' ? (index + catalog.plans.length - 1) % catalog.plans.length : event.key === 'Home' ? 0 : event.key === 'End' ? catalog.plans.length - 1 : null;
            if (next == null) return;
            event.preventDefault();
            setSelected(next);
            document.getElementById(`${tabsId}-tab-${next}`)?.focus();
          }}>
            <PlanName id={item.id} name={item.name} />
          </button>
        ))}
      </div>
      <article key={plan.id} id={`${tabsId}-panel`} aria-labelledby={`${tabsId}-tab-${selected}`} tabIndex={0} className="plan-detail" role="tabpanel" data-plan={plan.id}>
        <h4><PlanName id={plan.id} name={plan.name} /></h4>
        {plan.tagline && <p className="plan-tagline">{plan.tagline}</p>}
        <Price plan={plan} months={catalog.period_months} />
        <div className="plan-cta">
          <button
            type="button"
            className="pref-button primary plan-buy"
            disabled={blocked}
            onClick={guarded(() => startPayment({ intent: 'plan', plan: plan.id, planName: plan.name }))}
          >
            {button.label}
            {!blocked && pay && <span className="plan-buy-price">{money(pay.amount, pay.currency)}</span>}
          </button>
          {!button.disabled && !catalog.payment_available && <p className="plan-note">Оплата временно недоступна, попробуйте позже.</p>}
          {!button.disabled && pay && plan.action === 'upgrade' && <p className="plan-note">К оплате сейчас - доплата за переход на {plan.name}.</p>}
          {renewalLine && <p className="plan-note">{renewalLine}</p>}
        </div>
        <FeatureList plan={plan} heading={catalog.included_heading} />
      </article>
      <p className="plan-foot">Возможности и лимиты зависят от выбранного плана.</p>
      <p className="plan-foot">Цены указаны с учётом комиссии сервиса Докки: отдельно она не начисляется.</p>
    </div>
  );
}

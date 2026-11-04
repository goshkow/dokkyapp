import { Check } from 'lucide-react';
import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import { reducedMotion } from '../../lib/motion';
import { guarded } from '../../state/overlay';
import { startPayment } from '../../state/payment';
import { money, planButton, planName, type Catalog, type Plan } from '../../state/plans';
import { FeatureList, PlanHero, PlanName } from './planUi';

const day = (value?: string | null): string => {
  const date = value ? new Date(value) : null;
  return date && Number.isFinite(date.getTime()) ? date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }) : '';
};

// те же время и кривая, что у переключателя планов на сайте
const SLIDE = { duration: 300, easing: 'cubic-bezier(.25,.75,.25,1)' };

function PlanCard({ catalog, plan, active }: { catalog: Catalog; plan: Plan; active: boolean }) {
  const currentId = catalog.current.plan ?? '';
  const currentName = catalog.current.plan_name || planName(currentId, catalog);
  const button = planButton(plan, { expiresAt: catalog.current.expires_at, name: currentName, unlimited: catalog.current.unlimited === true });
  const blocked = button.disabled || !catalog.payment_available;
  const owned = plan.action === 'current' || plan.action === 'included';
  const pay = plan.checkout_price ?? null;
  const renewal = catalog.current.renewal;
  const renewalLine = plan.action === 'current' && renewal?.available
    ? renewal.enabled && renewal.next_charge_at ? `Следующее списание: ${day(renewal.next_charge_at)}` : catalog.current.expires_at ? `Активно до ${day(catalog.current.expires_at)}` : ''
    : '';
  return (
    <>
      {plan.tagline && <p className="plan-tagline">{plan.tagline}</p>}
      <div className="plan-cta">
        <button
          type="button"
          className={`plan-buy${owned ? ' is-owned' : ''}`}
          disabled={blocked}
          tabIndex={active ? 0 : -1}
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
    </>
  );
}

/** Экран планов как на сайте: ползунок вкладок, карточки перелистываются в сторону выбранного плана. */
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
  const tabs = useRef<HTMLDivElement>(null);
  const indicator = useRef<HTMLSpanElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const panels = useRef<Array<HTMLElement | null>>([]);
  const heroStage = useRef<HTMLDivElement>(null);
  const heroes = useRef<Array<HTMLElement | null>>([]);
  const shown = useRef(-1);
  const running = useRef<Animation[]>([]);
  // каталог обновился (смена плана, сокращение списка): выбранная вкладка не уходит за его пределы
  useEffect(() => { setSelected((value) => Math.min(value, catalog.plans.length - 1)); }, [catalog.plans.length]);
  const currentId = catalog.current.plan ?? '';
  const currentName = catalog.current.plan_name || planName(currentId, catalog);

  const place = (instant: boolean) => {
    const tab = tabs.current?.querySelectorAll<HTMLElement>('[role="tab"]')[selected];
    const bar = indicator.current;
    const first = tabs.current?.querySelector<HTMLElement>('[role="tab"]');
    if (tab && first && bar) {
      if (instant) bar.style.transition = 'none';
      // линейка заполняется от первого плана до выбранного
      const start = first.offsetLeft + first.offsetWidth / 2;
      bar.style.left = `${start}px`;
      bar.style.width = `${tab.offsetLeft + tab.offsetWidth / 2 - start}px`;
      bar.style.transform = '';
      if (instant) requestAnimationFrame(() => { bar.style.transition = ''; });
    }
    const panel = panels.current[selected];
    if (panel && stage.current) stage.current.style.height = `${panel.offsetHeight}px`;
    const hero = heroes.current[selected];
    if (hero && heroStage.current) heroStage.current.style.height = `${hero.offsetHeight}px`;
  };

  useLayoutEffect(() => {
    const previous = shown.current;
    const initialRender = previous < 0;
    shown.current = selected;
    place(initialRender);
    running.current.forEach((animation) => animation.cancel());
    running.current = [];
    const rows = [panels.current, heroes.current];
    rows.forEach((row) => row.forEach((panel) => panel?.classList.remove('leaving', 'entering')));
    const next = panels.current[selected];
    const old = previous >= 0 ? panels.current[previous] : null;
    if (initialRender || !old || !next || old === next || typeof next.animate !== 'function' || reducedMotion()) return;
    // как на сайте: цена с названием и карточка уезжают вместе, линейка планов стоит
    const direction = selected > previous ? 1 : -1;
    const distance = (stage.current?.clientWidth ?? 460) + 24;
    const timing: KeyframeAnimationOptions = { ...SLIDE, fill: 'both' };
    const animations: Animation[] = [];
    const moved: HTMLElement[] = [];
    for (const row of rows) {
      const leaving = row[previous];
      const entering = row[selected];
      if (!leaving || !entering) continue;
      leaving.classList.add('leaving');
      entering.classList.add('entering');
      moved.push(leaving, entering);
      animations.push(
        leaving.animate([{ transform: 'translate3d(0,0,0)' }, { transform: `translate3d(${-direction * distance}px,0,0)` }], timing),
        entering.animate([{ transform: `translate3d(${direction * distance}px,0,0)` }, { transform: 'translate3d(0,0,0)' }], timing),
      );
    }
    running.current = animations;
    void Promise.allSettled(animations.map((animation) => animation.finished)).then(() => {
      if (running.current !== animations) return;
      moved.forEach((node) => node.classList.remove('leaving', 'entering'));
      animations.forEach((animation) => animation.cancel());
      running.current = [];
    });
  }, [selected, catalog]);

  // описание функции раскрывается, окно меняет ширину: высота сцены и ползунок следуют за содержимым
  useEffect(() => {
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(() => place(false));
    const panel = panels.current[selected];
    if (panel) observer.observe(panel);
    if (tabs.current) observer.observe(tabs.current);
    return () => observer.disconnect();
  });

  return (
    <div className="plans-view" data-testid="plans-view">
      <div className="plan-hero-stage" ref={heroStage}>
        {catalog.plans.map((plan, index) => (
          <div
            key={plan.id}
            ref={(node) => { heroes.current[index] = node; }}
            className={`plan-hero${index === selected ? ' active' : ''}`}
            aria-hidden={index !== selected}
          >
            <PlanHero plan={plan} months={catalog.period_months} />
          </div>
        ))}
      </div>
      <div className="plan-tabs" role="tablist" aria-label="Планы" ref={tabs} style={{ '--plans': catalog.plans.length } as CSSProperties}>
        <span className="plan-tab-indicator" ref={indicator} aria-hidden="true" />
        {catalog.plans.map((item, index) => (
          <button key={item.id} type="button" role="tab" id={`${tabsId}-tab-${index}`} aria-controls={`${tabsId}-panel-${index}`} tabIndex={index === selected ? 0 : -1} aria-selected={index === selected} data-plan-tab={item.id} onClick={() => setSelected(index)} onKeyDown={(event) => {
            const next = event.key === 'ArrowRight' ? (index + 1) % catalog.plans.length : event.key === 'ArrowLeft' ? (index + catalog.plans.length - 1) % catalog.plans.length : event.key === 'Home' ? 0 : event.key === 'End' ? catalog.plans.length - 1 : null;
            if (next == null) return;
            event.preventDefault();
            setSelected(next);
            document.getElementById(`${tabsId}-tab-${next}`)?.focus();
          }}>
            <i className="plan-dot" data-done={index < selected || undefined} aria-hidden="true"><Check /></i>
            <PlanName id={item.id} name={item.name} />
            {item.usage_multiplier != null && <small>×{item.usage_multiplier}</small>}
          </button>
        ))}
      </div>
      <div className="plan-stage" ref={stage}>
        {catalog.plans.map((plan, index) => {
          const active = index === selected;
          const owned = plan.action === 'current' || plan.action === 'included';
          return (
            <article
              key={plan.id}
              ref={(node) => { panels.current[index] = node; }}
              id={`${tabsId}-panel-${index}`}
              aria-labelledby={`${tabsId}-tab-${index}`}
              aria-hidden={!active}
              inert={!active}
              tabIndex={active ? 0 : -1}
              className={`plan-detail${active ? ' active' : ''}${owned ? ' owned-plan' : ''}`}
              role="tabpanel"
              data-plan={plan.id}
            >
              <PlanCard catalog={catalog} plan={plan} active={active} />
            </article>
          );
        })}
      </div>
      <p className="plan-foot">{currentName ? <>Ваш план: <PlanName id={currentId} name={currentName} />. </> : null}Возможности и лимиты зависят от выбранного плана.</p>
      <p className="plan-foot">Цены указаны с учётом комиссии сервиса Докки: отдельно она не начисляется.</p>
    </div>
  );
}

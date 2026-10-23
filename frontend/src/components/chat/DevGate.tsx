import { Lock } from 'lucide-react';
import { useEffect } from 'react';
import { guarded } from '../../state/overlay';
import { useView } from '../../state/view';
import { startPayment } from '../../state/payment';
import { developerPlan, loadPlans, money, usePlans } from '../../state/plans';
import { openBilling } from '../../state/settingsUi';
import { PlanIconSvg } from '../billing/planUi';

/** Что предложить тому, у кого Докки Code закрыт тарифом: название плана и цена берутся из каталога сервера. */
export function useGateOffer() {
  const catalog = usePlans((state) => state.catalog);
  useEffect(() => { if (!catalog) void loadPlans(); }, [catalog]);
  const target = developerPlan(catalog);
  const plan = target.plan;
  const name = target.name || 'Plus';
  const buyable = Boolean(plan && catalog?.payment_available && (plan.action === 'subscribe' || plan.action === 'upgrade'));
  const price = plan?.checkout_price ?? plan?.price ?? null;
  const label = `Перейти на ${name}${buyable && price ? ` - ${money(price.amount, price.currency)}` : ''}`;
  const points = (plan?.features ?? []).filter((feature) => !feature.from_previous_plan).slice(0, 4);
  const upgrade = () => (buyable && plan ? startPayment({ intent: 'plan', plan: plan.id, planName: plan.name }) : openBilling(target.id));
  return { name, label, points, upgrade, compare: () => openBilling(target.id) };
}

/** Экран вместо поля ввода Докки Code для Free, пробного периода и Base. */
export function DevGateCard() {
  const offer = useGateOffer();
  return (
    <div className="dev-gate" role="region" aria-label="Докки Code недоступен на вашем тарифе" data-testid="dev-gate">
      <span className="dev-gate-icon" aria-hidden="true"><Lock /></span>
      <h2>Докки Code включается с подпиской {offer.name}</h2>
      <p>Пишите и правьте код вместе с Докки: она сама читает проект, запускает команды и доводит задачу до результата.</p>
      {offer.points.length > 0 && (
        <ul className="dev-gate-points" aria-label={`Что даёт ${offer.name}`}>
          {offer.points.map((feature) => <li key={feature.id}><PlanIconSvg feature={feature} /><span>{feature.label}</span></li>)}
        </ul>
      )}
      <div className="dev-gate-actions">
        <button type="button" className="pref-button primary" onClick={guarded(offer.upgrade)}>{offer.label}</button>
        <button type="button" className="pref-button" onClick={offer.compare}>Сравнить планы</button>
      </div>
      <p className="dev-gate-note">Чаты, которые уже есть, остаются на месте: их можно читать, но писать в них можно только с подпиской. Остальное приложение работает на любом плане.</p>
    </div>
  );
}

/** Вместо поля ввода у закрытого Докки Code: читать чат можно, писать нельзя. */
export function LockedComposer() {
  const offer = useGateOffer();
  // в новом чате кнопки уже есть в приглашении над полем: здесь остаётся только пометка
  const opened = useView((state) => state.chat !== null);
  return (
    <section id="composer" aria-label="Запись недоступна" className="locked" data-product-scope="development" data-testid="locked-composer">
      <div className="locked-composer" role="status">
        <span className="locked-icon" aria-hidden="true"><Lock /></span>
        <div className="locked-text">
          <strong>Писать агенту можно с подпиской {offer.name}</strong>
          <span>Чат доступен для чтения. Обновитесь до {offer.name}, чтобы продолжить работу над проектом.</span>
        </div>
        {opened && (
          <div className="locked-actions">
            <button type="button" className="pref-button primary" onClick={guarded(offer.upgrade)}>{offer.label}</button>
            <button type="button" className="pref-button" onClick={offer.compare}>Сравнить планы</button>
          </div>
        )}
      </div>
    </section>
  );
}

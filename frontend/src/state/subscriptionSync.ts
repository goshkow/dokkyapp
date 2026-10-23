import { refreshAccount } from './data';
import { onLimitsLoaded } from './limits';
import { notify } from './overlay';
import { payment } from './payment';
import { loadPlans, planName, plansStore } from './plans';

/* Подписка меняется и вне приложения (оплата на сайте, окончание срока, бонус тестера): каждое свежее чтение
   `/subscription` (раз в минуту, при возврате в окно и по событиям сервера) сверяется с прежним. */
onLimitsLoaded((previous, next) => {
  const catalog = plansStore.get().catalog;
  // каталог планов нужен сразу для названий плана в меню; сменился его хеш - читается заново
  if (!catalog || (next.catalogVersion && catalog.catalog_version !== next.catalogVersion)) void loadPlans();
  if (!previous) return;
  const changed = previous.plan !== next.plan || previous.paidActive !== next.paidActive || previous.developer.allowed !== next.developer.allowed;
  if (!changed) return;
  void refreshAccount().catch(() => undefined);
  void loadPlans();
  // во время оплаты в приложении итог покажет экран ожидания, дублировать его не нужно
  if (previous.plan !== next.plan && !payment.get().open) notify(`Подписка обновлена: ${planName(next.plan)}`);
});

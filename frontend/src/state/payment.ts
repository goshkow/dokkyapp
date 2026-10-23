import { ApiError, api, cloudPath, post } from '../lib/api';
import { openExternal } from '../lib/external';
import { createStore, useStore } from '../lib/store';
import { refreshAccount } from './data';
import { loadLimits, limitsStore } from './limits';
import { notify } from './overlay';
import { loadPlans, planName, plansStore } from './plans';

/* Оплата проходит только на сайте Докки. Приложение просит у сервера персональную ссылку (`POST /billing/links`),
   открывает её в системном браузере и ждёт результата опросом `GET /billing/links/{id}`; деньги и платёжные данные
   через приложение не идут. Ссылка живёт 30 минут, привязана к аккаунту и хранится только в памяти, пока открыт экран ожидания. */

export type PaymentRequest = { intent: 'plan'; plan: string; planName: string } | { intent: 'topup'; percent?: number };
export type PaymentStage = 'creating' | 'waiting' | 'paid' | 'canceled' | 'expired' | 'failed';

export interface PaymentState {
  open: boolean;
  stage: PaymentStage;
  request: PaymentRequest | null;
  /** Заголовок и пояснение для экрана. */
  title: string;
  text: string;
  /** Предложить вторым действием посмотреть планы (нет платного плана и т.п.). */
  offerPlans: boolean;
  /** Повтор имеет смысл: сбой связи, частые запросы, оплата недоступна; «план уже включён» повторять незачем. */
  canRetry: boolean;
}

const idle: PaymentState = { open: false, stage: 'creating', request: null, title: '', text: '', offerPlans: false, canRetry: true };
export const payment = createStore<PaymentState>(idle);
export const usePayment = <S,>(selector: (state: PaymentState) => S): S => useStore(payment, selector);

const POLL_MS = 2500;
const WAIT_MS = 15 * 60 * 1000;
let attempt = 0;
let timer = 0;
let link: { id: string; url: string; deadline: number } | null = null;

/** Что сказать человеку про отказ сервера в ссылке на оплату. */
export function paymentFailure(error: unknown): { title: string; text: string; offerPlans: boolean; canRetry: boolean } {
  const failure = error instanceof ApiError ? error : null;
  const reason = failure?.reason ?? '';
  if (reason === 'already_included') return { title: 'План уже включён', text: 'Этот план уже входит в вашу подписку.', offerPlans: false, canRetry: false };
  if (reason === 'usage_above_threshold') return { title: 'Пока хватает использования', text: 'Сбросить использование можно, когда оно почти закончится.', offerPlans: false, canRetry: false };
  if (reason === 'no_paid_plan') return { title: 'Нужна платная подписка', text: 'Сброс использования доступен с платной подпиской. Выберите план.', offerPlans: true, canRetry: false };
  if (reason === 'not_applicable') return { title: 'Оплата не нужна', text: 'Для этого аккаунта оплата не требуется.', offerPlans: false, canRetry: false };
  if (failure?.status === 429) {
    const wait = failure.retryAfter ? ` Повторите через ${failure.retryAfter} с.` : ' Повторите чуть позже.';
    return { title: 'Слишком часто', text: `Ссылки на оплату можно запрашивать не чаще десяти раз в минуту.${wait}`, offerPlans: false, canRetry: true };
  }
  if (failure?.status === 503 || failure?.code === 'upstream_unavailable') return { title: 'Оплата временно недоступна', text: 'Попробуйте позже: сервер пока не принимает оплату.', offerPlans: false, canRetry: true };
  return { title: 'Не удалось подготовить оплату', text: failure?.message || 'Проверьте соединение и попробуйте ещё раз.', offerPlans: false, canRetry: true };
}

function stop(): void {
  attempt += 1;
  window.clearTimeout(timer);
}

/** Закрывает экран ожидания: опрос прекращается, ссылка забывается. */
export function closePayment(): void {
  stop();
  link = null;
  payment.set({ ...idle });
}

/** После оплаты всё, что зависит от плана, читается заново без перезапуска приложения. */
async function refreshAfterPayment(request: PaymentRequest): Promise<void> {
  await Promise.allSettled([refreshAccount(), loadLimits(), loadPlans()]);
  const catalog = plansStore.get().catalog;
  const remaining = limitsStore.get().value?.remaining;
  if (request.intent === 'plan') {
    const name = planName(catalog?.current.plan, catalog) || request.planName;
    payment.set({ stage: 'paid', title: `План ${name} включён`, text: 'Функции плана уже доступны.', offerPlans: false });
    notify(`План ${name} включён`);
  } else {
    payment.set({
      stage: 'paid',
      title: 'Использование пополнено',
      text: remaining != null ? `Осталось ${remaining}%. Срок подписки не изменился.` : 'Остаток использования обновлён.',
      offerPlans: false,
    });
    notify('Использование пополнено');
  }
}

interface LinkStatus { id: string; status: 'created' | 'opened' | 'awaiting_payment' | 'paid' | 'canceled' | 'expired' }

let polling = false;

async function poll(token: number): Promise<void> {
  if (token !== attempt || !link || polling) return;
  polling = true;
  try {
    await check(token);
  } finally {
    polling = false;
  }
}

async function check(token: number): Promise<void> {
  if (!link) return;
  const { request } = payment.get();
  try {
    const status = await api<LinkStatus>(cloudPath(`billing/links/${encodeURIComponent(link.id)}`));
    if (token !== attempt || !request) return;
    if (status.status === 'paid') {
      stop();
      link = null;
      await refreshAfterPayment(request);
      return;
    }
    if (status.status === 'canceled') {
      stop();
      link = null;
      payment.set({ stage: 'canceled', title: 'Оплата не прошла', text: 'Деньги не списаны. Можно попробовать ещё раз: ссылка будет новой.', offerPlans: false });
      return;
    }
    if (status.status === 'expired') {
      stop();
      link = null;
      payment.set({ stage: 'expired', title: 'Ссылка устарела', text: 'Ссылка на оплату действует 30 минут. Запросите новую.', offerPlans: false });
      return;
    }
  } catch (error) {
    // временный сбой связи не обрывает ожидание; чужая или пропавшая ссылка - обрывает
    if (error instanceof ApiError && (error.status === 404 || error.status === 401)) {
      stop();
      link = null;
      payment.set({ stage: 'expired', title: 'Ссылка устарела', text: 'Запросите новую ссылку на оплату.', offerPlans: false });
      return;
    }
  }
  if (token !== attempt || !link) return;
  if (Date.now() > link.deadline) {
    stop();
    payment.set({ stage: 'expired', title: 'Ожидание закончилось', text: 'Если вы уже оплатили, подписка обновится сама через пару минут. Иначе запросите новую ссылку.', offerPlans: false });
    return;
  }
  timer = window.setTimeout(() => void poll(token), POLL_MS);
}

/** Опрос идёт, пока окно активно; при возврате из браузера статус проверяется сразу. */
export function checkPaymentNow(): void {
  if (!link || payment.get().stage !== 'waiting' || document.visibilityState !== 'visible') return;
  window.clearTimeout(timer);
  void poll(attempt);
}

/** Просит ссылку на оплату у сервера, открывает её в браузере и ждёт результата. */
export async function startPayment(request: PaymentRequest): Promise<void> {
  stop();
  const token = attempt;
  link = null;
  payment.set({ open: true, stage: 'creating', request, title: 'Готовим оплату…', text: 'Запрашиваем ссылку на сайте Докки.', offerPlans: false });
  try {
    const body = request.intent === 'plan'
      ? { intent: 'plan', plan: request.plan, return_app: 'koddi' }
      : { intent: 'topup', ...(request.percent ? { percent: request.percent } : {}), return_app: 'koddi' };
    const created = await post<{ id: string; url: string }>(cloudPath('billing/links'), body);
    if (token !== attempt) return;
    if (!created.id || !openExternal(created.url)) throw new ApiError('Сервер не вернул ссылку на оплату', 502);
    link = { id: created.id, url: created.url, deadline: Date.now() + WAIT_MS };
    payment.set({ stage: 'waiting', title: 'Ждём оплату на сайте…', text: 'Оплатите в открывшемся браузере. Приложение само увидит оплату и обновит подписку.', offerPlans: false });
    timer = window.setTimeout(() => void poll(token), POLL_MS);
  } catch (error) {
    if (token !== attempt) return;
    payment.set({ stage: 'failed', ...paymentFailure(error) });
  }
}

/** «Открыть страницу ещё раз»: та же ссылка, пока она не просрочена. */
export function reopenPayment(): boolean {
  return link ? openExternal(link.url) : false;
}

if (typeof window !== 'undefined') {
  window.addEventListener('focus', checkPaymentNow);
  document.addEventListener('visibilitychange', checkPaymentNow);
}

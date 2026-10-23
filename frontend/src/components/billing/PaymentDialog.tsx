import { CircleAlert, CircleCheck, ExternalLink, LoaderCircle } from 'lucide-react';
import { guarded } from '../../state/overlay';
import { closePayment, reopenPayment, startPayment, usePayment } from '../../state/payment';
import { openSettings } from '../../state/settingsUi';
import { PlainDialog } from '../ui/PlainDialog';

/** Экран ожидания оплаты на сайте: ссылка уже открыта в браузере, приложение само увидит результат. */
export function PaymentDialog() {
  const state = usePayment((value) => value);
  const { open, stage, request } = state;
  const retry = () => { if (request) void startPayment(request); };
  const icon = stage === 'paid'
    ? <CircleCheck aria-hidden="true" />
    : stage === 'waiting' || stage === 'creating' ? <LoaderCircle className="spin" aria-hidden="true" /> : <CircleAlert aria-hidden="true" />;
  return (
    <PlainDialog open={open} onClose={closePayment} className="payment-window" label="Оплата" closeDelay={140} backdropClose={stage !== 'waiting'}>
      <div className="payment-body" data-stage={stage} role="status" aria-live="polite">
        <span className="payment-icon">{icon}</span>
        <h2>{state.title}</h2>
        <p>{state.text}</p>
        <div className="payment-actions">
          {stage === 'waiting' && (
            <>
              <button type="button" className="pref-button" onClick={() => reopenPayment()}><ExternalLink aria-hidden="true" /><span>Открыть страницу ещё раз</span></button>
              <button type="button" className="pref-button" onClick={closePayment}>Отмена</button>
            </>
          )}
          {(stage === 'canceled' || stage === 'expired') && request && (
            <>
              <button type="button" className="pref-button primary" onClick={guarded(retry)}>Попробовать снова</button>
              <button type="button" className="pref-button" onClick={closePayment}>Закрыть</button>
            </>
          )}
          {stage === 'failed' && (
            <>
              {state.offerPlans
                ? <button type="button" className="pref-button primary" onClick={() => { closePayment(); openSettings('billing'); }}>Выбрать план</button>
                : request && state.canRetry && <button type="button" className="pref-button primary" onClick={guarded(retry)}>Попробовать снова</button>}
              <button type="button" className="pref-button" onClick={closePayment}>Закрыть</button>
            </>
          )}
          {stage === 'paid' && <button type="button" className="pref-button primary" onClick={closePayment}>Готово</button>}
        </div>
      </div>
    </PlainDialog>
  );
}

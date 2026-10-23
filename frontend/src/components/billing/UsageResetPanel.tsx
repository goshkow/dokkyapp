import { useState } from 'react';
import type { UsageReset } from '../../lib/types';
import { guarded } from '../../state/overlay';
import { startPayment } from '../../state/payment';
import { money } from '../../state/plans';

/** Сброс использования: к остатку прибавляются выбранные проценты (итог может быть больше 100%), срок подписки не меняется. */
export function UsageResetPanel({ reset, remaining, currency = 'RUB', compact = false }: { reset: UsageReset; remaining: number | null; currency?: string; compact?: boolean }) {
  const options = reset.options;
  const [percent, setPercent] = useState<number>(() => options[options.length - 1]?.percent ?? 0);
  const chosen = options.find((option) => option.percent === percent) ?? options[options.length - 1];
  const after = chosen?.remainingAfter ?? null;
  const fill = (value: number | null) => Math.max(0, Math.min(100, value ?? 0));
  return (
    <div className={`usage-reset${compact ? ' compact' : ''}`} data-testid="usage-reset">
      <div className="usage-reset-head">
        <strong>Использование заканчивается</strong>
        <span>Осталось {remaining ?? '—'}%</span>
      </div>
      <div className="usage-reset-scale" aria-hidden="true">
        {after != null && <i className="usage-reset-after" data-over={after > 100 ? true : undefined} style={{ width: `${fill(after)}%` }} />}
        <i className="usage-reset-now" style={{ width: `${fill(remaining)}%` }} />
      </div>
      {after != null && (
        <p className="usage-reset-result">
          Сейчас {remaining ?? '—'}% <span aria-hidden="true">→</span> после оплаты <b>{after}%</b>
          {after > 100 && <em> больше полного объёма плана</em>}
        </p>
      )}
      <div className="usage-reset-options" role="radiogroup" aria-label="Сколько добавить">
        {options.map((option) => (
          <button key={option.percent} type="button" role="radio" aria-checked={option.percent === percent} data-percent={option.percent} onClick={() => setPercent(option.percent)}>
            <b>+{option.percent}%</b>
            {option.price != null && <span>{money(option.price, currency)}</span>}
          </button>
        ))}
      </div>
      <button type="button" className="pref-button primary usage-reset-pay" disabled={!chosen} onClick={guarded(() => startPayment({ intent: 'topup', percent: chosen?.percent }))}>
        Сбросить использование
      </button>
      <p className="plan-note">Добавленные проценты действуют до конца оплаченного периода: подписка не продлевается.</p>
    </div>
  );
}

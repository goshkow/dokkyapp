import { TriangleAlert, X } from 'lucide-react';
import { useState } from 'react';
import { readText, writeText } from '../../lib/storage';
import type { Limits } from '../../lib/types';
import { useLimits } from '../../state/limits';
import { guarded } from '../../state/overlay';
import { nextPlan, usePlans, type Catalog, type Plan } from '../../state/plans';
import { openBilling } from '../../state/settingsUi';
import { useUi } from '../../state/ui';

interface Action { label: string; run: () => void }
export interface Notice { key: string; tone: 'warn' | 'danger'; title: string; text: string; actions: Action[] }

const LOW = 15;
const time = (value: string | null): string => {
  const date = value ? new Date(value) : null;
  if (!date || !Number.isFinite(date.getTime())) return '';
  return date.toDateString() === new Date().toDateString()
    ? date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
    : date.toLocaleString('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' });
};

/** Что подсказать про остаток использования; `null`, когда всё в порядке. Решения про сброс и улучшение плана принимает сервер. */
export function computeNotice(limits: Limits | null, scope: string, catalog: Catalog | null): Notice | null {
  if (!limits) return null;
  const upper: Plan | null = nextPlan(catalog);
  const upgrade: Action[] = upper ? [{ label: `Улучшить до ${upper.name}`, run: () => openBilling(upper.id) }] : [];
  const reset: Action[] = limits.usageReset?.available ? [{ label: 'Сбросить использование', run: () => openBilling() }] : [];

  // скользящее окно в 5 часов относится только к Докки Code
  if (scope === 'development' && limits.windowRemaining != null && limits.windowRemaining <= LOW) {
    const reopens = time(limits.windowResetsAt);
    const exhausted = limits.windowRemaining <= 0;
    return {
      key: `window:${exhausted ? 'out' : 'low'}:${limits.windowResetsAt ?? ''}`,
      tone: exhausted ? 'danger' : 'warn',
      title: exhausted ? `Лимит на ${limits.windowHours} часов закончился` : `${limits.windowHours}-часовой лимит заканчивается`,
      text: `${exhausted ? '' : `Осталось ${limits.windowRemaining}%. `}${reopens ? `Окно освободится ${reopens}.` : ''}${upper ? ' Улучшите план, чтобы работать дольше без пауз.' : ''}`.trim(),
      actions: [...upgrade, ...reset],
    };
  }
  // остаток подписки: у Free и пробного периода предлагается оформить подписку, у платных - сбросить использование
  if (limits.usageExhausted && !limits.paidActive) {
    return {
      key: 'free-out',
      tone: 'danger',
      title: 'Бесплатное использование закончилось',
      text: 'Оформите подписку, чтобы продолжить пользоваться Докки без ожидания.',
      actions: [{ label: upper ? `Купить ${upper.name}` : 'Выбрать план', run: () => openBilling(upper?.id) }],
    };
  }
  if (limits.usageReset?.available) {
    const remaining = limits.remaining ?? 0;
    return {
      key: `usage:${remaining <= 0 ? 'out' : 'low'}`,
      tone: remaining <= 0 ? 'danger' : 'warn',
      title: remaining <= 0 ? 'Использование закончилось' : 'Использование заканчивается',
      text: remaining <= 0 ? 'Сбросьте использование или улучшите план, чтобы продолжить.' : `Осталось ${remaining}%. Можно докупить использование: срок подписки не изменится.`,
      actions: [...reset, ...upgrade],
    };
  }
  return null;
}

const dismissedKey = 'koddi-dismissed-notices';
const readDismissed = (): string[] => {
  try { return JSON.parse(readText(dismissedKey) || '[]') as string[]; } catch { return []; }
};

/** Подсказка над полем ввода: остаток использования заканчивается или закончился. */
export function UsageNotice() {
  const limits = useLimits((state) => state.value);
  const catalog = usePlans((state) => state.catalog);
  const scope = useUi((state) => state.scope);
  const [dismissed, setDismissed] = useState(readDismissed);
  const notice = computeNotice(limits, scope, catalog);
  if (!notice || dismissed.includes(notice.key)) return null;
  const close = () => {
    const next = [...dismissed, notice.key].slice(-20);
    setDismissed(next);
    writeText(dismissedKey, JSON.stringify(next));
  };
  return (
    <div className="usage-notice" data-tone={notice.tone} role="status" data-testid="usage-notice">
      <TriangleAlert aria-hidden="true" />
      <div className="usage-notice-text">
        <strong>{notice.title}</strong>
        {notice.text && <span>{notice.text}</span>}
      </div>
      <div className="usage-notice-actions">
        {notice.actions.slice(0, 2).map((action, index) => (
          <button key={action.label} type="button" className={`pref-button${index === 0 ? ' primary' : ''}`} onClick={guarded(action.run)}>{action.label}</button>
        ))}
      </div>
      <button type="button" className="icon-button usage-notice-close" aria-label="Скрыть подсказку" title="Скрыть" onClick={close}><X aria-hidden="true" /></button>
    </div>
  );
}

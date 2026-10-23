import { setPlanRequiredHandler, type ApiError } from '../lib/api';
import { createStore, useStore } from '../lib/store';
import { data } from './data';
import { limitsStore, loadLimits, onLimitsLoaded, useLimits } from './limits';
import { notify } from './overlay';
import { openSettings } from './settingsUi';

/* Докки Code открыт с Plus и выше (а также тестерам с кредитами и админам). Free, пробный период и Base видят
   приглашение вместо поля ввода: старые чаты читаются, писать в них, создавать новые и запускать агента нельзя.
   Настоящее решение всегда за сервером Докки: любой его отказ `plan_required` закрывает запись так же. */

export interface DeveloperAccess {
  locked: boolean;
  /** Причина от сервера (`plus_required`); пусто, пока доступ открыт. */
  reason: string;
}

/** Сервер отказал по тарифу, а свежих данных подписки ещё нет: запись закрыта до следующего чтения. */
const forced = createStore({ value: false });

interface BootstrapModel { id?: string; available?: boolean; unavailable_reason?: string }

/** Решение по данным, которые уже есть: подписка (если прочитана), иначе список моделей из bootstrap. */
export function resolveAccess(limits: { developer: { allowed: boolean | null; reason: string } } | null, models: BootstrapModel[], denied: boolean): DeveloperAccess {
  if (denied) return { locked: true, reason: 'plus_required' };
  if (limits && typeof limits.developer.allowed === 'boolean') {
    return limits.developer.allowed ? { locked: false, reason: '' } : { locked: true, reason: limits.developer.reason || 'plus_required' };
  }
  const code = models.find((model) => model.id === 'dokky-code');
  if (code && code.available === false && code.unavailable_reason === 'plus_required') return { locked: true, reason: 'plus_required' };
  return { locked: false, reason: '' };
}

export function developerAccess(): DeveloperAccess {
  return resolveAccess(limitsStore.get().value, (data.get().account?.bootstrap?.models ?? []) as BootstrapModel[], forced.get().value);
}

export function useDeveloperAccess(): DeveloperAccess {
  const limits = useLimits((state) => state.value);
  const models = useStore(data, (state) => state.account?.bootstrap?.models);
  const denied = useStore(forced, (state) => state.value);
  return resolveAccess(limits, (models ?? []) as BootstrapModel[], denied);
}

/** Не пускает начало работы агента без доступа: подсказка и переход к планам. */
export function requireDeveloper(): boolean {
  if (!developerAccess().locked) return true;
  notify('Докки Code включается с подпиской Plus. Обновите план, чтобы писать агенту', true);
  openSettings('billing');
  return false;
}

/** Отказ сервера по тарифу (в том числе посреди работы агента): запись закрывается, подписка перечитывается. */
export function handlePlanRequired(): void {
  forced.set({ value: true });
  void loadLimits();
}

// после свежего чтения подписки решает она, а не прежний отказ
onLimitsLoaded(() => forced.set({ value: false }));
setPlanRequiredHandler((_error: ApiError) => handlePlanRequired());

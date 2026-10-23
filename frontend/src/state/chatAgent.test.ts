import { beforeEach, describe, expect, it } from 'vitest';
import { chat, resetChat } from './chatCore';
import { planDisplaySteps, setPlan, setPlanTurnState } from './chatAgent';
import type { PlanStep } from './chatTypes';

const steps: PlanStep[] = [{ title: 'Готовый', status: 'completed' }, { title: 'Текущий', status: 'running' }, { title: 'Следующий', status: 'pending' }];
beforeEach(resetChat);

describe('состояние выполнения плана', () => {
  it.each(['cancelled', 'failed', 'interrupted'] as const)('останавливает активный пункт после %s и сохраняет завершённый', (status) => {
    setPlan({ turn_id: 7, tracking_steps: steps });
    setPlanTurnState({ active: false, turn: { id: 7, status } });
    expect(planDisplaySteps(chat.get().plan!.tracking_steps!, chat.get().plan!.tracking_status).map((step) => step.status)).toEqual(['completed', status, 'pending']);
    expect(chat.get().plan!.tracking_steps![1]!.status).toBe('running');
  });
  it('возвращает выполнение только при возобновлении того же хода', () => {
    setPlan({ turn_id: 7, tracking_steps: steps, tracking_status: 'cancelled' });
    setPlanTurnState({ active: true, turn: { id: 8, status: 'running' } });
    expect(chat.get().plan!.tracking_status).toBe('cancelled');
    setPlanTurnState({ active: true, turn: { id: 7, status: 'running' } });
    expect(planDisplaySteps(steps, chat.get().plan!.tracking_status)[1]!.status).toBe('running');
  });
  it('открывает сохранённый прерванный план со статусом его хода из истории', () => {
    chat.set({ messages: [{ id: 3, role: 'assistant', content: '', metadata: { turn: { id: 7, status: 'interrupted' } } }] });
    setPlan({ turn_id: 7, tracking_steps: steps });
    expect(chat.get().plan!.tracking_status).toBe('interrupted');
  });
  it('отличает исторический документ от текущих пунктов выполнения', () => {
    setPlan({ turn_id: 7, tracking_turn_id: 9, tracking_status: 'failed', tracking_steps: steps, document: '# Старый план' });
    setPlanTurnState({ active: true, turn: { id: 7 } });
    expect(chat.get().plan!.tracking_status).toBe('failed');
    setPlanTurnState({ active: true, turn: { id: 9 } });
    expect(chat.get().plan!.tracking_status).toBe('running');
  });
  it('не изображает выполнение после перезапуска без активного агента', () => {
    setPlan({ turn_id: 7, tracking_status: 'running', tracking_steps: steps });
    setPlanTurnState({ active: false, turn: { id: 7, status: 'running' } });
    expect(chat.get().plan!.tracking_status).toBe('interrupted');
  });
  it('новый ход не наследует ошибку предыдущего плана', () => {
    setPlan({ turn_id: 7, tracking_turn_id: 9, tracking_status: 'failed', tracking_steps: steps });
    setPlan({ turn_id: 7, tracking_turn_id: 10, tracking_steps: steps });
    expect(chat.get().plan!.tracking_status).toBeUndefined();
  });
});

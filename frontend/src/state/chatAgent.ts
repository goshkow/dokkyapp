/* Запросы агента, план и изменения проекта. */
import { api, post } from '../lib/api';
import { notify, confirmDialog } from './overlay';
import { chat, currentChatId, followFeed, setStatus } from './chatCore';
import type { AgentRequest, Change, PlanSnapshot, PlanStep } from './chatTypes';

/* подтверждения и вопросы агента */
export async function showRequests(): Promise<void> {
  const id = currentChatId();
  if (id == null) return;
  const requests = await api<AgentRequest[]>('/api/agent/requests');
  if (currentChatId() !== id) return;
  chat.set({ requests: requests.filter((request) => request.chat_id === undefined || String(request.chat_id) === String(id)) });
}

export async function respondRequest(request: AgentRequest, payload: { approved?: boolean; answer?: string }): Promise<void> {
  await post(`/api/agent/requests/${request.id}/respond`, payload);
  chat.set({ requests: chat.get().requests.filter((item) => item.id !== request.id) });
  setStatus('Продолжаю выполнение…');
}

/* план */
export interface PlanTurnState { active: boolean; turn?: { id?: number | string; status?: string } }

const sameTurn = (left: unknown, right: unknown): boolean => left != null && right != null && String(left) === String(right);
const trackingTurn = (plan: PlanSnapshot) => plan.tracking_turn_id ?? plan.turn_id;

/** сохранённый running остаётся исходным: повтор хода сможет снова показать выполнение */
export function planDisplaySteps(steps: PlanStep[], status?: string): PlanStep[] {
  const stopped = status === 'failed' ? 'failed' : status === 'cancelled' ? 'cancelled' : ['interrupted', 'completed'].includes(status ?? '') ? 'interrupted' : null;
  return stopped ? steps.map((step) => step.status === 'running' ? { ...step, status: stopped } : step) : steps;
}

export function setPlanTurnState(state: PlanTurnState): void {
  const plan = chat.get().plan;
  if (!plan || !sameTurn(trackingTurn(plan), state.turn?.id)) return;
  const status = state.active ? 'running' : state.turn?.status === 'running' ? 'interrupted' : state.turn?.status;
  if (status) chat.set({ plan: { ...plan, tracking_status: status } });
}

export function setPlan(raw: PlanSnapshot): void {
  const previous = chat.get().plan;
  let snapshot: PlanSnapshot = { ...raw };
  // документ и статус приходят не с каждым событием, сохраняем их только для своего хода
  if (previous && sameTurn(previous.turn_id, raw.turn_id)) snapshot = { ...previous, ...raw, document: raw.document ?? previous.document };
  const turn = trackingTurn(snapshot);
  if (raw.tracking_status == null && !sameTurn(trackingTurn(previous ?? {}), turn)) delete snapshot.tracking_status;
  if (snapshot.tracking_status == null && turn != null) {
    const message = chat.get().messages.findLast((item) => sameTurn(item.metadata?.turn?.id ?? item.metadata?.turn_id, turn));
    snapshot.tracking_status = message?.metadata?.turn?.status;
  }
  const steps = raw.tracking_steps ?? raw.steps ?? [];
  chat.set({ plan: { ...snapshot, tracking_steps: Array.isArray(steps) ? steps : [] } });
  followFeed();
}

export const clearPlan = (): void => chat.set({ plan: null });

/* изменения */
export function setChanges(changes: Change[] | Change | null | undefined): void {
  const list = Array.isArray(changes) ? changes : changes ? [changes] : [];
  const selection = chat.get().changeSelection;
  chat.set({ changes: list, changeSelection: list.length ? selection : null });
}

export const selectChange = (key: string): void => chat.set({ changeSelection: key });

export async function rollbackChange(id: Change['id'], fileCount: number, afterSync: () => Promise<void>): Promise<void> {
  const text = fileCount > 1
    ? `Будет отменён весь набор изменений. Файлов: ${fileCount}. Это затронет не только открытый файл.`
    : 'Изменение выбранного файла будет отменено';
  if (!(await confirmDialog('Откатить изменения?', text, 'Откатить'))) return;
  const result = await post<{ rolled_back: boolean; reason?: string }>(`/api/changes/${id}/rollback`);
  if (!result.rolled_back) throw new Error(result.reason);
  await afterSync();
  notify('Изменения отменены');
}

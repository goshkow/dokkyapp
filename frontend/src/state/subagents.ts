/* Сабагенты текущего хода: что каждый делает сейчас - для его карточки в ленте. После хода не нужен:
   отчёт сабагента лежит в результате вызова. */
import { liveLabel } from '../lib/journal';
import { createStore, useStore } from '../lib/store';

export interface SubagentRun { agent: string; title: string; actions: number; last: string; status?: string }

export const subagentRuns = createStore<{ runs: Record<string, SubagentRun> }>({ runs: {} });

/** Событие task.progress сервера: started, tool (очередное действие сабагента) или finished. */
export function trackSubagent(data: Record<string, any>): void {
  const id = String(data.call_id ?? '');
  if (!id) return;
  const { runs } = subagentRuns.get();
  const current = runs[id] ?? { agent: String(data.agent ?? ''), title: String(data.title ?? ''), actions: 0, last: '' };
  const next = data.event === 'tool'
    ? { ...current, actions: current.actions + 1, last: liveLabel({ event: 'tool.call', data: { name: data.name, arguments: data.arguments } }) }
    : data.event === 'finished' ? { ...current, status: String(data.status ?? '') } : current;
  subagentRuns.set({ runs: { ...runs, [id]: next } });
}

export function clearSubagents(): void {
  if (Object.keys(subagentRuns.get().runs).length) subagentRuns.set({ runs: {} });
}

export const useSubagentRun = (id: string): SubagentRun | undefined => useStore(subagentRuns, (state) => state.runs[id]);

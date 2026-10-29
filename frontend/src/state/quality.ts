/* Объективные гейты качества в правой панели: последние прогоны проекта, инженерное состояние чата,
   решения человека по находкам, слои и тепловая карта. Данные считает сервер; здесь только показ и действия. */
import { api, del, post } from '../lib/api';
import { createStore, useStore } from '../lib/store';
import { view } from './view';
import { workspace } from './workspace';

export type GateName = 'signals' | 'crap' | 'dry' | 'architecture' | 'mutation';
export type GateStatus = 'passed' | 'failed' | 'skipped' | 'unavailable' | 'cancelled' | 'error';
export type Decision = 'review' | 'accepted' | 'refactor';

export interface Finding {
  key?: string;
  path?: string;
  line_start?: number | null;
  symbol?: string;
  kind?: string;
  severity?: string;
  value?: number;
  decision?: Decision;
  advice?: string;
  target?: string;
  other?: { path?: string; line_start?: number; symbol?: string };
  evidence?: Record<string, unknown>;
}

export interface GateRun {
  id: number;
  gate: GateName;
  status: GateStatus;
  turn_id: number | null;
  started_at: string;
  duration_ms: number;
  error?: string | null;
  summary: Record<string, unknown> & { notes?: string[] };
  findings: Finding[];
}

export interface Workflow {
  stage?: 'make_it_work' | 'make_it_right' | 'practice';
  units?: { title: string; check: string; verified: boolean }[];
  red?: { verified: boolean; checks: string[]; why?: string | null };
  green?: string[];
  premise_required?: string;
  root_cause?: string;
  quality?: Record<string, GateStatus>;
}

export interface AgentDecision { id: number; kind: string; summary: string; created_at: string; turn_id: number | null }

export interface Layer { name: string; paths: string[] }
export interface Policy { version: number; layers: Layer[]; ignore?: string[]; allow_type_imports?: boolean }
export interface Proposal { id: number; name: string; note: string; policy: Policy; created_at: string; label: string }
export interface HeatItem { score: number; max_complexity: number; max_crap: number | null; dry: number; survived: number; violations: number }

interface QualityState {
  projectId: number | null;
  chatId: number | null;
  load: 'idle' | 'loading' | 'ready' | 'failed';
  error: string;
  runs: GateRun[];
  workflow: Workflow;
  decisions: AgentDecision[];
  policy: Policy | null;
  proposals: Proposal[];
  proposalLabel: string;
  heat: Record<string, HeatItem>;
  heatOn: boolean;
  running: boolean;
}

const initial = (): QualityState => ({
  projectId: null, chatId: null, load: 'idle', error: '', runs: [], workflow: {}, decisions: [], policy: null,
  proposals: [], proposalLabel: '', heat: {}, heatOn: false, running: false,
});

export const quality = createStore<QualityState>(initial());
export const useQuality = <S,>(selector: (state: QualityState) => S): S => useStore(quality, selector);

export const GATE_LABELS: Record<GateName, string> = {
  signals: 'Сигналы', crap: 'CRAP', dry: 'Дубли', architecture: 'Слои', mutation: 'Мутации',
};
export const STATUS_LABELS: Record<GateStatus, string> = {
  passed: 'прошёл', failed: 'провал', skipped: 'пропущен', unavailable: 'не измерен', cancelled: 'отменён', error: 'сбой',
};
export const STAGE_LABELS: Record<NonNullable<Workflow['stage']>, string> = {
  make_it_work: 'Make It Work', make_it_right: 'Make It Right', practice: 'Practice',
};
const ORDER: GateName[] = ['signals', 'crap', 'dry', 'architecture', 'mutation'];

/** Последний прогон каждого гейта: старые записи того же гейта не показываются. */
// ответ без массива (старый сервер, сбой прокси) не должен ронять правую панель
function list<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

export function latestByGate(runs: GateRun[]): GateRun[] {
  const seen = new Map<GateName, GateRun>();
  for (const run of runs) if (!seen.has(run.gate) || (seen.get(run.gate) as GateRun).id < run.id) seen.set(run.gate, run);
  return ORDER.flatMap((gate) => (seen.has(gate) ? [seen.get(gate) as GateRun] : []));
}

/** Главное число гейта одной строкой: без него статус «провал» ничего не говорит. */
export function gateDetail(run: Pick<GateRun, 'gate' | 'summary' | 'status'>): string {
  const s = run.summary ?? {};
  const n = (key: string) => (typeof s[key] === 'number' ? (s[key] as number) : null);
  if (run.gate === 'crap') {
    if (n('violations')) return `выше порога ${n('threshold') ?? 10}: ${n('violations')}`;
    if (n('unknown_coverage')) return `без покрытия: ${n('unknown_coverage')}`;
    return n('max_crap') !== null ? `макс. ${n('max_crap')}` : '';
  }
  if (run.gate === 'dry') return n('candidates') ? `кандидатов ${n('candidates')}, открытых ${n('open_candidates') ?? 0}` : '';
  if (run.gate === 'mutation') {
    const evaluated = n('evaluated');
    return evaluated ? `убито ${n('killed') ?? 0} из ${evaluated}` + (n('survived') ? `, выжило ${n('survived')}` : '') : '';
  }
  if (run.gate === 'architecture') return n('violations') ? `нарушений ${n('violations')}, новых ${n('new') ?? 0}` : '';
  return n('total') ? `сигналов ${n('total')}, важных ${n('high') ?? 0}` : '';
}

let request = 0;

export async function loadQuality(): Promise<void> {
  const { project, chat } = view.get();
  const ticket = ++request;
  if (!project) {
    quality.set(initial());
    return;
  }
  const chatId = typeof chat?.id === 'number' ? chat.id : /^\d+$/.test(String(chat?.id ?? '')) ? Number(chat?.id) : null;
  if (quality.get().projectId !== project.id) quality.set({ ...initial(), projectId: project.id, heatOn: quality.get().heatOn });
  quality.set({ load: quality.get().load === 'ready' ? 'ready' : 'loading', error: '', chatId });
  try {
    const [runs, architecture, engineering] = await Promise.all([
      api<{ runs: GateRun[] }>(`/api/projects/${project.id}/quality/runs?limit=40`),
      api<{ policy: Policy | null; proposals: Proposal[]; proposal_label: string }>(`/api/projects/${project.id}/architecture`),
      chatId !== null
        ? api<{ workflow: Workflow; decisions: AgentDecision[] }>(`/api/chats/${chatId}/engineering`)
        : Promise.resolve({ workflow: {}, decisions: [] }),
    ]);
    if (ticket !== request) return;
    quality.set({
      runs: list<GateRun>(runs?.runs), policy: architecture?.policy ?? null, proposals: list<Proposal>(architecture?.proposals),
      proposalLabel: architecture?.proposal_label ?? '', workflow: engineering?.workflow ?? {},
      decisions: list<AgentDecision>(engineering?.decisions), load: 'ready', error: '',
    });
  } catch (error) {
    if (ticket !== request || (error as Error).name === 'AbortError') return;
    quality.set({ load: 'failed', error: (error as Error).message });
    return;
  }
  // подсветка отдельно: её сбой не должен помечать загруженную карточку как неудачную
  if (quality.get().heatOn && ticket === request) await loadHeat().catch(() => undefined);
}

async function loadHeat(): Promise<void> {
  const project = view.get().project;
  if (!project) return;
  const result = await api<{ files: Record<string, HeatItem> }>(`/api/projects/${project.id}/quality/heatmap`);
  if (view.get().project?.id === project.id) quality.set({ heat: result.files });
}

export async function toggleHeat(): Promise<void> {
  const next = !quality.get().heatOn;
  quality.set({ heatOn: next });
  if (!next) return;
  try {
    await loadHeat();
  } catch (error) {
    quality.set({ heatOn: false });
    throw error;
  }
}

// идущие ручные прогоны по проектам: смена чата сбрасывает карточку, но не должна разрешать второй прогон
const running = new Set<number>();

/** Ручной прогон кнопкой; мутации запускают тесты проекта в изолированной копии. */
export async function runQuality(gates: GateName[]): Promise<void> {
  const project = view.get().project;
  if (!project || running.has(project.id)) return;
  running.add(project.id);
  quality.set({ running: true });
  try {
    await post(`/api/projects/${project.id}/quality/run`, { gates });
  } finally {
    running.delete(project.id);
    if (view.get().project?.id === project.id) quality.set({ running: false });
  }
  if (view.get().project?.id === project.id) await loadQuality();
}

export async function decide(gate: 'dry' | 'mutation' | 'architecture', key: string, decision: Decision): Promise<void> {
  const project = view.get().project;
  if (!project) return;
  await post(`/api/projects/${project.id}/quality/decision`, { gate, key, decision });
  if (view.get().project?.id !== project.id) return;
  // решение видно сразу; следующий прогон учтёт его в статусе гейта
  quality.set((state) => ({
    runs: state.runs.map((run) => (run.gate !== gate ? run : {
      ...run, findings: run.findings.map((item) => (item.key === key ? { ...item, decision } : item)),
    })),
  }));
}

export async function suggestLayers(): Promise<{ layers: Layer[]; note?: string; cycles?: string[][]; evaluation?: { violations: number } | null }> {
  const project = view.get().project;
  if (!project) return { layers: [] };
  return post(`/api/projects/${project.id}/architecture/suggest`);
}

export async function applyProposal(id: number): Promise<void> {
  const project = view.get().project;
  if (!project) return;
  await post(`/api/projects/${project.id}/architecture/proposals/${id}/apply`);
  if (view.get().project?.id === project.id) await loadQuality();
}

export async function deleteProposal(id: number): Promise<void> {
  const project = view.get().project;
  if (!project) return;
  await del(`/api/projects/${project.id}/architecture/proposals/${id}`);
  if (view.get().project?.id === project.id) await loadQuality();
}

let refreshTimer = 0;
/** Сервер прогнал гейты или обновил состояние качества хода: открытая карточка перечитывает данные. */
export function onQualityEvent(): void {
  window.clearTimeout(refreshTimer);
  // закрытой карточке хватает списка прогонов для счётчика провалов в заголовке; остальное - при открытии
  const full = workspace.get().focus === 'quality' || quality.get().heatOn;
  refreshTimer = window.setTimeout(() => { void (full ? loadQuality() : loadRuns()); }, 300);
}

async function loadRuns(): Promise<void> {
  const project = view.get().project;
  if (!project) return;
  const ticket = request;
  try {
    const result = await api<{ runs: GateRun[] }>(`/api/projects/${project.id}/quality/runs?limit=40`);
    if (ticket === request && view.get().project?.id === project.id) quality.set({ runs: list<GateRun>(result?.runs) });
  } catch {
    // счётчик в заголовке необязателен: ошибка видна при открытии карточки
  }
}

let lastKey = '';
view.subscribe(() => {
  const { project, chat } = view.get();
  const key = `${project?.id ?? ''}:${chat?.id ?? ''}`;
  if (key === lastKey) return;
  lastKey = key;
  request += 1;
  quality.set({ ...initial(), projectId: project?.id ?? null, heatOn: quality.get().heatOn, running: project ? running.has(project.id) : false });
  // подсветка на карте включена - у нового проекта своя
  if (project && quality.get().heatOn) void loadHeat().catch(() => undefined);
});

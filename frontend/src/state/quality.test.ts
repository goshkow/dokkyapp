import { beforeEach, describe, expect, it, vi } from 'vitest';

const env = vi.hoisted(() => ({ api: vi.fn(), post: vi.fn(), del: vi.fn() }));
vi.mock('../lib/api', () => ({ api: env.api, post: env.post, del: env.del }));
vi.mock('./view', async () => {
  const { createStore } = await import('../lib/store');
  return { view: createStore<{ project: { id: number } | null; chat: { id: number | string } | null }>({ project: null, chat: null }) };
});
vi.mock('./workspace', async () => {
  const { createStore } = await import('../lib/store');
  return { workspace: createStore<{ focus: string }>({ focus: '' }) };
});
import { view } from './view';
import { workspace } from './workspace';
import { decide, gateDetail, latestByGate, loadQuality, onQualityEvent, quality, runQuality, toggleHeat, type GateRun } from './quality';

const run = (id: number, gate: GateRun['gate'], status: GateRun['status'], summary: Record<string, unknown> = {}): GateRun => ({
  id, gate, status, turn_id: null, started_at: '', duration_ms: 1, summary, findings: [],
});
// в тестах вид - только проект и чат
const setView = (value: { project: { id: number } | null; chat: { id: number | string } | null }) => view.set(value as never);
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  env.api.mockReset();
  env.post.mockReset();
  setView({ project: null, chat: null });
  workspace.set({ focus: '' });
});

describe('гейты качества', () => {
  it('берёт последний прогон каждого гейта в фиксированном порядке', () => {
    const latest = latestByGate([run(1, 'mutation', 'failed'), run(3, 'crap', 'passed'), run(2, 'crap', 'failed'), run(4, 'signals', 'passed')]);
    expect(latest.map((item) => [item.gate, item.id])).toEqual([['signals', 4], ['crap', 3], ['mutation', 1]]);
  });

  it('показывает главное число гейта и не выдаёт «не измерено» за успех', () => {
    expect(gateDetail(run(1, 'crap', 'failed', { violations: 2, threshold: 10 }))).toBe('выше порога 10: 2');
    expect(gateDetail(run(1, 'crap', 'unavailable', { unknown_coverage: 3 }))).toBe('без покрытия: 3');
    expect(gateDetail(run(1, 'mutation', 'failed', { evaluated: 6, killed: 5, survived: 1 }))).toBe('убито 5 из 6, выжило 1');
    expect(gateDetail(run(1, 'architecture', 'failed', { violations: 3, new: 1 }))).toBe('нарушений 3, новых 1');
    expect(gateDetail(run(1, 'dry', 'passed', {}))).toBe('');
  });
});

describe('загрузка и действия', () => {
  it('грузит прогоны, слои и инженерное состояние чата', async () => {
    setView({ project: { id: 7 }, chat: { id: '12' } });
    env.api.mockImplementation(async (url: string) => {
      if (url.includes('/quality/runs')) return { runs: [run(1, 'crap', 'passed')] };
      if (url.includes('/architecture')) return { policy: null, proposals: [], proposal_label: 'ПРЕДЛОЖЕНИЕ - не применено' };
      if (url === '/api/chats/12/engineering') return { workflow: { stage: 'make_it_right' }, decisions: [] };
      throw new Error(url);
    });
    await loadQuality();
    expect(quality.get()).toMatchObject({ load: 'ready', projectId: 7, chatId: 12, workflow: { stage: 'make_it_right' }, proposalLabel: 'ПРЕДЛОЖЕНИЕ - не применено' });
    expect(quality.get().runs).toHaveLength(1);
  });

  it('ошибка загрузки видна, а не выдаётся за пустой результат', async () => {
    setView({ project: { id: 7 }, chat: null });
    env.api.mockRejectedValue(new Error('сеть недоступна'));
    await loadQuality();
    expect(quality.get()).toMatchObject({ load: 'failed', error: 'сеть недоступна' });
  });

  it('решение человека сразу отмечается в находке', async () => {
    setView({ project: { id: 7 }, chat: null });
    quality.set({ runs: [{ ...run(5, 'mutation', 'failed'), findings: [{ key: 'm1', kind: 'survived' }, { key: 'm2', kind: 'survived' }] }] });
    env.post.mockResolvedValue({ ok: true });
    await decide('mutation', 'm1', 'accepted');
    expect(env.post).toHaveBeenCalledWith('/api/projects/7/quality/decision', { gate: 'mutation', key: 'm1', decision: 'accepted' });
    expect(quality.get().runs[0]?.findings.map((item) => item.decision)).toEqual(['accepted', undefined]);
  });

  it('ручной прогон снимает флаг даже при ошибке сервера', async () => {
    setView({ project: { id: 7 }, chat: null });
    env.post.mockRejectedValue(new Error('fail'));
    await expect(runQuality(['mutation'])).rejects.toThrow('fail');
    expect(quality.get().running).toBe(false);
  });

  it('при закрытой карточке событие хода обновляет только список прогонов для счётчика', async () => {
    setView({ project: { id: 7 }, chat: null });
    env.api.mockResolvedValue({ runs: [run(9, 'crap', 'failed')], policy: null, proposals: [], proposal_label: '' });
    onQualityEvent();
    await new Promise((resolve) => setTimeout(resolve, 350));
    await flush();
    expect(env.api.mock.calls.map((call) => call[0])).toEqual(['/api/projects/7/quality/runs?limit=40']);
    expect(quality.get().runs.map((item) => item.id)).toEqual([9]);
    env.api.mockClear();
    workspace.set({ focus: 'quality' });
    onQualityEvent();
    await new Promise((resolve) => setTimeout(resolve, 350));
    await flush();
    expect(env.api).toHaveBeenCalled();
  });

  it('сбой тепловой карты отжимает кнопку и не портит загруженную карточку', async () => {
    setView({ project: { id: 7 }, chat: null });
    env.api.mockRejectedValueOnce(new Error('нет карты'));
    await expect(toggleHeat()).rejects.toThrow('нет карты');
    expect(quality.get().heatOn).toBe(false);
    quality.set({ heatOn: true });
    env.api.mockImplementation(async (url: string) => {
      if (url.includes('/heatmap')) throw new Error('нет карты');
      if (url.includes('/quality/runs')) return { runs: [] };
      return { policy: null, proposals: [], proposal_label: '' };
    });
    await loadQuality();
    expect(quality.get().load).toBe('ready');
    quality.set({ heatOn: false });
  });
});

describe('ответ без массива', () => {
  it('не ломает список прогонов и правую панель', async () => {
    setView({ project: { id: 77 }, chat: null });
    env.api.mockResolvedValue({});
    onQualityEvent();
    await new Promise((resolve) => setTimeout(resolve, 350));
    await flush();
    expect(quality.get().runs).toEqual([]);
    expect(latestByGate(quality.get().runs)).toEqual([]);
  });
});

import { beforeEach, describe, expect, it, vi } from 'vitest';

const env = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('../lib/api', () => ({ api: env.api }));
vi.mock('./view', async () => {
  const { createStore } = await import('../lib/store');
  return { view: createStore<{ project: { id: number } | null }>({ project: null }) };
});
import { view } from './view';
import { loadMap, onMapUpdated, projectMap, setMapActive } from './projectMap';

const tree = (version: number) => ({
  version, files: [{ p: 'app/a.py', w: 10, l: 10 }], rest: [], totals: { files: 1, dirs: 1, api: 0, notes: 0 },
  state: { status: 'ready', files: 1, truncated: false, built_at: null, error: null }, watch: 'events',
});
const project = (id: number) => ({ id, name: `p${id}`, path: `/p${id}` });
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const mapCalls = () => env.api.mock.calls.map((call) => String(call[0])).filter((url) => /\/map$/.test(url));

beforeEach(async () => {
  vi.useRealTimers();
  setMapActive(false);
  view.set({ project: null });
  await flush();
  vi.clearAllMocks();
  env.api.mockImplementation(async (url: string) => (url.includes('/map/state') ? { status: 'ready' } : tree(1)));
});

describe('карта проекта в правой панели', () => {
  it('открытие проекта запускает сборку на сервере, мозаика грузится только в открытой карточке', async () => {
    view.set({ project: project(5) });
    await flush();
    expect(env.api).toHaveBeenCalledWith('/api/projects/5/map/state');
    expect(mapCalls()).toEqual([]);
    setMapActive(true);
    await flush();
    expect(mapCalls()).toEqual(['/api/projects/5/map']);
    expect(projectMap.get()).toMatchObject({ projectId: 5, load: 'ready' });
    expect(projectMap.get().tree?.files[0]?.p).toBe('app/a.py');
  });

  it('повторная загрузка не сбрасывает показанную карту', async () => {
    view.set({ project: project(5) });
    setMapActive(true);
    await flush();
    let release: (value: unknown) => void = () => undefined;
    env.api.mockImplementation(() => new Promise((resolve) => { release = resolve; }));
    const pending = loadMap();
    expect(projectMap.get()).toMatchObject({ load: 'ready', tree: { version: 1 } });
    release(tree(2));
    await pending;
    expect(projectMap.get().tree?.version).toBe(2);
  });

  it('событие сервера перечитывает карту один раз и не трогает чужой проект', async () => {
    view.set({ project: project(5) });
    setMapActive(true);
    await flush();
    vi.useFakeTimers();
    const before = mapCalls().length;
    onMapUpdated({ project_id: 9, version: 2 });
    onMapUpdated({ project_id: 5, version: 1 });
    onMapUpdated({ project_id: 5, version: 2 });
    onMapUpdated({ project_id: 5, version: 3 });
    await vi.advanceTimersByTimeAsync(500);
    expect(mapCalls().length - before).toBe(1);
  });

  it('закрытая карточка сбрасывает устаревшую карту, ответ прежнего проекта не применяется', async () => {
    view.set({ project: project(5) });
    setMapActive(true);
    await flush();
    setMapActive(false);
    onMapUpdated({ project_id: 5, version: 4 });
    expect(projectMap.get()).toMatchObject({ tree: null, load: 'idle' });

    let release: (value: unknown) => void = () => undefined;
    env.api.mockImplementation((url: string) => (url.endsWith('/map') ? new Promise((resolve) => { release = resolve; }) : Promise.resolve({})));
    const pending = loadMap();
    view.set({ project: project(6) });
    release(tree(7));
    await pending;
    expect(projectMap.get()).toMatchObject({ projectId: 6, tree: null });
  });

  it('ошибка сервера и неизвестный ответ показываются, а не роняют карточку', async () => {
    view.set({ project: project(5) });
    await flush();
    env.api.mockImplementation(async () => { throw new Error('Проект недоступен'); });
    await loadMap();
    expect(projectMap.get()).toMatchObject({ load: 'failed', error: 'Проект недоступен' });
    env.api.mockImplementation(async () => ({ files: 'x' }));
    await loadMap();
    expect(projectMap.get().error).toBe('Сервер вернул карту в неизвестном виде');
  });
});

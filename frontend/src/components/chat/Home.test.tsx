import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
  scope: 'development', page: 'chat', chat: null as unknown, started: false, siteStatus: 'off', locked: false,
  activity: {} as Record<string, string>,
  chats: [] as { id: number; title: string; updated_at: string; project_id?: number }[],
}));
const actions = vi.hoisted(() => ({ openChat: vi.fn(), openPage: vi.fn(), openLibrary: vi.fn(), openFolder: vi.fn(), createProject: vi.fn() }));

vi.mock('../../state/ui', () => ({ useUi: (select: (s: unknown) => unknown) => select({ scope: state.scope }) }));
vi.mock('../../state/view', () => ({ useView: (select: (s: unknown) => unknown) => select({ page: state.page, chat: state.chat, project: null }) }));
vi.mock('../../state/chatCore', () => ({
  useChat: (select: (s: unknown) => unknown) => select({ messages: state.started ? [{}] : [], optimistic: null, live: [] }),
}));
vi.mock('../../state/surface', () => ({ useSurface: (select: (s: unknown) => unknown) => select({ status: state.siteStatus }) }));
vi.mock('../../state/access', () => ({ useDeveloperAccess: () => ({ locked: state.locked }) }));
vi.mock('../../state/data', () => ({ useData: (select: (s: unknown) => unknown) => select({ localChats: state.chats, cloudChats: [], projects: [{ id: 3, name: 'demo' }] }) }));
vi.mock('../../state/chatActivity', () => ({ chatActivity: { get: () => ({ activity: state.activity }), subscribe: () => () => {} } }));
vi.mock('../../lib/store', () => ({ useStore: (store: { get: () => unknown }, select: (s: unknown) => unknown) => select(store.get()) }));
vi.mock('../../state/actions', () => ({ createProject: actions.createProject, openFolder: actions.openFolder }));
vi.mock('../../state/overlay', () => ({ guarded: (run: () => void) => run }));
vi.mock('../../state/remote', () => ({ openChat: actions.openChat, openLibrary: actions.openLibrary, openPage: actions.openPage }));
vi.mock('../../lib/glass', () => ({ kickGlass: vi.fn() }));
vi.mock('../../lib/mascot-rig.js', () => ({}));

import { Home } from './Home';

const mainWith = () => {
  document.body.innerHTML = '<main id="main"><section id="composer"></section><div id="root"></div></main>';
  return document.getElementById('root')!;
};

globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;

beforeEach(() => {
  Object.assign(state, { scope: 'development', page: 'chat', chat: null, started: false, siteStatus: 'off', locked: false, activity: {}, chats: [] });
  vi.stubGlobal('matchMedia', () => ({ matches: true }));
  vi.clearAllMocks();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('новый чат режима кода: экран есть, поле поднято, разделы открываются', () => {
  const { container } = render(<Home />, { container: mainWith() });
  expect(document.getElementById('main')!.hasAttribute('data-home')).toBe(true);
  expect(container.querySelector('.home-word')?.textContent).toBe('Докки');
  const rows = [...container.querySelectorAll<HTMLElement>('.home-row')];
  expect(rows.map((row) => row.querySelector('b')?.textContent)).toContain('Открыть проект');
  fireEvent.click(rows.find((row) => row.textContent?.includes('Задачи'))!);
  expect(actions.openPage).toHaveBeenCalledWith('mission');
});

it.each([
  ['открыт чат', { chat: { id: 1 } }],
  ['пошла переписка', { started: true }],
  ['другая страница', { page: 'skills' }],
  ['Докки Code закрыт тарифом', { locked: true }],
  ['режим чата принадлежит сайту', { scope: 'chat', siteStatus: 'ready' }],
])('экрана нет: %s', (_name, change) => {
  Object.assign(state, change);
  const { container } = render(<Home />, { container: mainWith() });
  expect(container.querySelector('.home-stage')).toBeNull();
  expect(document.getElementById('main')!.hasAttribute('data-home')).toBe(false);
});

it('«Продолжить»: работающий чат первым, старше двух недель не показывается', () => {
  const now = Date.now();
  state.chats = [
    { id: 1, title: 'Свежий', updated_at: new Date(now - 3_600_000).toISOString(), project_id: 3 },
    { id: 2, title: 'Работает', updated_at: new Date(now - 86_400_000).toISOString() },
    { id: 3, title: 'Старый', updated_at: new Date(now - 30 * 86_400_000).toISOString() },
  ];
  state.activity = { 2: 'working' };
  const { container } = render(<Home />, { container: mainWith() });
  const titles = [...container.querySelectorAll('.home-column:first-child .home-row b')].map((node) => node.textContent);
  expect(titles).toEqual(['Работает', 'Свежий']);
  expect(container.querySelector('.home-row.is-working small')?.textContent).toBe('Докки работает');
  fireEvent.click(container.querySelector('.home-row.is-working')!);
  expect(actions.openChat).toHaveBeenCalledWith(expect.objectContaining({ id: 2 }));
});

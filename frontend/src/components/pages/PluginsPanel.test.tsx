import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { componentsLine, PluginCatalog, PluginsPanel } from './PluginsPanel';
import { pluginsStore } from '../../state/plugins';

const confirm = vi.fn();
vi.mock('../../state/overlay', () => ({ confirmDialog: (...args: unknown[]) => confirm(...args), guarded: (fn: unknown) => fn, notify: vi.fn() }));
vi.mock('../../lib/external', () => ({ openExternal: vi.fn() }));
vi.mock('../../state/skills', () => ({ loadSkills: vi.fn().mockResolvedValue(undefined) }));

const json = (value: unknown) => new Response(JSON.stringify(value));

const entry = {
  id: 'egm', name: 'egm', title: 'Elephant-Goldfish Model', repo: 'google/elephant-goldfish-model', path: '', ref: 'v0.1.0',
  commit: 'b3a35956759bf7f024af2ff061b2e0b44b438e4c', format: 'claude+codex', description: 'Ревью дизайна свежим взглядом.',
  stars: 5056, license: 'Apache-2.0', marketplace: 'egm', components: { skills: 7, commands: 0, agents: 9, hooks: false, mcp: false },
  installed_id: null, update: false,
};

const plugin = {
  id: 4, name: 'tools', title: 'Acme tools', description: 'Инструменты', version: '1.2.0', repo: 'acme/tools', path: '', ref: '',
  commit: 'c'.repeat(40), catalog_id: null, formats: ['claude'], license: 'MIT', author: 'Acme', enabled: true, url: 'https://github.com/acme/tools',
  skills: [{ name: 'review', description: 'Review', model_invocation: true }], commands: [], agents: [],
  hooks: [{ event: 'SessionStart', matcher: '', type: 'command', command: 'echo hi', timeout: 60, supported: true, source: 'hooks/hooks.json' }],
  mcp: [{ name: 'web', type: 'stdio', command: 'npx', args: ['-y', 'pkg@1.0.0'], cwd: '/data/plug', url: '',
    env: [{ name: 'MODE', value: 'fast' }, { name: 'API_KEY', value: '•••' }], headers: [] }], executable: true, exec_digest: 'a'.repeat(64), exec_approved: false, skipped_links: [], file_count: 3,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  confirm.mockReset();
  pluginsStore.set({ items: [], catalog: [], checkedAt: '', loaded: false, catalogLoaded: false, error: '', busy: [] });
});

describe('плагины', () => {
  it('ставит плагин из каталога по id и закреплённому коммиту', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(json({ items: [entry], checked_at: '2026-10-29' }))
      .mockResolvedValueOnce(json({ plugin: { ...plugin, title: 'Elephant-Goldfish Model' }, installed: true }))
      .mockResolvedValue(json({ plugins: [], items: [{ ...entry, installed_id: 1 }], checked_at: '2026-10-29' }));
    vi.stubGlobal('fetch', fetch);
    render(<PluginCatalog />);
    await screen.findByText('Elephant-Goldfish Model');
    expect(screen.getByText('7 навыков · 9 агентов')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Установить' }));
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => url === '/api/plugins/install')).toBe(true));
    const call = fetch.mock.calls.find(([url]) => url === '/api/plugins/install');
    expect(JSON.parse(call?.[1].body)).toEqual({ catalog_id: 'egm' });
  });

  it('хуки не разрешаются без подтверждения человека', async () => {
    const fetch = vi.fn().mockResolvedValue(json({ plugins: [plugin] }));
    vi.stubGlobal('fetch', fetch);
    confirm.mockResolvedValueOnce(false);
    render(<PluginsPanel onCatalog={vi.fn()} />);
    await screen.findByText('Хуки и MCP не запускаются, пока вы не разрешите');
    expect(screen.getByText('echo hi')).toBeTruthy();
    expect(screen.getByText('npx -y pkg@1.0.0')).toBeTruthy();
    expect(screen.getByText('Окружение: MODE=fast, API_KEY=•••')).toBeTruthy();
    expect(screen.getByText('Папка: /data/plug')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Разрешить запуск' }));
    await waitFor(() => expect(confirm).toHaveBeenCalled());
    expect(fetch.mock.calls.some(([url]) => String(url).includes('/exec'))).toBe(false);
  });

  it('предлагает выбрать плагин маркетплейса', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(json({ plugins: [] }))
      .mockResolvedValueOnce(json({ needs_choice: true, choices: [{ name: 'alpha', path: 'plugins/alpha', description: 'Первый' }] }));
    vi.stubGlobal('fetch', fetch);
    render(<PluginsPanel onCatalog={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText('https://github.com/owner/repo или owner/repo'), { target: { value: 'acme/market' } });
    fireEvent.click(screen.getByRole('button', { name: 'Установить' }));
    await screen.findByText('alpha');
    expect(screen.getByText('Первый')).toBeTruthy();
  });

  it('пишет состав по-русски', () => {
    expect(componentsLine({ skills: 1, commands: 2, agents: 5, hooks: true, mcp: false })).toBe('1 навык · 2 команды · 5 агентов · хуки');
  });
});

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { git, type GitPr } from '../../state/git';
import { checksText, GitHubPr } from './GitHubPr';

const openExternal = vi.fn((_url: string) => true);
const refreshGithub = vi.fn(() => Promise.resolve());
vi.mock('../../lib/external', () => ({ openExternal: (url: string) => openExternal(url) }));
vi.mock('../../state/remote', () => ({ refreshGithub: () => refreshGithub() }));
vi.mock('../../state/overlay', () => ({ guarded: (fn: () => unknown) => fn }));

const pr = (patch: Partial<GitPr> = {}): GitPr => ({
  number: 12, title: 'Новая кнопка', state: 'OPEN', draft: false, url: 'https://github.com/o/r/pull/12',
  checks: { state: 'failure', total: 6, passed: 3, failed: 1, pending: 2, failed_names: ['tests'] }, ...patch,
});
const base = { repo: true, remote: 'origin', branch: 'feat', projectId: 1 };

describe('PR в блоке git', () => {
  beforeEach(() => { openExternal.mockClear(); refreshGithub.mockClear(); });
  afterEach(() => { cleanup(); git.set({ repo: false, remote: null, branch: null, github: null }); });

  it('ничего не показывает, если gh недоступен, репозиторий без remote или ответа ещё нет', () => {
    git.set({ ...base, github: { available: false, pr: null } });
    const { container, rerender } = render(<GitHubPr />);
    expect(container.firstChild).toBeNull();
    git.set({ github: null });
    rerender(<GitHubPr />);
    expect(container.firstChild).toBeNull();
    git.set({ github: { available: true, pr: pr() }, remote: null });
    rerender(<GitHubPr />);
    expect(container.firstChild).toBeNull();
  });

  it('показывает номер, название, статус и сводку проверок; ссылка идёт во внешний браузер', () => {
    git.set({ ...base, github: { available: true, pr: pr() } });
    render(<GitHubPr />);
    expect(screen.getByText('Открыт')).toBeTruthy();
    expect(screen.getByText('Проверки: прошли 3 · упали 1 · идут 2').parentElement?.getAttribute('title')).toContain('tests');
    fireEvent.click(screen.getByText('Новая кнопка'));
    expect(openExternal).toHaveBeenCalledWith('https://github.com/o/r/pull/12');
  });

  it('черновик, влитый PR и ветка без PR', () => {
    git.set({ ...base, github: { available: true, pr: pr({ draft: true }) } });
    const { rerender } = render(<GitHubPr />);
    expect(screen.getByText('Черновик')).toBeTruthy();
    git.set({ github: { available: true, pr: pr({ state: 'MERGED', checks: { state: 'none', total: 0, passed: 0, failed: 0, pending: 0, failed_names: [] } }) } });
    rerender(<GitHubPr />);
    expect(screen.getByText('Влит')).toBeTruthy();
    expect(screen.queryByText(/Проверки/)).toBeNull();
    git.set({ github: { available: true, pr: null } });
    rerender(<GitHubPr />);
    expect(screen.getByText('Pull request для ветки не создан')).toBeTruthy();
  });

  it('«Обновить» просит свежие данные, пока идёт запрос кнопка заблокирована', async () => {
    let finish: () => void = () => undefined;
    refreshGithub.mockReturnValueOnce(new Promise<void>((resolve) => { finish = resolve; }));
    git.set({ ...base, github: { available: true, pr: pr() } });
    render(<GitHubPr />);
    const button = screen.getByRole('button', { name: 'Обновить' });
    await act(async () => { fireEvent.click(button); });
    expect(refreshGithub).toHaveBeenCalledTimes(1);
    expect(button).toHaveProperty('disabled', true);
    await act(async () => { finish(); });
    expect(button).toHaveProperty('disabled', false);
  });

  it('текст проверок опускает нулевые группы', () => {
    expect(checksText({ state: 'success', total: 2, passed: 2, failed: 0, pending: 0, failed_names: [] })).toBe('прошли 2');
  });
});

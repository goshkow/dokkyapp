import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RequestPanel } from './ComposerParts';

const respond = vi.hoisted(() => vi.fn());
vi.mock('../../state/chat', () => ({ changeChatModel: vi.fn() }));
vi.mock('../../state/chatCore', () => ({ useChat: (select: (state: unknown) => unknown) => select({ requests: [{ id: 7, kind: 'approval', payload: { name: 'file.create', arguments: { path: 'src/main.ts', content: 'private file body' } } }] }) }));
vi.mock('../../state/chatAgent', () => ({ respondRequest: respond }));
vi.mock('../../state/overlay', () => ({ guarded: (callback: unknown) => callback }));
vi.mock('../../state/actions', () => ({ openFolder: vi.fn() }));
vi.mock('../../state/queue', () => ({ editQueueItem: vi.fn(), removeQueueItem: vi.fn(), resumeQueue: vi.fn(), runQueueItemNow: vi.fn() }));
vi.mock('../../state/skills', () => ({ attachSkill: vi.fn(), detachSkill: vi.fn(), groupSkills: vi.fn(), loadSkills: vi.fn(), useSkills: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('подтверждение действия', () => {
  it('показывает отклонение слева, разрешение справа и раскрывает детали Lucide стрелкой', () => {
    const { container } = render(<RequestPanel />);
    const buttons = [...container.querySelectorAll('.approval-panel .actions button')];
    expect(buttons.map((button) => button.textContent)).toEqual(['Отклонить', 'Разрешить']);
    const details = container.querySelector('details')!;
    expect(details.open).toBe(false);
    expect(container.querySelector('summary svg.lucide-chevron-right')).toBeTruthy();
    fireEvent.click(screen.getByText('Что будет сделано'));
    details.open = true;
    fireEvent(details, new Event('toggle'));
    expect(container.querySelector('summary svg.lucide-chevron-down')).toBeTruthy();
    expect(screen.queryByText('private file body')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Отклонить' }));
    expect(respond).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }), { approved: false });
    fireEvent.click(screen.getByRole('button', { name: 'Разрешить' }));
    expect(respond).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }), { approved: true });
  });
});

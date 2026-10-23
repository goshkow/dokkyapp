import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PlanList } from './Plan';

vi.mock('../../state/chat', () => ({ executePlan: vi.fn() }));
vi.mock('../../state/workspaceRemote', () => ({ selectInspector: vi.fn() }));
vi.mock('./Markdown', () => ({ Markdown: () => null }));
afterEach(cleanup);

describe('индикаторы плана', () => {
  it.each(['cancelled', 'failed', 'interrupted'])('показывает статичную Lucide иконку при %s', (status) => {
    const { container } = render(<PlanList steps={[{ title: 'Готово', status: 'completed' }, { title: 'Работа', status: 'running' }]} status={status} />);
    expect(container.querySelector('li.running')).toBeNull();
    expect(container.querySelector('li.completed svg.lucide-circle-check')).toBeTruthy();
    expect(container.querySelector(`li.${status} svg.plan-status`)).toBeTruthy();
    expect(container.querySelector('[aria-current]')).toBeNull();
  });
  it('снова показывает активный пункт при фактическом выполнении', () => {
    render(<PlanList steps={[{ title: 'Работа', status: 'running' }]} status="running" />);
    expect(screen.getByLabelText('Выполняется: Работа').getAttribute('aria-current')).toBe('step');
  });
});

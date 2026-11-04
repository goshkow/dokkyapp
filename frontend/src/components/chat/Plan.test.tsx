import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PlanStep } from '../../state/chatTypes';
import { LivePlan, PlanApproval, PlanList } from './Plan';

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

describe('план в ленте', () => {
  const steps: PlanStep[] = [{ title: 'Изучить', status: 'completed' }, { title: 'Внести правки', status: 'running' }, { title: 'Проверить', status: 'pending' }];
  // в jsdom нет matchMedia, а блоку без анимаций он и не нужен
  beforeEach(() => { document.documentElement.dataset.motion = 'reduced'; });
  afterEach(() => { delete document.documentElement.dataset.motion; });

  it('показывает одной строкой текущий шаг и счётчик, список только по щелчку', () => {
    const { container } = render(<LivePlan steps={steps} />);
    expect(container.querySelector('.live-plan-current')?.textContent).toBe('Внести правки');
    expect(screen.getByRole('status').textContent).toBe('1 из 3');
    expect(container.querySelector('.plan-list')).toBeNull();
    fireEvent.click(container.querySelector('summary') as Element);
    expect(container.querySelectorAll('.plan-list li').length).toBe(3);
  });
  it('говорит, что план выполнен, и отмечает остановленный', () => {
    const { container, rerender } = render(<LivePlan steps={[{ title: 'Изучить', status: 'completed' }]} />);
    expect(container.querySelector('.live-plan-current')?.textContent).toBe('План выполнен');
    expect(container.querySelector('#live-plan')?.hasAttribute('data-stopped')).toBe(false);
    rerender(<LivePlan steps={[{ title: 'Работа', status: 'running' }]} snapshot={{ tracking_status: 'failed' }} />);
    expect(container.querySelector('.live-plan-current')?.textContent).toBe('Работа');
    expect(container.querySelector('#live-plan')?.hasAttribute('data-stopped')).toBe(true);
  });
  it('документ плана свёрнут, заголовок и действия в одной строке', () => {
    const { container } = render(<PlanApproval snapshot={{ ready: true, document: '# Новый экран\n\nТекст', steps }} />);
    expect(container.querySelector('.plan-bar strong')?.textContent).toBe('Новый экран');
    expect(container.querySelector('.plan-bar small')?.textContent).toBe('3 шага');
    expect(container.querySelector('#plan-approval')?.hasAttribute('data-open')).toBe(false);
    expect(container.querySelector('#plan-document-content')?.hasAttribute('inert')).toBe(true);
    for (const name of ['Скачать план', 'Открыть в панели', 'Реализуй план']) expect(screen.getByRole('button', { name })).toBeTruthy();
  });
  it('пока план не готов, кнопки запуска нет, а строка говорит, что с ним', () => {
    const { container } = render(<PlanApproval snapshot={{ executed: true, document: '# План' }} />);
    expect(screen.queryByRole('button', { name: 'Реализуй план' })).toBeNull();
    expect(container.querySelector('.plan-bar small')?.textContent).toBe('Принят к выполнению');
  });
});

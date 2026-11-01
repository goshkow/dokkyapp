import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { actionText } from '../../lib/journal';
import { chat } from '../../state/chatCore';
import { clearSubagents, trackSubagent } from '../../state/subagents';
import { SubagentLive } from './Journal';

afterEach(() => { cleanup(); clearSubagents(); chat.set({ running: false, turnEnded: false }); });

describe('сабагент в ленте', () => {
  it('показывает текущее действие и число шагов, пока ход идёт', () => {
    chat.set({ running: true, turnEnded: false });
    render(<SubagentLive id="d1" />);
    expect(screen.getByText('Начинаю')).toBeTruthy();
    act(() => {
      trackSubagent({ call_id: 'd1', event: 'tool', name: 'file.read', arguments: { path: 'src/app.ts' } });
      trackSubagent({ call_id: 'd1', event: 'tool', name: 'file.read', arguments: { path: 'src/main.ts' } });
    });
    expect(screen.getByText(/Читаю src\/main\.ts/).textContent).toContain('шагов: 2');
  });

  it('после хода строки нет', () => {
    chat.set({ running: false, turnEnded: true });
    const { container } = render(<SubagentLive id="d1" />);
    expect(container.innerHTML).toBe('');
  });

  it('подпись называет агента и задачу', () => {
    const data = { name: 'task.delegate', arguments: { agent: 'explore', title: 'где вход' } };
    expect(actionText({ event: 'tool.call', data })).toBe('Агент explore · где вход');
    expect(actionText({ event: 'tool.call', data: { name: 'task.delegate', arguments: { title: 'правка' } } })).toBe('Агент general · правка');
  });
});

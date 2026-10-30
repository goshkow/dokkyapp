import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AgentQuestions } from './AgentQuestions';
import type { AgentRequest } from '../../state/chatTypes';

const respond = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));
vi.mock('../../state/chatAgent', () => ({ respondRequest: respond }));

afterEach(() => { cleanup(); respond.mockClear(); });

const request: AgentRequest = {
  id: 9,
  kind: 'question',
  payload: {
    question: 'Где?\nКакие ОС?',
    questions: [
      { question: 'Где ошибка?', header: 'Экран', options: [{ label: 'Лента' }, { label: 'Поле', description: 'внизу' }] },
      { question: 'Какие ОС?', options: [{ label: 'Windows' }, { label: 'macOS' }], multi_select: true },
    ],
  },
};

describe('вопросы агента', () => {
  it('несколько вопросов: ответить можно, только когда отвечены все', async () => {
    render(<AgentQuestions request={request} />);
    const submit = screen.getByRole('button', { name: 'Ответить' });
    expect(screen.getByText('Ответьте на каждый вопрос')).toBeTruthy();
    fireEvent.click(screen.getByRole('radio', { name: /Поле/ }));
    // в одиночном вопросе второй вариант заменяет первый
    fireEvent.click(screen.getByRole('radio', { name: /Лента/ }));
    expect(screen.getByRole('radio', { name: /Поле/ }).getAttribute('aria-checked')).toBe('false');
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Windows' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'macOS' }));
    fireEvent.change(screen.getByLabelText('Свой ответ: Какие ОС?'), { target: { value: '  и Linux ' } });
    fireEvent.click(submit);
    await waitFor(() => expect(respond).toHaveBeenCalledTimes(1));
    expect(respond.mock.calls[0]?.[1]).toEqual({ answers: [
      { selected: ['Лента'], text: '' },
      { selected: ['Windows', 'macOS'], text: 'и Linux' },
    ] });
  });

  it('старый одиночный вопрос - многострочное поле, Enter отправляет, Shift+Enter переносит строку', async () => {
    render(<AgentQuestions request={{ id: 3, kind: 'question', payload: { question: 'Какой порт?' } }} />);
    const field = screen.getByLabelText('Ответ: Какой порт?');
    expect(field.tagName).toBe('TEXTAREA');
    fireEvent.change(field, { target: { value: '8080' } });
    fireEvent.keyDown(field, { key: 'Enter', shiftKey: true });
    expect(respond).not.toHaveBeenCalled();
    fireEvent.keyDown(field, { key: 'Enter' });
    await waitFor(() => expect(respond).toHaveBeenCalledWith(expect.objectContaining({ id: 3 }), { answers: [{ selected: [], text: '8080' }] }));
  });
});

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
  it('пошагово: автопереход в одиночном выборе, множественный по «Далее», все ответы разом', async () => {
    render(<AgentQuestions request={request} />);
    expect(screen.getByText('1 из 2')).toBeTruthy();
    expect(screen.queryByRole('checkbox', { name: 'Windows' })).toBeNull();
    expect((screen.getByRole('button', { name: 'Далее' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('radio', { name: /Лента/ }));
    // выбор сразу открыл второй вопрос
    expect(screen.getByText('2 из 2')).toBeTruthy();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Windows' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'macOS' }));
    expect(screen.getByText('2 из 2')).toBeTruthy();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Другое' }));
    fireEvent.change(screen.getByLabelText('Свой ответ: Какие ОС?'), { target: { value: '  и Linux ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Ответить' }));
    await waitFor(() => expect(respond).toHaveBeenCalledTimes(1));
    expect(respond.mock.calls[0]?.[1]).toEqual({ answers: [
      { selected: ['Лента'], text: '' },
      { selected: ['Windows', 'macOS'], text: 'и Linux' },
    ] });
  });

  it('«Назад» и вкладки возвращают к вопросу, «Другое» в одиночном выборе снимает вариант', async () => {
    render(<AgentQuestions request={request} />);
    fireEvent.click(screen.getByRole('radio', { name: /Поле/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Назад' }));
    expect(screen.getByText('1 из 2')).toBeTruthy();
    expect(screen.getByRole('radio', { name: /Поле/ }).getAttribute('aria-checked')).toBe('true');
    fireEvent.click(screen.getByRole('radio', { name: 'Другое' }));
    expect(screen.getByRole('radio', { name: /Поле/ }).getAttribute('aria-checked')).toBe('false');
    fireEvent.change(screen.getByLabelText('Свой ответ: Где ошибка?'), { target: { value: 'в меню' } });
    fireEvent.click(screen.getByRole('button', { name: /Вопрос 2/ }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'macOS' }));
    fireEvent.click(screen.getByRole('button', { name: 'Ответить' }));
    await waitFor(() => expect(respond).toHaveBeenCalledTimes(1));
    expect(respond.mock.calls[0]?.[1]).toEqual({ answers: [
      { selected: [], text: 'в меню' },
      { selected: ['macOS'], text: '' },
    ] });
  });

  it('клавиши: цифра выбирает вариант, стрелки и Enter листают вопросы', async () => {
    render(<AgentQuestions request={request} />);
    fireEvent.keyDown(screen.getByRole('radio', { name: /Лента/ }), { key: '2' });
    expect(screen.getByText('2 из 2')).toBeTruthy();
    fireEvent.keyDown(screen.getByRole('checkbox', { name: 'macOS' }), { key: '2' });
    expect(screen.getByRole('checkbox', { name: 'macOS' }).getAttribute('aria-checked')).toBe('true');
    fireEvent.keyDown(screen.getByRole('checkbox', { name: 'macOS' }), { key: 'ArrowLeft' });
    expect(screen.getByText('1 из 2')).toBeTruthy();
    fireEvent.keyDown(screen.getByRole('radio', { name: /Поле/ }), { key: 'ArrowRight' });
    fireEvent.keyDown(screen.getByRole('group', { name: 'Вопросы' }), { key: 'Enter' });
    await waitFor(() => expect(respond).toHaveBeenCalledTimes(1));
    expect(respond.mock.calls[0]?.[1]).toEqual({ answers: [
      { selected: ['Поле'], text: '' },
      { selected: ['macOS'], text: '' },
    ] });
  });

  it('один вопрос без вкладок, у вопроса без вариантов сразу поле ответа', () => {
    render(<AgentQuestions request={{ id: 4, kind: 'question', payload: { questions: [{ question: 'Что делать?', options: [{ label: 'А' }] }] } }} />);
    expect(screen.queryByRole('group', { name: 'Вопросы' })).toBeNull();
    expect(screen.queryByLabelText('Свой ответ: Что делать?')).toBeNull();
    expect(screen.getByRole('button', { name: 'Ответить' })).toBeTruthy();
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

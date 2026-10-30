import { beforeEach, describe, expect, it } from 'vitest';
import type { Chat } from '../lib/types';
import { chatActivity, markSeen, onQuestion, onRunState } from './chatActivity';
import { data } from './data';
import { ui } from './ui';
import { view } from './view';

const chat = (id: number, activity: Chat['activity'] = null): Chat => ({ id, title: `Чат ${id}`, activity });

describe('состояние чатов в меню', () => {
  beforeEach(() => {
    localStorage.clear();
    ui.set({ scope: 'development' });
    view.set({ chat: null });
    chatActivity.set({ activity: {}, done: {} });
    data.set({ localChats: [] });
  });

  it('работа, вопрос и синяя точка после конца хода в закрытом чате', () => {
    onRunState(5, true);
    expect(chatActivity.get().activity['5']).toBe('working');
    onQuestion(5, true);
    expect(chatActivity.get().activity['5']).toBe('question');
    // конец хода не снимает вопрос, пока на него не ответили
    onRunState(5, true);
    expect(chatActivity.get().activity['5']).toBe('question');
    onQuestion(5, false);
    expect(chatActivity.get().activity['5']).toBe('working');
    onRunState(5, false);
    expect(chatActivity.get().activity['5']).toBeUndefined();
    expect(chatActivity.get().done['5']).toBe(true);
    markSeen(5);
    expect(chatActivity.get().done['5']).toBeUndefined();
  });

  it('открытый чат не получает отметку «готово»', () => {
    view.set({ chat: chat(7) });
    onRunState(7, true);
    onRunState(7, false);
    expect(chatActivity.get().done['7']).toBeUndefined();
  });

  it('список с сервера снимает зависшую работу и забытые чаты', () => {
    data.set({ localChats: [chat(1, 'working'), chat(2, 'question')] });
    expect(chatActivity.get().activity).toEqual({ 1: 'working', 2: 'question' });
    data.set({ localChats: [chat(1)] });
    expect(chatActivity.get().activity).toEqual({});
    // чат 2 исчез из списка - его отметка тоже не нужна
    expect(chatActivity.get().done).toEqual({ 1: true });
  });

  it('открытие чата снимает синюю точку', () => {
    onRunState(3, true);
    onRunState(3, false);
    expect(chatActivity.get().done['3']).toBe(true);
    view.set({ chat: chat(3) });
    expect(chatActivity.get().done['3']).toBeUndefined();
  });
});

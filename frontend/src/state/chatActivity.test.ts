import { beforeEach, describe, expect, it, vi } from 'vitest';

const network = vi.hoisted(() => ({ post: vi.fn(() => Promise.resolve({ ok: true })) }));
vi.mock('../lib/api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../lib/api')>()), post: network.post }));

import { announceAgentNotice } from '../lib/bridge';
import type { Chat } from '../lib/types';
import { chatActivity, markSeen, onAnswered, onQuestion, onRunState } from './chatActivity';
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
    network.post.mockClear();
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

  it('ответ в окне сразу снимает оранжевую точку, если других вопросов нет', () => {
    onRunState(8, true);
    onQuestion(8, true, 'approval');
    onAnswered(8, 1);
    expect(chatActivity.get().activity['8']).toBe('question');
    onAnswered(8, 0);
    expect(chatActivity.get().activity['8']).toBe('working');
  });

  it('список, запрошенный до ответа, не возвращает точку; новый вопрос - возвращает', () => {
    onRunState(9, true);
    onQuestion(9, true);
    onQuestion(9, false);
    data.set({ localChats: [chat(9, 'question')] });
    expect(chatActivity.get().activity['9']).toBe('working');
    onQuestion(9, true);
    data.set({ localChats: [chat(9, 'question'), chat(10)] });
    expect(chatActivity.get().activity['9']).toBe('question');
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

  it('конец хода и вопрос агента уходят хосту уведомлением, сверка со списком - нет', () => {
    const agentNotice = vi.fn();
    window.koddiWindowBridge = { agentNotice } as unknown as typeof window.koddiWindowBridge;
    data.set({ localChats: [chat(6)] });
    onRunState(6, true);
    onQuestion(6, true);
    onQuestion(6, false);
    onRunState(6, false);
    expect(agentNotice.mock.calls.map(([payload]) => JSON.parse(payload as string))).toEqual([
      { chat_id: 6, kind: 'question', title: 'Чат 6' },
      { chat_id: 6, kind: 'done', title: 'Чат 6' },
    ]);
    // перезапуск окна: зависший ход снимает список сервера, о нём не уведомляем
    agentNotice.mockClear();
    data.set({ localChats: [chat(6, 'working')] });
    data.set({ localChats: [chat(6)] });
    expect(agentNotice).not.toHaveBeenCalled();
    // выключатель в настройках
    data.set({ settings: { ...data.get().settings, agent_notifications: false } });
    onRunState(6, true);
    onRunState(6, false);
    expect(agentNotice).not.toHaveBeenCalled();
    data.set({ settings: { ...data.get().settings, agent_notifications: true } });
    delete window.koddiWindowBridge;
  });

  it('запрос подтверждения уходит как approval, вопрос - как question', () => {
    const agentNotice = vi.fn();
    window.koddiWindowBridge = { agentNotice } as unknown as typeof window.koddiWindowBridge;
    data.set({ localChats: [chat(8)] });
    onRunState(8, true);
    onQuestion(8, true, 'approval');
    // меню у обоих одно: ждёт человека
    expect(chatActivity.get().activity['8']).toBe('question');
    onQuestion(8, false);
    onQuestion(8, true, 'question');
    onQuestion(8, false);
    onQuestion(8, true, 'что-то новое');
    expect(agentNotice.mock.calls.map(([payload]) => JSON.parse(payload as string).kind)).toEqual(['approval', 'question', 'question']);
    delete window.koddiWindowBridge;
  });

  it('stalled и limit из плашки идут тем же мостом и уважают настройку', () => {
    const agentNotice = vi.fn();
    window.koddiWindowBridge = { agentNotice } as unknown as typeof window.koddiWindowBridge;
    announceAgentNotice(2, 'stalled', 'Чат');
    announceAgentNotice(2, 'limit', 'Чат');
    expect(agentNotice.mock.calls.map(([payload]) => JSON.parse(payload as string).kind)).toEqual(['stalled', 'limit']);
    agentNotice.mockClear();
    data.set({ settings: { ...data.get().settings, agent_notifications: false } });
    announceAgentNotice(2, 'limit', 'Чат');
    expect(agentNotice).not.toHaveBeenCalled();
    data.set({ settings: { ...data.get().settings, agent_notifications: true } });
    delete window.koddiWindowBridge;
  });

  it('прочитанное в окне уходит на сервер, а прочитанное на телефоне снимает точку', () => {
    view.set({ chat: chat(4) });
    expect(network.post).toHaveBeenCalledWith('/api/chats/4/seen');
    // ход закончился на глазах: ответ прочитан сразу
    network.post.mockClear();
    onRunState(4, true);
    onRunState(4, false);
    expect(network.post).toHaveBeenCalledWith('/api/chats/4/seen');
    // в закрытом чате точка остаётся до прочтения где угодно, в том числе на телефоне (событие chat.read)
    view.set({ chat: null });
    network.post.mockClear();
    onRunState(9, true);
    onRunState(9, false);
    expect(network.post).not.toHaveBeenCalled();
    markSeen(9);
    expect(chatActivity.get().done['9']).toBeUndefined();
  });

  it('окно в фоне не читает чат за человека: прочтение - когда окно снова впереди', () => {
    window.koddiWindowActive!(false);
    view.set({ chat: chat(6) });
    onRunState(6, true);
    onRunState(6, false);
    expect(network.post).not.toHaveBeenCalled();
    expect(chatActivity.get().done['6']).toBe(true);
    window.koddiWindowActive!(true);
    expect(network.post).toHaveBeenCalledWith('/api/chats/6/seen');
    expect(chatActivity.get().done['6']).toBeUndefined();
  });
});

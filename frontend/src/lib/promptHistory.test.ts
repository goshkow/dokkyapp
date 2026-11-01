import { beforeEach, describe, expect, it } from 'vitest';
import { forgetPrompts, promptHistory, rememberPrompt, stepHistory } from './promptHistory';

describe('история запросов поля ввода', () => {
  beforeEach(() => localStorage.clear());

  it('помнит последние запросы без повторов и пустых', () => {
    rememberPrompt('development', 'первый');
    rememberPrompt('development', '  ');
    rememberPrompt('development', 'второй');
    rememberPrompt('development', 'первый');
    expect(promptHistory('development')).toEqual(['второй', 'первый']);
    for (let index = 0; index < 60; index += 1) rememberPrompt('development', `запрос ${index}`);
    expect(promptHistory('development')).toHaveLength(50);
    expect(promptHistory('development').at(-1)).toBe('запрос 59');
    localStorage.setItem('dokky.prompt-history.development', '{"битое": true}');
    expect(promptHistory('development')).toEqual([]);
  });

  it('у каждого режима своя история, выход из аккаунта стирает все', () => {
    rememberPrompt('development', 'почини тесты');
    rememberPrompt('search', 'погода');
    rememberPrompt('chat', 'привет');
    expect(promptHistory('search')).toEqual(['погода']);
    expect(promptHistory('development')).toEqual(['почини тесты']);
    forgetPrompts();
    expect([promptHistory('development'), promptHistory('chat'), promptHistory('search')]).toEqual([[], [], []]);
  });

  it('стрелки идут от свежего к старому и возвращают пустое поле', () => {
    const list = ['a', 'b', 'c'];
    expect(stepHistory(list, null, -1)).toEqual({ index: 2, text: 'c' });
    expect(stepHistory(list, 2, -1)).toEqual({ index: 1, text: 'b' });
    expect(stepHistory(list, 0, -1)).toBeNull();
    expect(stepHistory(list, 1, 1)).toEqual({ index: 2, text: 'c' });
    expect(stepHistory(list, 2, 1)).toEqual({ index: null, text: '' });
    expect(stepHistory(list, null, 1)).toBeNull();
    expect(stepHistory([], null, -1)).toBeNull();
  });
});

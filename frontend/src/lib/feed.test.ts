import { describe, expect, it } from 'vitest';
import { buildFeed, projectVariants, type FeedInput } from './feed';
import type { Message } from './journal';

const base: FeedInput = {
  development: false, chatId: 'chat_1', messages: [], optimistic: null, live: [], alias: {}, sent: {}, selectedVariants: new Map(),
  plan: null, running: false, deltaSeen: false, runId: null, cloudWork: false, contextOperation: '',
};
const user = (id: string, content = 'q'): Message => ({ id, role: 'user', content });
const answer = (id: string, content = 'a', extra: Partial<Message> = {}): Message => ({ id, role: 'assistant', content, ...extra });

describe('feed', () => {
  it('сохраняет порядок и пропускает пустые сообщения', () => {
    const items = buildFeed({ ...base, messages: [user('1'), answer('2', ''), answer('3')] });
    expect(items.map((item) => item.key)).toEqual(['m:1', 'm:3']);
  });

  it('временное сообщение отправителя стоит в конце, а сообщение сервера занимает его ключ', () => {
    const optimistic = user('pending-user', 'привет');
    expect(buildFeed({ ...base, messages: [answer('2')], optimistic }).at(-1)?.key).toBe('m:pending-user');
    const items = buildFeed({ ...base, messages: [answer('2'), user('5', 'привет')], alias: { 5: 'm:pending-user' } });
    expect(items.map((item) => item.key)).toEqual(['m:2', 'm:pending-user']);
  });

  it('варианты ответа Докки сворачиваются в выбранный', () => {
    const messages = [user('u'), answer('a1', 'один'), answer('a2', 'два', { branch_of: 'a1' })];
    const chosen = projectVariants(messages, 'c', new Map());
    expect(chosen.map((item) => item.id)).toEqual(['u', 'a2']);
    expect(chosen[1]?.metadata?.cloud_variant).toMatchObject({ index: 1, ids: ['a1', 'a2'] });
    expect(chosen[1]?.content).toBe('два');
    expect(projectVariants(messages, 'c', new Map([['c:a1', 'a1']]))[1]?.content).toBe('один');
  });

  it('в разработке показывает ожидание, пока нет ни ответа, ни текста', () => {
    const messages = [user('1')];
    expect(buildFeed({ ...base, development: true, messages, running: true }).at(-1)?.kind).toBe('waiting');
    expect(buildFeed({ ...base, development: true, messages, running: true, deltaSeen: true }).some((item) => item.kind === 'waiting')).toBe(false);
  });

  it('вставляет план после журнала своего хода', () => {
    const turn = { id: 7, status: 'completed' };
    const messages: Message[] = [
      user('1'),
      { id: 2, role: 'assistant', content: '', metadata: { kind: 'activity', channel: 'commentary', status: 'completed', turn_id: 7, turn, events: [{ event: 'tool.call', data: { id: 'a', name: 'file.read' } }] } },
      answer('3', 'итог', { metadata: { channel: 'final', status: 'completed', turn_id: 7, turn } }),
    ];
    const items = buildFeed({
      ...base, development: true, messages, plan: { turn_id: 7, tracking_steps: [{ title: 'Шаг', status: 'running' }], document: '# План' },
    });
    expect(items.map((item) => item.kind)).toEqual(['message', 'journal', 'message', 'plan-approval', 'live-plan']);
  });

  it('вложения отправленного сообщения возвращаются в ленту', () => {
    const items = buildFeed({ ...base, messages: [user('5', 'файл')], sent: { 5: [{ id: 1, name: 'a.txt' }] } });
    expect(items[0]?.kind === 'message' && items[0].message.attachments).toHaveLength(1);
  });
});

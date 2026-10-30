/* Из сообщений сервера и временных узлов собирается список элементов ленты. Чистая функция: порядок, ключи
   и вставки плана считаются здесь, а React только сопоставляет ключи и не пересоздаёт готовые узлы. */
import type { Id } from './types';
import { projectLocalJournal, type Message } from './journal';
import type { PlanSnapshot, PlanStep } from '../state/chatTypes';

export type FeedItem =
  | { kind: 'message'; key: string; message: Message }
  | { kind: 'journal'; key: string; message: Message; turnId: string }
  | { kind: 'plan-approval'; key: string; snapshot: PlanSnapshot }
  | { kind: 'live-plan'; key: string; steps: PlanStep[]; snapshot: PlanSnapshot }
  | { kind: 'cloud-work'; key: string }
  | { kind: 'waiting'; key: string }
  | { kind: 'context'; key: string; text: string };

export interface FeedInput {
  development: boolean;
  chatId: Id | null;
  messages: Message[];
  optimistic: Message | null;
  live: Message[];
  alias: Record<string, string>;
  sent: Record<string, unknown[]>;
  selectedVariants: ReadonlyMap<string, string>;
  plan: PlanSnapshot | null;
  running: boolean;
  deltaSeen: boolean;
  runId: string | null;
  cloudWork: boolean;
  contextOperation: string;
}

/* варианты ответа (облако) */
function variantRoot(message: Message, byId: Map<string, Message>): string {
  let current = message;
  const visited = new Set<unknown>();
  while (current.branch_of && byId.has(String(current.branch_of)) && !visited.has(current.id)) {
    visited.add(current.id);
    current = byId.get(String(current.branch_of)) as Message;
  }
  return String(current.id);
}

export function projectVariants(messages: Message[], chatId: Id | null, selected: ReadonlyMap<string, string>): Message[] {
  const byId = new Map(messages.map((message) => [String(message.id), message]));
  const groups = new Map<string, Message[]>();
  for (const message of messages) {
    if (message.role !== 'assistant') continue;
    const root = variantRoot(message, byId);
    groups.set(root, [...(groups.get(root) ?? []), message]);
  }
  const result: Message[] = [];
  for (const message of messages) {
    const root = variantRoot(message, byId);
    const group = groups.get(root);
    if (message.role !== 'assistant' || !group || group.length < 2) {
      result.push(message);
      continue;
    }
    if (String(message.id) !== root) continue;
    const key = `${chatId}:${root}`;
    const choice = selected.get(key);
    const index = choice ? Math.max(0, group.findIndex((item) => String(item.id) === choice)) : group.length - 1;
    const chosen = group[index] as Message;
    result.push({ ...chosen, metadata: { ...chosen.metadata, cloud_variant: { root, key, index, ids: group.map((item) => String(item.id)) } } });
  }
  return result;
}

export const variantRootOf = variantRoot;

/* сборка */
const empty = (message: Message): boolean =>
  !message.content &&
  !message.metadata?.events?.length &&
  !message.attachments?.length &&
  !message.artifacts?.length &&
  !message.activity?.length &&
  message.metadata?.kind !== 'journal';

const turnOf = (item: FeedItem): string => {
  if (item.kind === 'journal') return item.turnId;
  if (item.kind === 'message') return String(item.message.metadata?.turn_id ?? '');
  return '';
};

export function buildFeed(input: FeedInput): FeedItem[] {
  let messages = input.messages;
  const known = new Set(messages.map((message) => String(message.id)));
  messages = [...messages, ...input.live.filter((message) => !known.has(String(message.id)))];
  if (!input.development) messages = projectVariants(messages, input.chatId, input.selectedVariants);
  else messages = projectLocalJournal(messages);

  const grouped: Message[] = [];
  for (const raw of messages) {
    const message = !raw.attachments?.length && input.sent[String(raw.id)] ? { ...raw, attachments: input.sent[String(raw.id)] } : raw;
    if (empty(message)) continue;
    const previous = grouped.at(-1);
    if (
      !message.content && message.metadata?.events?.length && previous && !previous.content && previous.metadata?.events?.length
    ) {
      previous.metadata = { ...message.metadata, events: [...(previous.metadata?.events ?? []), ...(message.metadata?.events ?? [])] };
    } else grouped.push({ ...message, metadata: { ...message.metadata } });
  }

  const items: FeedItem[] = [];
  // повтор ключа React не прощает: узлы с одинаковым ключом остаются в ленте навсегда, даже на других страницах
  const used = new Set<string>();
  for (const message of grouped) {
    const id = String(message.id);
    const aliased = input.alias[id];
    let key = aliased && !used.has(aliased) ? aliased : `m:${id}`;
    // повтор id с сервера или журнал двух сбросов после одного вопроса: свой ключ у каждой записи
    for (let n = 2; used.has(key); n += 1) key = `m:${id}#${n}`;
    used.add(key);
    if (message.metadata?.kind === 'journal') items.push({ kind: 'journal', key, message, turnId: String(message.metadata.turn?.id ?? '') });
    else items.push({ kind: 'message', key, message });
  }
  if (input.optimistic && !used.has(`m:${input.optimistic.id}`)) items.push({ kind: 'message', key: `m:${input.optimistic.id}`, message: input.optimistic });

  // план стоит сразу после журнала хода и не прыгает за новыми ответами
  const snapshot = input.plan;
  if (input.development && snapshot) {
    const turn = String(snapshot.turn_id ?? '');
    const indexes = items.map((item, index) => (turn && turnOf(item) === turn ? index : -1)).filter((index) => index >= 0);
    let approval: FeedItem | null = null;
    if (snapshot.document) approval = { kind: 'plan-approval', key: `plan:${input.chatId}:${turn}`, snapshot };
    const steps = (snapshot.tracking_steps ?? snapshot.steps ?? []) as PlanStep[];
    const live: FeedItem | null = steps.length ? { kind: 'live-plan', key: 'live-plan', steps, snapshot } : null;
    const last = indexes.at(-1);
    const journal = indexes.find((index) => items[index]?.kind === 'journal');
    let at = last == null ? items.length : last + 1;
    if (approval) {
      items.splice(at, 0, approval);
      at += 1;
    }
    if (live) {
      const anchor = approval ? at : journal != null ? journal + 1 : at;
      items.splice(Math.min(anchor, items.length), 0, live);
    }
  }

  if (!input.development && input.cloudWork) {
    const pending = input.runId ? items.findIndex((item) => item.kind === 'message' && String(item.message.id) === `pending_${input.runId}`) : -1;
    const work: FeedItem = { kind: 'cloud-work', key: 'cloud-work' };
    if (pending >= 0) items.splice(pending, 0, work);
    else items.push(work);
  }

  if (input.development && input.running && !input.deltaSeen) {
    const lastUser = items.findLastIndex((item) => item.kind === 'message' && item.message.role === 'user');
    const answered = items.slice(lastUser + 1).some((item) => item.kind === 'journal' || (item.kind === 'message' && item.message.role === 'assistant'));
    if (!answered) items.push({ kind: 'waiting', key: 'waiting' });
  }
  if (input.contextOperation) items.push({ kind: 'context', key: 'context-operation', text: input.contextOperation });
  return items;
}

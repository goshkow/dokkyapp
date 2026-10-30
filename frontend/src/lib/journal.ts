/* Журнал работы агента: чистые функции. Публичные сообщения и действия хода собираются в этапы «Работал…». */

import { serverTime } from './format';

export interface ToolEventData {
  id?: string | number;
  name?: string;
  display_name?: string;
  arguments?: Record<string, unknown>;
  args?: Record<string, unknown>;
  result?: Record<string, unknown> & { ok?: boolean; error?: string; reason?: string; path?: string };
  sequence?: number | string;
  created_at?: string;
  channel?: string;
  text?: string;
}

export interface JournalEvent {
  id?: string | number;
  event?: string;
  kind?: string;
  data?: ToolEventData;
  payload?: ToolEventData;
  sequence?: number | string;
  created_at?: string;
}

export interface TurnInfo {
  id?: number | string;
  status?: string;
  created_at?: string;
  completed_at?: string;
  summary?: string;
  timeline?: JournalEvent[];
}

export interface MessageMeta {
  status?: string;
  channel?: string;
  kind?: string;
  turn?: TurnInfo;
  turn_id?: number | string;
  events?: JournalEvent[];
  sequence?: number | string;
  started_at?: string;
  created_at?: string;
  completed_at?: string;
  provisional?: boolean;
  work_items?: Message[];
  messages?: Message[];
  live_text?: boolean;
  /** `remote` - сообщение отправлено из Докки на телефоне. */
  origin?: string;
  // облачные поля
  cloud_activity?: unknown[];
  cloud_citations?: unknown[];
  cloud_artifacts?: unknown[];
  cloud_attachments?: unknown[];
  cloud_version?: number;
  cloud_variant?: { root: string; key: string; index: number; ids: string[] };
  [key: string]: unknown;
}

export interface Message {
  id: string | number;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  created_at?: string;
  metadata?: MessageMeta;
  attachments?: unknown[];
  artifacts?: unknown[];
  activity?: unknown[];
  citations?: unknown[];
  version?: number;
  status?: string;
  branch_of?: string | number | null;
  /** Картинки, уже показанные в тексте ответа: во вложениях их не повторяем. */
  _richUsed?: Set<string>;
}

export interface JournalEntry {
  key: string;
  type: 'action' | 'commentary' | 'final' | 'status' | 'compaction';
  sequence: number | null;
  created_at?: string;
  updated_at?: string;
  updated_sequence?: number | null;
  completed_at?: string;
  text?: string;
  running?: boolean;
  complete?: boolean;
  event?: { event: string; data: ToolEventData };
  order: number;
  position?: number | null;
}

export interface JournalBlock {
  key: string;
  type: 'segment' | 'commentary' | 'compaction' | 'final' | 'action' | 'status';
  entries: JournalEntry[];
  created_at?: string;
  end?: string;
  running?: boolean;
  text?: string;
  event?: JournalEntry['event'];
}

/** Длительность в виде «6 мин. 13 сек.». */
export function duration(milliseconds: number): string {
  const seconds = Math.max(1, Math.round(milliseconds / 1000));
  return seconds < 60 ? `${seconds} сек.` : `${Math.floor(seconds / 60)} мин. ${seconds % 60} сек.`;
}

const publicMessage = (message: Message): boolean => {
  const metadata = message.metadata ?? {};
  return (!message.role || message.role === 'assistant') &&
    (!metadata.channel || ['commentary', 'final'].includes(metadata.channel)) &&
    !['analysis', 'reasoning', 'thinking'].includes(String(metadata.kind));
};

/** Собирает журнал хода из сообщений ассистента: работа отдельно, ответы отдельно. */
export function projectLocalJournal(messages: Message[]): Message[] {
  const result: Message[] = [];
  let pending: Message[] = [];
  let user: Message | null = null;
  const flush = () => {
    if (!pending.length) return;
    const turns = pending.map((message) => message.metadata?.turn).filter((turn): turn is TurnInfo => Boolean(turn));
    const last = turns.at(-1);
    const turn: TurnInfo | null = last ? { ...last, timeline: [] } : null;
    if (turn) {
      const timeline = new Map<string, JournalEvent>();
      for (const value of turns) for (const event of value.timeline ?? []) timeline.set(String(event.id ?? `${event.sequence}:${event.kind}`), event);
      turn.timeline = [...timeline.values()];
    }
    const work: Message[] = [];
    const answers: Message[] = [];
    let hasWork = false;
    // текст идущего шага ещё не знает свой канал: пока у хода есть работа, он остаётся в журнале
    const provisional = (message: Message): Message =>
      message.content && message.metadata?.status === 'running' && !message.metadata?.channel
        ? { ...message, metadata: { ...message.metadata, channel: 'commentary', provisional: true } }
        : message;
    for (const message of pending) {
      if (!publicMessage(message)) continue;
      const metadata = message.metadata ?? {};
      if (metadata.channel === 'final') {
        answers.push(message);
        work.push(message);
      } else if (metadata.channel === 'commentary' || metadata.kind === 'activity' || metadata.events?.length) {
        work.push(provisional(message));
        hasWork = true;
      } else if (message.content && hasWork && provisional(message) !== message) work.push(provisional(message));
      else if (message.content) answers.push(message);
    }
    if (work.some((message) => message.metadata?.channel !== 'final' || message.metadata?.events?.length) || turn?.timeline?.length) {
      result.push({
        id: `journal-${turn?.id ?? pending[0]?.metadata?.turn_id ?? user?.id ?? pending[0]?.id}`,
        role: 'assistant',
        content: '',
        metadata: {
          kind: 'journal',
          work_items: work,
          turn: turn ?? undefined,
          // ответ уже печатается: общий статус «Думаю…» больше не нужен
          live_text: answers.some((message) => message.metadata?.status === 'running' && message.content),
          status: turn?.status ?? (work.some((message) => message.metadata?.status === 'running') ? 'running' : 'completed'),
        },
      });
    }
    result.push(...answers);
    pending = [];
  };
  for (const message of messages) {
    if (message.role === 'user' || message.metadata?.kind === 'journal') {
      flush();
      if (message.role === 'user') user = message;
      result.push(message);
    } else {
      const previous = pending.at(-1)?.metadata;
      const oldTurn = previous?.turn_id ?? previous?.turn?.id;
      const newTurn = message.metadata?.turn_id ?? message.metadata?.turn?.id;
      if (oldTurn != null && newTurn != null && oldTurn !== newTurn) flush();
      pending.push(message);
    }
  }
  flush();
  return result;
}

export const journalTime = (value: unknown): number => serverTime(value);

function journalMessages(metadata: MessageMeta): Message[] {
  const messages = new Map<unknown, Message>();
  for (const message of [...(metadata.work_items ?? []), ...(metadata.messages ?? [])]) {
    if (publicMessage(message)) messages.set(message.id ?? message, message);
  }
  return [...messages.values()];
}

function entryOrder(a: JournalEntry, b: JournalEntry, updated = false): number {
  const aSequence = updated && a.type === 'action' ? a.updated_sequence : a.sequence;
  const bSequence = updated && b.type === 'action' ? b.updated_sequence : b.sequence;
  if (aSequence != null && bSequence != null && aSequence !== bSequence) return aSequence - bSequence;
  const delta = journalTime(updated ? a.updated_at || a.created_at : a.created_at) - journalTime(updated ? b.updated_at || b.created_at : b.created_at);
  return Number.isFinite(delta) ? delta : 0;
}

const sequenceOf = (value: unknown): number | null => (value != null && Number.isFinite(Number(value)) ? Number(value) : null);

export function orderedJournalEntries(metadata: MessageMeta): JournalEntry[] {
  const entries = new Map<string, JournalEntry>();
  let order = 0;
  const add = (entry: Omit<JournalEntry, 'order'>) => {
    const previous = entries.get(entry.key);
    if (previous) {
      // результат обновляет строку вызова, но не переносит её через commentary
      const candidate = { ...entry, order: 0 } as JournalEntry;
      const first = entryOrder(candidate, previous) < 0 ? candidate : previous;
      const latest = entryOrder(candidate, previous, true) < 0 ? previous : candidate;
      const value = previous.type === 'action' && previous.event?.event === 'tool.result' && entry.event?.event === 'tool.call' ? previous : latest;
      entries.set(entry.key, {
        ...value,
        sequence: first.sequence ?? latest.sequence,
        created_at: first.created_at || latest.created_at,
        updated_at: latest.updated_at,
        updated_sequence: latest.updated_sequence,
        order: previous.order,
      });
    } else entries.set(entry.key, { ...entry, order: order++ });
  };
  const addEvent = (event: JournalEvent, fallback: { sequence: number | null; created_at?: string }, key: string) => {
    const payload = event.data ?? event.payload ?? {};
    if (payload.channel && !['commentary', 'final'].includes(payload.channel)) return;
    const kind = event.event ?? event.kind;
    const common = { sequence: sequenceOf(event.sequence ?? payload.sequence ?? fallback.sequence), created_at: event.created_at || payload.created_at || fallback.created_at };
    if (kind && ['tool.call', 'tool.result'].includes(kind)) {
      add({
        ...common,
        key: payload.id != null ? `action:${payload.id}` : `action:${key}`,
        type: 'action',
        updated_at: common.created_at,
        updated_sequence: sequenceOf(event.sequence ?? payload.sequence),
        event: { event: kind, data: payload },
      });
    } else if (kind && ['context.compacting', 'context.compacted'].includes(kind)) {
      add({ ...common, key: `context:${event.id ?? key}`, type: 'compaction', text: payload.text || 'Сжимаю контекст…' });
    } else if ((kind && ['agent.status', 'agent.step', 'agent.input_required'].includes(kind)) || (!kind && typeof payload.text === 'string')) {
      const text = payload.text || (kind === 'agent.step' ? 'Думаю…' : kind === 'agent.input_required' ? 'Ожидаю подтверждение' : '');
      if (text) add({ ...common, key: `status:${event.id ?? key}`, type: 'status', text });
    }
  };
  for (const message of journalMessages(metadata)) {
    const meta = message.metadata ?? {};
    const common = { sequence: sequenceOf(meta.sequence), created_at: meta.started_at || message.created_at || meta.created_at };
    if (message.content?.trim() && meta.channel && ['commentary', 'final'].includes(meta.channel)) {
      add({
        ...common,
        key: `message:${message.id}:text`,
        type: meta.channel as 'commentary' | 'final',
        text: message.content,
        completed_at: meta.completed_at,
        running: meta.status === 'running',
        complete: meta.status === 'completed',
      });
    }
    for (const [index, event] of (meta.events ?? []).entries()) {
      const time = event.created_at || event.data?.created_at || event.payload?.created_at;
      addEvent(event, { ...common, sequence: index === 0 || !time ? common.sequence : null }, `${message.id}:${index}`);
    }
  }
  for (const [index, event] of (metadata.turn?.timeline ?? []).entries()) addEvent(event, { sequence: null }, `timeline:${index}`);
  const values = [...entries.values()];
  // старые записи без sequence располагаются по времени, а не получают ложный ноль
  const known = values.filter((entry) => entry.sequence != null).sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0));
  for (const entry of values) {
    entry.position = entry.sequence;
    if (entry.position != null) continue;
    const time = journalTime(entry.created_at);
    const next = known.find((value) => journalTime(value.created_at) > time);
    const previous = known.findLast((value) => journalTime(value.created_at) <= time);
    entry.position = previous && next ? ((previous.sequence ?? 0) + (next.sequence ?? 0)) / 2 : previous ? (previous.sequence ?? 0) + 0.5 : next ? (next.sequence ?? 0) - 0.5 : null;
  }
  return values.sort((a, b) => {
    if (a.position != null && b.position != null && a.position !== b.position) return a.position - b.position;
    const delta = journalTime(a.created_at) - journalTime(b.created_at);
    return (Number.isFinite(delta) && delta) || a.order - b.order;
  });
}

/** Время работы за вычетом сжатия контекста. */
export function journalDuration(turn: { created_at?: string; completed_at?: string; timeline?: JournalEvent[] } | undefined): string | null {
  let paused = 0;
  let since: number | null = null;
  for (const event of turn?.timeline ?? []) {
    if (event.kind === 'context.compacting' && since == null) since = journalTime(event.created_at);
    if (event.kind === 'context.compacted' && since != null) {
      paused += Math.max(0, journalTime(event.created_at) - since);
      since = null;
    }
  }
  if (since != null) paused += Math.max(0, journalTime(turn?.completed_at) - since);
  const elapsed = journalTime(turn?.completed_at) - journalTime(turn?.created_at) - paused;
  return Number.isFinite(elapsed) && elapsed >= 0 ? duration(elapsed) : null;
}

const plural = (n: number, one: string, few: string, many: string) => {
  const tail = n % 100;
  const last = n % 10;
  return tail >= 11 && tail <= 14 ? many : last === 1 ? one : last >= 2 && last <= 4 ? few : many;
};

const groups: Record<string, string> = {
  'file.read': 'read', 'tool.output_read': 'read',
  'directory.list': 'explore', 'directory.tree': 'explore', 'search.text': 'explore', 'project.instructions': 'explore', 'project.map': 'explore',
  'file.create': 'edit', 'file.write_patch': 'edit', 'file.edit': 'edit', 'file.apply_patch': 'edit',
  'shell.run': 'command', 'test.run': 'command', 'verification.run': 'command', 'qa.run': 'command', 'quality.run': 'command',
  'web.search': 'web', 'web.fetch': 'web', 'plan.update': 'meta', 'project.map_update': 'map',
  'task.complete': 'meta', 'task.ask_user': 'meta', 'task.delegate': 'meta',
};

/** «Прочитал 3 файла, изменил 2 файла, выполнил 5 команд (1 с ошибкой)». */
export function journalSummary(entries: JournalEntry[]) {
  const actions = new Map<string, NonNullable<JournalEntry['event']>>();
  for (const entry of entries) if (entry.type === 'action' && entry.event) actions.set(entry.key, entry.event);
  const count = { read: new Set<unknown>(), explore: 0, created: new Set<unknown>(), edited: new Set<unknown>(), command: 0, failed: 0, web: 0, map: 0, plan: 0, other: 0 };
  let added = 0;
  let removed = 0;
  for (const event of actions.values()) {
    if (event.event !== 'tool.result') continue;
    const data = event.data ?? {};
    const args = data.arguments ?? data.args ?? {};
    const result = data.result ?? {};
    const path = args.path ?? args.source ?? result.path ?? data.id;
    const group = groups[data.name ?? ''] ?? 'other';
    if (group === 'read') count.read.add(path);
    else if (group === 'explore') count.explore++;
    else if (group === 'edit') {
      (data.name === 'file.create' ? count.created : count.edited).add(path);
      added += Number(result.added_lines ?? result.lines_added) || 0;
      removed += Number(result.removed_lines ?? result.lines_removed) || 0;
    } else if (group === 'command') count.command++;
    else if (group === 'web') count.web++;
    else if (group === 'meta') count.plan++;
    else if (group === 'map') count.map++;
    else count.other++;
    if (result.ok === false) count.failed++;
  }
  const parts: string[] = [];
  const files = (n: number) => `${n} ${plural(n, 'файл', 'файла', 'файлов')}`;
  if (count.read.size) parts.push(`прочитал ${files(count.read.size)}`);
  if (count.explore) parts.push('изучил проект');
  if (count.created.size) parts.push(`создал ${files(count.created.size)}`);
  if (count.edited.size) parts.push(`изменил ${files(count.edited.size)}`);
  if (count.command) parts.push(`выполнил ${count.command} ${plural(count.command, 'команду', 'команды', 'команд')}`);
  if (count.web) parts.push('искал в интернете');
  if (count.map) parts.push('обновил карту проекта');
  if (count.other) parts.push(`выполнил ${count.other} ${plural(count.other, 'действие', 'действия', 'действий')}`);
  if (!parts.length && count.plan) parts.push('обновил план');
  // длинный перечень не помещается в строку: три главные части и многоточие
  let text = parts.slice(0, 3).join(', ') + (parts.length > 3 ? '…' : '');
  if (count.failed) text += ` (${count.failed} с ошибкой)`;
  text = text ? (text[0]?.toLocaleUpperCase('ru-RU') ?? '') + text.slice(1) : 'Работал';
  return { text, added, removed, edits: count.created.size + count.edited.size };
}

/** Крупные блоки журнала: комментарии модели отдельно, действия между ними сворачиваются в этапы. */
export function journalSegments(entries: JournalEntry[], metadata: MessageMeta): JournalBlock[] {
  const blocks: JournalBlock[] = [];
  let segment: JournalBlock | null = null;
  const close = (end: string | undefined) => {
    if (segment) {
      segment.end = end;
      segment = null;
    }
  };
  for (const entry of entries) {
    if (entry.type === 'final') {
      close(entry.created_at);
      continue;
    }
    if (entry.type === 'compaction') {
      close(entry.created_at);
      blocks.push({ key: entry.key, type: 'compaction', entries: [entry], text: entry.text, created_at: entry.created_at });
      continue;
    }
    if (entry.type === 'commentary') {
      close(entry.created_at);
      blocks.push({ key: entry.key, type: 'commentary', entries: [entry], text: entry.text, created_at: entry.created_at, running: entry.running });
    } else {
      if (!segment) {
        segment = { key: `segment:${entry.key}`, type: 'segment', entries: [], created_at: entry.created_at };
        if (!blocks.length && metadata.turn?.created_at) segment.created_at = metadata.turn.created_at;
        blocks.push(segment);
      }
      // модель иногда просит правила для нескольких вложенных папок разом: в этапе это одна строка
      const rules = (item: JournalEntry) => item.type === 'action' && item.event?.data?.name === 'project.instructions' && item.event.data.result?.ok !== false;
      if (rules(entry) && segment.entries.some(rules)) continue;
      segment.entries.push(entry);
    }
  }
  const open = segment as JournalBlock | null;
  if (open) {
    open.running = metadata.status === 'running' && !metadata.live_text;
    if (!open.running) open.end = metadata.turn?.completed_at;
  }
  // этап из одних состояний («Думаю…», «Подготавливаю действие…») после завершения не получает строку «Думал N сек.»
  return blocks.filter((block) => block.type !== 'segment' || block.running || block.entries.some((entry) => entry.type !== 'status'));
}

/** Подпись хода: меняется только когда меняется то, что рисуется. */
export function journalSignature(metadata: MessageMeta): string {
  return JSON.stringify([
    metadata.status, metadata.live_text, metadata.turn?.id, metadata.turn?.created_at, metadata.turn?.completed_at, metadata.turn?.summary,
    orderedJournalEntries(metadata).map((entry) => [
      entry.key, entry.sequence, entry.created_at, entry.completed_at, entry.updated_at, entry.updated_sequence, entry.running, entry.complete,
      entry.type === 'action' ? [entry.event?.event, entry.event?.data?.id, actionText(entry.event as ActionEvent), entry.event?.data?.result?.ok, entry.event?.data?.result?.error, entry.event?.data?.result?.reason] : entry.text,
    ]),
  ]);
}

/* текст действий */
export interface ActionEvent {
  event: string;
  data?: ToolEventData;
}

const brief = (value: unknown): string => {
  const line = String(value ?? '').split(/[\r\n]/)[0]?.trim() ?? '';
  return line.length > 90 ? `${line.slice(0, 87)}…` : line;
};

export function actionText(event: ActionEvent): string {
  const data = event.data ?? {};
  const args = data.arguments ?? data.args ?? {};
  const result = data.result ?? {};
  const pending = event.event === 'tool.call';
  const path = String(args.path ?? args.source ?? result.path ?? '');
  const added = result.added_lines ?? result.lines_added;
  const removed = result.removed_lines ?? result.lines_removed;
  const delta = added !== undefined ? ` · +${added} −${removed || 0}` : '';
  const displayName = data.display_name || data.name;
  const command = brief(args.command ?? args.cmd);
  const query = brief(args.query ?? args.pattern);
  // ответ на вопросы агента: что выбрал или написал человек, одной строкой
  const reply = brief(String((result.response as { answer?: unknown } | undefined)?.answer ?? '').split('\n').filter((line) => line.startsWith('→ ')).map((line) => line.slice(2)).join('; '));
  let site = '';
  try {
    site = new URL(String(args.url ?? '')).hostname.replace(/^www\./, '');
  } catch {
    /* адреса нет */
  }
  const titles: Record<string, [string, string]> = {
    'tool.output_read': ['Читаю полный результат действия', 'Полный результат действия прочитан'],
    'project.instructions': ['Читаю правила проекта', 'Правила проекта изучены'],
    'project.map': ['Смотрю карту проекта', 'Карта проекта изучена'],
    'project.map_update': ['Обновляю карту проекта', 'Карта проекта обновлена'],
    'file.create': [`Создаю файл: ${path}`, `Создан файл: ${path}${delta}`],
    'file.write_patch': [`Редактирую: ${path}`, `Изменён файл: ${path}${delta}`],
    'file.edit': [`Редактирую: ${path}`, `Изменён файл: ${path}${delta}`],
    'file.apply_patch': ['Применяю изменения', 'Изменения применены'],
    'file.read': [`Читаю: ${path}`, `Прочитан файл: ${path}`],
    'directory.list': ['Изучаю папку проекта', 'Папка проекта изучена'],
    'directory.tree': ['Изучаю структуру проекта', 'Структура проекта изучена'],
    'search.text': [query ? `Ищу в проекте: ${query}` : 'Ищу в проекте', query ? `Поиск в проекте: ${query}` : 'Поиск в проекте завершён'],
    'web.search': [query ? `Ищу в интернете: ${query}` : 'Ищу информацию в интернете', query ? `Поиск в интернете: ${query}` : 'Поиск в интернете завершён'],
    'web.fetch': [site ? `Открываю ${site}` : 'Открываю источник', site ? `Изучен источник: ${site}` : 'Источник изучен'],
    'shell.run': [command ? `Выполняю: ${command}` : 'Выполняю команду', command ? `Выполнена команда: ${command}` : 'Команда выполнена'],
    'verification.run': [command ? `Проверяю: ${command}` : 'Проверяю результат', command ? `Проверено: ${command}` : 'Результат проверен'],
    'test.run': [command ? `Запускаю тесты: ${command}` : 'Запускаю тесты', command ? `Тесты выполнены: ${command}` : 'Тесты выполнены'],
    'plan.update': ['Обновляю план', 'План обновлён'],
    'qa.run': args.expect === 'fail' ? ['Проверяю, что тест падает по нужной причине', 'Красный тест проверен'] : ['Запускаю проверки', 'Проверки выполнены'],
    'quality.run': args.report_id ? ['Читаю отчёт качества', 'Отчёт качества прочитан'] : ['Проверяю качество кода', 'Качество кода проверено'],
    'task.delegate': ['Передаю подзадачу агенту', 'Подзадача выполнена'],
    'task.complete': ['Подвожу итог', 'Задача завершена'],
    'task.ask_user': ['Ожидаю уточнения', reply ? `Ответ пользователя: ${reply}` : 'Уточнение получено'],
  };
  const known = titles[data.name ?? ''];
  if (!pending && result.ok === false) return `Не выполнено: ${known?.[1] ?? displayName}`;
  return (known ?? [`Выполняю: ${displayName}`, `Выполнено: ${displayName}`])[pending ? 0 : 1];
}

/** Два последних звена пути: «src/app.py» вместо полного адреса. */
const tailPath = (value: unknown): string => String(value ?? '').split(String.fromCharCode(92)).join('/').split('/').filter(Boolean).slice(-2).join('/');
const clip = (text: string, limit = 48): string => (text.length > limit ? `${text.slice(0, limit - 1)}…` : text);

/** Короткая подпись идущего действия для строки состояния: что именно делает агент, без длинных запросов и команд. */
export function liveLabel(event: ActionEvent): string {
  const data = event.data ?? {};
  const args = data.arguments ?? data.args ?? {};
  const name = data.name ?? '';
  const file = clip(tailPath(args.path ?? args.source ?? data.result?.path));
  let site = '';
  try {
    site = new URL(String(args.url ?? '')).hostname.replace(/^www\./, '');
  } catch {
    /* адреса нет */
  }
  const labels: Record<string, string> = {
    'tool.output_read': 'Читаю результат действия',
    'project.instructions': 'Читаю правила проекта',
    'project.map': 'Смотрю карту проекта',
    'project.map_update': 'Обновляю карту проекта',
    'file.create': file ? `Создаю ${file}` : 'Создаю файл',
    'file.write_patch': file ? `Редактирую ${file}` : 'Редактирую файл',
    'file.edit': file ? `Редактирую ${file}` : 'Редактирую файл',
    'file.apply_patch': 'Применяю изменения',
    'file.read': file ? `Читаю ${file}` : 'Читаю файл',
    'directory.list': 'Изучаю папку проекта',
    'directory.tree': 'Изучаю структуру проекта',
    'search.text': 'Ищу в файлах проекта',
    'web.search': 'Ищу в интернете',
    'web.fetch': site ? `Открываю ${site}` : 'Открываю источник',
    'shell.run': 'Выполняю команду',
    'verification.run': 'Проверяю результат',
    'test.run': 'Запускаю тесты',
    'plan.update': 'Обновляю план',
    'qa.run': args.expect === 'fail' ? 'Проверяю красный тест' : 'Запускаю проверки',
    'quality.run': 'Проверяю качество кода',
    'task.delegate': 'Передаю подзадачу агенту',
    'task.complete': 'Подвожу итог',
    'task.ask_user': 'Ожидаю уточнения',
  };
  const known = labels[name];
  if (known) return known;
  const display = String(data.display_name || name || '').replaceAll('__', '.');
  return display ? `Выполняю ${clip(display, 32)}` : 'Выполняю действие';
}

export const journalLiveLabel = liveLabel;

/** Подпись состояния без длинных деталей: «Ищу в проекте: запрос…» -> «Ищу в файлах проекта», путь - два последних звена. */
export function conciseStatus(text: string): string {
  const line = String(text ?? '').split(/[\r\n]/)[0]?.trim() ?? '';
  const match = /^([^:]{2,40}):\s*(.+)$/.exec(line);
  if (!match) return clip(line, 60);
  const [, verb, detail] = match as unknown as [string, string, string];
  const lower = verb.toLowerCase();
  if (/^(ищу в проекте|поиск в проекте)/.test(lower)) return 'Ищу в файлах проекта';
  if (/^(ищу в интернете|поиск в интернете)/.test(lower)) return 'Ищу в интернете';
  if (/^(выполняю|выполнена команда)/.test(lower)) return 'Выполняю команду';
  if (/^(проверяю|проверено)/.test(lower)) return 'Проверяю результат';
  if (/^(запускаю тесты|тесты выполнены)/.test(lower)) return 'Запускаю тесты';
  if (/[\/]|\.\w{1,6}$/.test(detail)) return `${verb} ${clip(tailPath(detail))}`;
  return clip(line, 60);
}

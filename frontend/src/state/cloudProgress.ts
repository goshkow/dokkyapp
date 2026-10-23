/* Ход работы Докки в облачном чате: статус, комментарии и этапы, как в work-progress.js сайта. */
import { createStore, useStore } from '../lib/store';
import { readJson, writeJson } from '../lib/storage';
import type { Id } from '../lib/types';
import { duration } from '../lib/journal';
import { data } from './data';
import { ui } from './ui';

export const PAST_LABELS: Record<string, string> = {
  understand: 'Разобрал запрос',
  plan: 'Составил план',
  routing: 'Выбрал способ обработки',
  inspect_attachments: 'Изучал вложения',
  attachments: 'Изучал вложения',
  local_rag: 'Проверял сохранённую информацию',
  web_search: 'Искал в интернете',
  relevance: 'Сопоставлял источники',
  page_reading: 'Изучал страницы',
  fact_check: 'Проверял факты',
  additional_search: 'Искал дополнительные источники',
  general_search: 'Расширял поиск',
  deep_search: 'Углублял поиск',
  deep_reading: 'Изучал дополнительные страницы',
  synthesis: 'Готовил ответ',
  citation_verify: 'Проверял ссылки',
  export: 'Работал над файлом',
  document_assembly: 'Собирал документ',
  memory: 'Учитывал контекст',
  image: 'Создавал изображение',
  video_resolve: 'Нашёл видео',
  video_wait: 'Проверял сохранённый анализ',
  video_captions: 'Получал субтитры',
  video_analysis: 'Изучал видео',
  video_transcription: 'Распознавал речь',
  video_visuals: 'Анализировал визуальные моменты',
  video_digest: 'Составлял конспект',
  video_index: 'Формировал индекс',
  browser: 'Работал с браузером',
};

export interface ProgressEntry {
  type: 'status' | 'commentary';
  node: string;
  text: string;
  at?: number;
}

export interface ProgressSnapshot {
  entries: ProgressEntry[];
  started: number;
  elapsed: number | null;
  messageId: string;
  expanded: boolean;
}

export type CommentBlock =
  | { kind: 'commentary'; key: number; text: string }
  | { kind: 'phase'; key: number; label: string; rows: string[] };

export interface BrowserPreview {
  url: string;
  title: string;
  image: string;
}

export interface LiveProgress {
  chatId: Id | null;
  runId: string | null;
  started: number;
  status: { node: string; text: string };
  blocks: CommentBlock[];
  entries: ProgressEntry[];
  /** Секунды текущей фазы нужны для подписи «Работал N сек.» у свернувшейся группы. */
  phaseStart: number;
  phase: Array<{ node: string; text: string }>;
  answerStarted: boolean;
  complete: boolean;
  elapsed: number | null;
  preview: BrowserPreview | null;
}

interface ProgressState {
  live: LiveProgress | null;
}

export const progress = createStore<ProgressState>({ live: null });
export const useProgress = <S,>(selector: (state: ProgressState) => S): S => useStore(progress, selector);

const snapshots = new Map<string, ProgressSnapshot>();
let blockKey = 0;

const userKey = (): string => data.get().account?.user?.id || 'current-account';
const storageKey = (): string => `dokky-work-progress-v1:${userKey()}`;
const durationsKey = (): string => `koddi-work-durations:${userKey()}`;

/* длительности ответов */
export function savedDurations(): Record<string, number> {
  const value = readJson<unknown>(durationsKey(), {});
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, number>) : {};
}

function saveDuration(id: string, elapsed: number | null): void {
  if (elapsed == null || !Number.isFinite(elapsed) || elapsed < 0) return;
  const entries = Object.entries(savedDurations()).filter(([key]) => key !== id);
  entries.push([id, elapsed]);
  writeJson(durationsKey(), Object.fromEntries(entries.slice(-300)));
}

/* ход работы последнего ответа переживает перезапуск */
interface Persisted extends Omit<ProgressSnapshot, 'expanded'> {
  scope: string;
  savedAt: number;
}

function persist(snapshot: ProgressSnapshot): void {
  try {
    const value: Persisted = {
      scope: ui.get().scope, messageId: snapshot.messageId, entries: snapshot.entries, started: snapshot.started,
      elapsed: snapshot.elapsed, savedAt: Date.now(),
    };
    sessionStorage.setItem(storageKey(), JSON.stringify(value));
  } catch {
    /* без хранилища ход работы просто не запоминается */
  }
}

export function restoreSnapshot(messageId: string): ProgressSnapshot | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey()) || 'null') as Persisted | null;
    if (!value || Date.now() - value.savedAt > 24 * 60 * 60 * 1000 || !Array.isArray(value.entries)) return null;
    if (value.scope !== ui.get().scope || String(value.messageId) !== String(messageId)) return null;
    return { entries: value.entries.slice(-160), started: value.started, elapsed: value.elapsed ?? null, messageId: String(value.messageId), expanded: false };
  } catch {
    return null;
  }
}

export function snapshotFor(messageId: string): ProgressSnapshot | undefined {
  return snapshots.get(messageId);
}

export function rememberSnapshot(snapshot: ProgressSnapshot): void {
  snapshots.set(snapshot.messageId, snapshot);
  while (snapshots.size > 30) snapshots.delete(snapshots.keys().next().value as string);
}

export function setExpanded(messageId: string, expanded: boolean): void {
  const snapshot = snapshots.get(messageId);
  if (snapshot) snapshots.set(messageId, { ...snapshot, expanded });
}

/* живой ход */
const patchLive = (patch: Partial<LiveProgress>) => {
  const live = progress.get().live;
  if (live) progress.set({ live: { ...live, ...patch } });
};

export function beginProgress(chatId: Id | null, runId: string | null = null, startedAt: number | null = null): void {
  progress.set({
    live: {
      chatId, runId, started: startedAt ?? Date.now(), status: { node: 'thinking', text: 'Думаю…' }, blocks: [], entries: [],
      phaseStart: Date.now(), phase: [], answerStarted: false, complete: false, elapsed: null, preview: null,
    },
  });
}

export function bindProgress(patch: { chatId?: Id | null; runId?: string | null }): void {
  patchLive(patch as Partial<LiveProgress>);
}

const record = (live: LiveProgress, type: ProgressEntry['type'], node: string, text: string): ProgressEntry[] => {
  if (!text || live.complete) return live.entries;
  const previous = live.entries.at(-1);
  if (previous?.type === type && previous.node === node && previous.text === text) return live.entries;
  return [...live.entries, { type, node, text: String(text).slice(0, 2000), at: Date.now() }].slice(-160);
};

export function progressStatus(node: string, text: string): void {
  const live = progress.get().live;
  if (!live) return;
  if (node === 'browser_preview') {
    let value: { clear?: boolean; url?: string; image?: string; title?: string };
    try {
      value = JSON.parse(text) as typeof value;
    } catch {
      return;
    }
    if (value.clear) return patchLive({ preview: null });
    if (!/^https?:\/\//.test(value.url ?? '') || !/^data:image\/jpeg;base64,[A-Za-z0-9+/=]+$/.test(value.image ?? '')) return;
    patchLive({ preview: { url: value.url as string, image: value.image as string, title: value.title || value.url || 'Браузер' } });
    return;
  }
  const entries = record(live, 'status', node, text);
  const last = live.phase.at(-1);
  const phase = !last || last.node !== node || last.text !== text ? [...live.phase, { node, text }].slice(-160) : live.phase;
  patchLive({ entries, phase, status: { node, text } });
}

export function progressCommentary(text: string): void {
  const live = progress.get().live;
  if (!live) return;
  let blocks = live.blocks;
  // этап между комментариями сворачивается в группу «Работал N сек.»
  if (blocks.length && live.phase.length) {
    blocks = [...blocks, {
      kind: 'phase', key: ++blockKey, label: `Работал ${duration(Date.now() - live.phaseStart)}`,
      rows: live.phase.map((entry) => PAST_LABELS[entry.node] || entry.text),
    }];
  }
  blocks = [...blocks, { kind: 'commentary' as const, key: ++blockKey, text }].slice(-40);
  patchLive({ entries: record(live, 'commentary', 'commentary', text), blocks, phase: [], phaseStart: Date.now() });
}

export function progressAnswer(): void {
  const live = progress.get().live;
  if (!live || live.answerStarted) return;
  patchLive({ answerStarted: true, elapsed: Date.now() - live.started });
}

/** Ход завершён: снимок уходит сообщению ответа, живой блок исчезает. */
export function finishProgress(messageId?: string): void {
  const live = progress.get().live;
  if (!live) return;
  const elapsed = live.elapsed ?? Date.now() - live.started;
  if (messageId) {
    const snapshot: ProgressSnapshot = { entries: live.entries, started: live.started, elapsed, messageId, expanded: false };
    rememberSnapshot(snapshot);
    saveDuration(messageId, elapsed);
    persist(snapshot);
  }
  progress.set({ live: null });
}

export function clearProgress(forget = false): void {
  progress.set({ live: null });
  if (!forget) return;
  try {
    localStorage.removeItem(durationsKey());
    sessionStorage.removeItem(storageKey());
  } catch {
    /* см. выше */
  }
  snapshots.clear();
}

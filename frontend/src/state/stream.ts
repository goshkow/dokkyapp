/* Живой текст ответа. Порции приходят потоком и не проходят через React: они пишутся прямо в узел сообщения,
   а сам узел хранится в реестре и переезжает между лентой и журналом без перерисовки. */
import { cancelStream } from '../lib/streamMarkdown';

export interface StreamBuffer {
  content: string;
  dirty: boolean;
  /** Сервер уже прислал это сообщение: понятно, комментарий это или ответ. */
  synced?: boolean;
  heldAt?: number;
  syncAsked?: boolean;
}

export interface Delta {
  content: string;
  replace?: boolean;
  offset?: number;
}

const buffers = new Map<string, StreamBuffer>();
const listeners = new Map<string, Set<(content: string) => void>>();

export const streamBuffers = buffers;
export const bufferContent = (id: string): string | undefined => buffers.get(id)?.content;
export const clearBuffers = (): void => buffers.clear();

export function seedBuffer(id: string, content: string): void {
  buffers.set(id, { content, dirty: false });
}

export function markSynced(id: string): void {
  const buffer = buffers.get(id);
  if (buffer) buffer.synced = true;
}

/* Ответ, который это окно получило вживую, печатается плавно, даже если сервер успел отдать его готовым:
   итог задачи режима кода приходит одним куском и сразу со статусом «готово». Ответ, открытый из истории,
   показывается сразу. */
const replays = new Set<string>();
export const hasReplay = (id: string): boolean => replays.has(id);
export const takeReplay = (id: string): boolean => replays.delete(id);
export const clearReplays = (): void => replays.clear();
export function moveReplay(from: string, to: string): void {
  if (replays.delete(from)) replays.add(to);
}

/** Добавляет порцию к накопленному тексту; возвращает true, если текст изменился. */
export function pushDelta(id: string, delta: Delta, base: string): boolean {
  if (!buffers.has(id) && !base && delta.content) replays.add(id);
  const buffer = buffers.get(id) ?? { content: base, dirty: false };
  const before = buffer.content;
  if (delta.replace) buffer.content = delta.content;
  else if (Number.isInteger(delta.offset)) buffer.content = buffer.content.slice(0, delta.offset) + delta.content;
  else buffer.content += delta.content;
  buffer.dirty ||= before !== buffer.content;
  buffers.set(id, buffer);
  return buffer.dirty;
}

export function resetBuffer(id: string): void {
  buffers.delete(id);
}

export function onStream(id: string, listener: (content: string) => void): () => void {
  let set = listeners.get(id);
  if (!set) listeners.set(id, (set = new Set()));
  set.add(listener);
  return () => {
    set.delete(listener);
    if (!set.size) listeners.delete(id);
  };
}

export function emitStream(id: string, content: string): void {
  for (const listener of listeners.get(id) ?? []) listener(content);
}

/* узлы тела сообщения */
const bodies = new Map<string, HTMLDivElement>();

/** Узел тела сообщения: один на сообщение, пока оно живёт в приложении. */
export function acquireBody(id: string): HTMLDivElement {
  let body = bodies.get(id);
  if (!body) {
    body = document.createElement('div');
    body.className = 'message-body';
    bodies.set(id, body);
  }
  return body;
}

export function dropBody(id: string): void {
  const body = bodies.get(id);
  if (body) cancelStream(body);
  bodies.delete(id);
}

export function clearBodies(): void {
  for (const body of bodies.values()) cancelStream(body);
  bodies.clear();
}

/** Узел оставляют в реестре только пока идёт ответ: вернётся в другое место ленты. */
export const hasBody = (id: string): boolean => bodies.has(id);

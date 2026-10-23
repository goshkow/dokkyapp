import { api } from '../lib/api';
import { createStore, useStore } from '../lib/store';
import { notify } from './overlay';
import type { Attachment, UploadJob } from './chatTypes';
import { appendPastedText } from '../lib/pastedText';

export type AgentMode = 'development' | 'chat' | 'plan' | 'debug' | 'review';

export interface ComposerState {
  text: string;
  /** Режим агента; «plan» включается чипом «Планирование». */
  agentMode: AgentMode;
  attachments: Attachment[];
  /** Подключённые навыки: уйдут вместе со следующим запросом. */
  skills: Array<{ id: number; name: string }>;
  uploads: UploadJob[];
  dragging: boolean;
  /** Растёт, когда нужно вернуть фокус в поле ввода. */
  focus: number;
  recording: boolean;
  /** Куски записи уже на сервере: ждём распознавания, поле ввода занято. */
  transcribing: boolean;
  /** Распознанный текст, который уже готов, пока запись ещё идёт. */
  voicePreview: string;
  /** Окно «Развернуть поле ввода» для длинного текста. */
  expanded: boolean;
  /** Выбранный режим Докки для облачных режимов (чат, поиск). */
  cloudModel: Record<string, string>;
}

export const composer = createStore<ComposerState>({
  text: '',
  agentMode: 'development',
  attachments: [],
  skills: [],
  uploads: [],
  dragging: false,
  focus: 0,
  recording: false,
  transcribing: false,
  voicePreview: '',
  expanded: false,
  cloudModel: {},
});
export const useComposer = <S,>(selector: (state: ComposerState) => S): S => useStore(composer, selector);

const controllers = new Map<string, AbortController>();
const previews = new Set<string>();

export const setText = (text: string): void => composer.set({ text });
export const focusComposer = (): void => composer.set({ focus: composer.get().focus + 1 });

const isImage = (file: File) => file.type.startsWith('image/');

function dropPreview(url?: string): void {
  if (!url) return;
  URL.revokeObjectURL(url);
  previews.delete(url);
}

const patchJob = (key: string, patch: Partial<UploadJob>) =>
  composer.set({ uploads: composer.get().uploads.map((job) => (job.key === key ? { ...job, ...patch } : job)) });

/** Путь загрузки зависит от режима: облачные файлы Докки или вложения проекта. */
export const uploadPath = (cloud: boolean): string => (cloud ? '/api/dokky/cloud/files' : '/api/attachments');

export async function upload(files: Iterable<File>, cloud: boolean, pastedText?: string): Promise<void> {
  const jobs: UploadJob[] = Array.from(files, (file) => {
    const preview = isImage(file) ? URL.createObjectURL(file) : undefined;
    if (preview) previews.add(preview);
    return { key: crypto.randomUUID(), file, error: null, running: false, preview, path: uploadPath(cloud), pastedText };
  });
  if (!jobs.length) return;
  composer.set({ uploads: [...composer.get().uploads, ...jobs] });
  // по одному: большие вложения не должны умножать расход памяти
  for (const job of jobs) {
    if (composer.get().uploads.some((item) => item.key === job.key)) await runUpload(job.key);
  }
}

export async function runUpload(key: string): Promise<void> {
  const job = composer.get().uploads.find((item) => item.key === key);
  if (!job || controllers.has(key)) return;
  const controller = new AbortController();
  controllers.set(key, controller);
  patchJob(key, { running: true, error: null });
  try {
    const form = new FormData();
    form.append('file', job.file);
    const saved = await api<Attachment>(job.path, {
      method: 'POST',
      body: form,
      signal: controller.signal,
      headers: { 'Idempotency-Key': key },
    });
    if (controller.signal.aborted || !composer.get().uploads.some((item) => item.key === key)) return;
    composer.set({
      uploads: composer.get().uploads.filter((item) => item.key !== key),
      attachments: [...composer.get().attachments, { ...saved, preview: job.preview, name: saved.name ?? job.file.name, pastedText: job.pastedText }],
    });
  } catch (error) {
    if (controller.signal.aborted || !composer.get().uploads.some((item) => item.key === key)) return;
    patchJob(key, { error: (error as Error).message });
  } finally {
    controllers.delete(key);
    if (composer.get().uploads.some((item) => item.key === key)) patchJob(key, { running: false });
  }
}

export function cancelUpload(key: string): void {
  const job = composer.get().uploads.find((item) => item.key === key);
  controllers.get(key)?.abort();
  controllers.delete(key);
  dropPreview(job?.preview);
  composer.set({ uploads: composer.get().uploads.filter((item) => item.key !== key) });
}

export function removeAttachment(id: Attachment['id']): void {
  const item = composer.get().attachments.find((value) => value.id === id);
  dropPreview(item?.preview);
  composer.set({ attachments: composer.get().attachments.filter((value) => value.id !== id) });
}

export function restorePastedText(source: { attachmentId: Attachment['id'] } | { uploadKey: string }): void {
  const state = composer.get();
  const item = 'attachmentId' in source ? state.attachments.find((value) => value.id === source.attachmentId) : state.uploads.find((value) => value.key === source.uploadKey);
  if (item?.pastedText == null) return;
  if ('attachmentId' in source) removeAttachment(source.attachmentId);
  else cancelUpload(source.uploadKey);
  composer.set({ text: appendPastedText(composer.get().text, item.pastedText), focus: composer.get().focus + 1 });
}

/** сервер требует непустое content; подпись не подменяет содержимое вложенного запроса */
export function draftContent(): string {
  const { text, attachments } = composer.get();
  return text.trim() || (attachments.some((item) => item.pastedText != null) ? 'Текст моего запроса во вложении.' : attachments.length ? 'Вложения' : '');
}

export function clearAttachments(): void {
  for (const controller of controllers.values()) controller.abort();
  controllers.clear();
  for (const url of [...previews]) dropPreview(url);
  composer.set({ attachments: [], uploads: [] });
}

/** Отправленные вложения остаются в сообщении: превью больше не нужны полю ввода, но живут, пока их показывает лента. */
export function releaseAttachments(): Attachment[] {
  const sent = composer.get().attachments;
  composer.set({ attachments: [], uploads: [] });
  return sent;
}

export function attachmentsReady(): boolean {
  const { uploads } = composer.get();
  if (!uploads.length) return true;
  notify(uploads.some((job) => job.error) ? 'Повторите загрузку вложения или уберите его перед отправкой' : 'Дождитесь загрузки вложений перед отправкой');
  return false;
}

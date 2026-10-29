/**
 * Поле ввода как нативное стекло (macOS): само поле живёт в отдельном веб-виде внутри стекла над лентой (роль composer),
 * а всё остальное (вложения, очередь, панель режимов) остаётся в главном виде. Здесь - зеркало нужного полю состояния
 * и действия, которые поле вызывает у главного вида (lib/mirror.ts).
 */
import { api } from '../lib/api';
import { exposeActions, exposeStore, isMirror, remote } from '../lib/mirror';
import { createStore, useStore } from '../lib/store';
import type { Attachment } from './chatTypes';
import { send, sendOrStop } from './chat';
import { recordCodingActivity } from './codingSession';
import { composer, setText, uploadPath, upload } from './composer';
import { guarded, notify } from './overlay';
import { enqueueDraft, interruptAndSend } from './queue';
import { attachFromField, siteFieldActive } from './siteField';
import { cancelVoice, stopVoice, toggleVoice, voiceLevels } from './voice';

export interface FieldMirror {
  text: string;
  hasFiles: boolean;
  recording: boolean;
  transcribing: boolean;
  dragging: boolean;
  focus: number;
}

/** Срез состояния ввода, который показывает поле (в главном виде вычисляется из composer, в виде поля приходит зеркалом). */
export const fieldMirror = createStore<FieldMirror>({ text: '', hasFiles: false, recording: false, transcribing: false, dragging: false, focus: 0 });
export const useField = <S,>(selector: (state: FieldMirror) => S): S => useStore(fieldMirror, selector);

/** Что поле сообщает главному виду (высота и раскрытость) и что главный вид сообщает полю (открыто ли меню «Добавить»). */
export const fieldHost = createStore({ menu: false, height: 0, expanded: false });
export const useFieldHost = <S,>(selector: (state: ReturnType<typeof fieldHost.get>) => S): S => useStore(fieldHost, selector);

export const voiceMeter = createStore<{ levels: number[] }>({ levels: [] });
export const METER_BARS = 36;

const sync = () => {
  const { text, attachments, uploads, recording, transcribing, dragging, focus } = composer.get();
  fieldMirror.set({ text, hasFiles: attachments.length + uploads.length > 0, recording, transcribing, dragging, focus });
};

// один relay ограничен 4 МиБ: большую вставку собираем до открытия кнопки восстановления
const pasteChunks = new Map<string, string[]>();
if (!isMirror) composer.subscribe(() => {
  const active = new Set(composer.get().uploads.map((item) => item.key));
  for (const key of pasteChunks.keys()) if (!active.has(key)) pasteChunks.delete(key);
});

/** Главный вид: поле видит состояние ввода и отдаёт его виду поля. */
export function startFieldMirror(): void {
  if (isMirror) return;
  sync();
  composer.subscribe(sync);
  exposeStore('fieldMirror', fieldMirror);
  exposeStore('fieldHost', fieldHost);
  exposeStore('voiceMeter', voiceMeter);
  // уровень звука нужен волне в виде поля: пока идёт запись, он уходит туда 30 раз в секунду
  let timer = 0;
  const watchVoice = () => {
    const recording = composer.get().recording;
    if (recording && !timer) timer = window.setInterval(() => voiceMeter.set({ levels: voiceLevels(METER_BARS) }), 33);
    else if (!recording && timer) { window.clearInterval(timer); timer = 0; voiceMeter.set({ levels: [] }); }
  };
  composer.subscribe(watchVoice);
}

/* загрузка файлов, вставленных в поле */
/** Вид поля загружает файл сам (содержимое файла по мосту окна не передать): главному виду сообщается начало и итог. */
async function uploadBegin(key: string, name: string, cloud: boolean, pastedText?: string): Promise<void> {
  recordCodingActivity();
  composer.set({ uploads: [...composer.get().uploads, { key, file: new File(pastedText == null ? [] : [pastedText], name), error: null, running: true, path: uploadPath(cloud), pastedText }] });
}

function uploadTextChunk(key: string, chunk: string, last: boolean): boolean {
  const job = composer.get().uploads.find((item) => item.key === key);
  if (!job) { pasteChunks.delete(key); return false; }
  const chunks = pasteChunks.get(key) ?? [];
  chunks.push(chunk);
  if (!last) { pasteChunks.set(key, chunks); return true; }
  const text = chunks.join('');
  pasteChunks.delete(key);
  composer.set({ uploads: composer.get().uploads.map((item) => item.key === key ? { ...item, pastedText: text, file: new File([text], item.file.name) } : item) });
  return true;
}

async function uploadEnd(key: string, saved: Attachment | null, error: string | null): Promise<void> {
  const job = composer.get().uploads.find((item) => item.key === key);
  if (!job) return; // вложение уже убрано человеком
  composer.set({
    uploads: saved || job.pastedText == null ? composer.get().uploads.filter((item) => item.key !== key) : composer.get().uploads.map((item) => item.key === key ? { ...item, running: false, error: error || 'Не удалось загрузить вложение' } : item),
    attachments: saved ? [...composer.get().attachments, { ...saved, name: saved.name ?? job.file.name, pastedText: job.pastedText }] : composer.get().attachments,
  });
  if (!saved) notify(error || 'Не удалось загрузить вложение', true);
}

const beginRemote = remote('fieldUploadBegin', uploadBegin);
const endRemote = remote('fieldUploadEnd', uploadEnd);
const chunkRemote = remote('fieldUploadTextChunk', uploadTextChunk);

export async function uploadFromField(files: Iterable<File>, cloud: boolean, pastedText?: string): Promise<void> {
  if (siteFieldActive()) return attachFromField(files);
  if (!isMirror) return upload(files, cloud, pastedText);
  for (const file of files) {
    const key = crypto.randomUUID();
    const chunked = pastedText != null && JSON.stringify(pastedText).length > 3 * 1024 * 1024;
    await beginRemote(key, file.name, cloud, chunked ? undefined : pastedText);
    if (chunked) {
      const size = 256 * 1024;
      for (let index = 0; index < pastedText.length; index += size) {
        if (!await chunkRemote(key, pastedText.slice(index, index + size), index + size >= pastedText.length)) return;
      }
    }
    try {
      const form = new FormData();
      form.append('file', file);
      const saved = await api<Attachment>(uploadPath(cloud), { method: 'POST', body: form, headers: { 'Idempotency-Key': key } });
      await endRemote(key, saved, null);
    } catch (error) {
      await endRemote(key, null, (error as Error).message);
    }
  }
}

/* действия поля: в виде поля это вызовы главного вида */
const table = {
  fieldSetText: (text: string) => { recordCodingActivity(); setText(text); },
  fieldSend: () => guarded(send)(),
  fieldSendOrStop: () => guarded(sendOrStop)(),
  fieldInterrupt: () => guarded(interruptAndSend)(),
  fieldQueue: () => guarded(enqueueDraft)(),
  fieldToggleVoice: () => guarded(toggleVoice)(),
  fieldStopVoice: () => stopVoice(true),
  fieldCancelVoice: cancelVoice,
  fieldExpand: () => composer.set({ expanded: true }),
  fieldMenu: (open?: boolean) => fieldHost.set({ menu: open ?? !fieldHost.get().menu }),
  fieldReport: (height: number, expanded: boolean) => fieldHost.set({ height: Math.round(height), expanded }),
  fieldUploadBegin: uploadBegin,
  fieldUploadEnd: uploadEnd,
  fieldUploadTextChunk: uploadTextChunk,
};
exposeActions(table as unknown as Record<string, (...args: never[]) => unknown>);

export const fieldActions = {
  setText: remote('fieldSetText', table.fieldSetText),
  send: remote('fieldSend', table.fieldSend),
  sendOrStop: remote('fieldSendOrStop', table.fieldSendOrStop),
  interrupt: remote('fieldInterrupt', table.fieldInterrupt),
  queue: remote('fieldQueue', table.fieldQueue),
  toggleVoice: remote('fieldToggleVoice', table.fieldToggleVoice),
  stopVoice: remote('fieldStopVoice', table.fieldStopVoice),
  cancelVoice: remote('fieldCancelVoice', table.fieldCancelVoice),
  expand: remote('fieldExpand', table.fieldExpand),
  menu: remote('fieldMenu', table.fieldMenu),
  report: remote('fieldReport', table.fieldReport),
};

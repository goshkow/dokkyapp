/**
 * Поле ввода режима чата на macOS со стеклом. Как в режиме кода: поле - нативный вид приложения внутри стекла над лентой,
 * поэтому стекло размывает и преломляет ленту сайта. Поле страницы сайта остаётся невидимым и держит текст (правка
 * сообщения, другой чат): текст ходит в обе стороны, отправляют, останавливают и принимают вложения кнопки страницы.
 * Протокол - dokky/docs/APP_SURFACE.md, команды field-*.
 */
import { exposeActions, isMirror, remote } from '../lib/mirror';
import { composer } from './composer';
import { notify } from './overlay';
import { command, onSiteField, surface } from './surface';

/** Поле приложения стоит на месте поля страницы сайта. */
export const siteFieldActive = (): boolean => surface.get().field;

// последний текст, о котором знают обе стороны: эхо не уходит обратно и не сбивает набор
let synced = '';

function pushText(): void {
  const { text } = composer.get();
  if (text === synced) return;
  synced = text;
  command('field-text', { text });
}

/** Отправка из поля приложения: сначала свежий текст, потом кнопка страницы (сообщения фрейму идут по порядку). */
export function sendToSite(): void {
  pushText();
  if (surface.get().siteField?.disabled) return;
  command('field-send');
}

export function stopSite(): void {
  command('field-stop');
}

export function attachToSite(files: File[]): void {
  if (files.length) command('field-attach', { files: files.slice(0, 10) });
}

export function siteFieldHeight(height: number): void {
  if (height > 0) command('field-height', { height });
}

/* файлы из вида поля (вставка, перетаскивание, «+»): по мосту окна идут только строки, поэтому кусками в base64 */
const CHUNK = 1536 * 1024; // ~2 МиБ в base64: один relay ограничен 4 МиБ
const MAX_FILE = 100 * 1024 * 1024;
const incoming = new Map<string, { name: string; type: string; parts: Uint8Array<ArrayBuffer>[]; size: number; timer: number }>();

function drop(key: string): void {
  const job = incoming.get(key);
  if (job) window.clearTimeout(job.timer);
  incoming.delete(key);
}

function fileBegin(key: string, name: string, type: string): void {
  if (incoming.size > 20) return;
  // передача, которая оборвалась, не держит память
  incoming.set(key, { name: name.slice(0, 255), type: type.slice(0, 120), parts: [], size: 0, timer: window.setTimeout(() => drop(key), 120_000) });
}

function fileChunk(key: string, data: string): boolean {
  const job = incoming.get(key);
  if (!job) return false;
  const raw = atob(data);
  const bytes = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index);
  job.size += bytes.length;
  if (job.size > MAX_FILE) { drop(key); return false; }
  job.parts.push(bytes);
  return true;
}

function fileEnd(key: string): void {
  const job = incoming.get(key);
  if (!job) return;
  drop(key);
  attachToSite([new File(job.parts, job.name, { type: job.type })]);
}

exposeActions({ siteFileBegin: fileBegin, siteFileChunk: fileChunk, siteFileEnd: fileEnd } as unknown as Record<string, (...args: never[]) => unknown>);
const beginRemote = remote('siteFileBegin', fileBegin);
const chunkRemote = remote('siteFileChunk', fileChunk);
const endRemote = remote('siteFileEnd', fileEnd);

function base64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let out = '';
  for (let index = 0; index < bytes.length; index += 0x8000) out += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  return btoa(out);
}

/** Вложения поля приложения уходят странице сайта: в главном виде сразу, из вида поля - через главный вид. */
export async function attachFromField(files: Iterable<File>): Promise<void> {
  const list = [...files];
  if (!isMirror) { attachToSite(list); return; }
  for (const file of list.slice(0, 10)) {
    if (file.size > MAX_FILE) { notify(`Файл «${file.name}» больше 100 МБ`, true); continue; }
    const key = crypto.randomUUID();
    await beginRemote(key, file.name, file.type);
    let ok = true;
    for (let offset = 0; ok && offset < file.size; offset += CHUNK) {
      ok = await chunkRemote(key, base64(await file.slice(offset, offset + CHUNK).arrayBuffer()));
    }
    if (ok) await endRemote(key);
    else notify(`Не удалось передать «${file.name}»`, true);
  }
}

/** Главный вид: текст поля приложения и поля страницы держатся одинаковыми, пока поле рисует приложение. */
export function startSiteField(): void {
  if (isMirror) return;
  onSiteField({
    text: (value) => {
      synced = value;
      if (composer.get().text !== value) composer.set({ text: value });
    },
    focus: () => composer.set({ focus: composer.get().focus + 1 }),
  });
  composer.subscribe(() => { if (siteFieldActive()) pushText(); });
  let active = siteFieldActive();
  surface.subscribe(() => {
    const next = siteFieldActive();
    if (next === active) return;
    active = next;
    // включение: текст забирается со страницы (она пришлёт его сама), выключение - своё поле страницы снова главное
    synced = active ? composer.get().text : '';
  });
}

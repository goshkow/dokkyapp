/* Голосовой ввод как на сайте Докки: запись режется на куски по паузам в речи, каждый кусок сразу уходит на сервер
   Докки и распознаётся там (до двух запросов одновременно), текст склеивается по порядку и виден по мере готовности.
   Отдельный путь остаётся только для внутреннего тестового режима: локальное распознавание всей записи. */
import { osName } from '../lib/platform';
import { api, post } from '../lib/api';
import { readText, writeText } from '../lib/storage';
import { send } from './chat';
import { setStatus } from './chatCore';
import { composer } from './composer';
import { data, login } from './data';
import { confirmDialog, notify } from './overlay';

const MAX_RECORDING_MS = 300_000;
const MAX_PARALLEL = 2;
/** Кусок закрывается принудительно, если говорят дольше этого времени и сделали паузу. */
const LONG_SEGMENT_MS = 6000;

interface Session {
  stream: MediaStream;
  context: AudioContext | null;
  analyser: AnalyserNode | null;
  recorder: MediaRecorder | null;
  abort: AbortController;
  texts: string[];
  tasks: Promise<void>[];
  waiters: Array<() => void>;
  active: number;
  count: number;
  error: Error | null;
  timer: number;
  started: number;
  draft: string;
  stopRequested: boolean;
  cancelled: boolean;
  submit: boolean;
  finishing: boolean;
}

let session: Session | null = null;
let localStop: ((cancel: boolean) => void) | null = null;
let transcribing: { abort: AbortController; draft: string } | null = null;

/** Куски приходят не по порядку: склеиваем по номерам и поправляем многоточия на стыках. */
export function joinSegments(segments: Array<string | undefined>): string {
  let text = '';
  for (const segment of segments) {
    if (segment == null) break;
    let next = String(segment).trim();
    if (!next) continue;
    if (text && /(?:\.{3,}|…)\s*$/u.test(text)) {
      text = text.replace(/\s*(?:\.{3,}|…)\s*$/u, '').trimEnd();
      next = next.replace(/^(["«“‘(\[]*)(\p{L})/u, (_all, prefix: string, letter: string) => prefix + letter.toLocaleLowerCase());
    }
    text += `${text ? ' ' : ''}${next}`;
  }
  return text;
}

/** Уровень звука для волны над полем ввода: значения 0..1 по частотным полосам. */
export function voiceLevels(count: number): number[] {
  const analyser = session?.analyser;
  if (!analyser) return Array.from({ length: count }, () => 0);
  const bins = new Uint8Array(analyser.frequencyBinCount);
  analyser.getByteFrequencyData(bins);
  const usable = Math.min(bins.length, 56);
  return Array.from({ length: count }, (_value, index) => {
    const from = Math.floor((index / count) * usable);
    const to = Math.max(from + 1, Math.floor(((index + 1) / count) * usable));
    let sum = 0;
    for (let i = from; i < to; i += 1) sum += bins[i] ?? 0;
    return Math.min(1, (sum / (to - from) / 255) * 1.6);
  });
}

const mimeFor = (): string => ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'].find((type) => MediaRecorder.isTypeSupported?.(type)) ?? '';
const extensionFor = (mime: string): string => (mime.includes('mp4') ? 'm4a' : mime.includes('ogg') ? 'ogg' : 'webm');

function release(current: Session): void {
  window.clearInterval(current.timer);
  current.stream.getTracks().forEach((track) => track.stop());
  void current.context?.close().catch(() => undefined);
  current.analyser = null;
  current.context = null;
}

function restoreDraft(draft: string): void {
  composer.set({ text: draft });
}

/** Запись остановлена без распознавания (во время записи) или распознавание отменено. */
export function cancelVoice(): void {
  if (localStop) return localStop(true);
  if (transcribing) {
    transcribing.abort.abort();
    const { draft } = transcribing;
    transcribing = null;
    composer.set({ transcribing: false, voicePreview: '' });
    restoreDraft(draft);
    setStatus('Распознавание отменено');
    return;
  }
  const current = session;
  if (!current) return;
  current.cancelled = true;
  current.stopRequested = true;
  current.abort.abort();
  if (current.recorder?.state === 'recording') current.recorder.stop();
  else void finish(current);
}

/** Завершить запись; submit - отправить сообщение сразу после распознавания (Enter или кнопка отправки). */
export function stopVoice(submit = false): void {
  const current = session;
  if (!current || current.stopRequested) return;
  current.stopRequested = true;
  current.submit = submit;
  if (current.recorder?.state === 'recording') current.recorder.stop();
}

async function finish(current: Session): Promise<void> {
  if (current.finishing) return;
  current.finishing = true;
  release(current);
  session = null;
  composer.set({ recording: false });
  if (current.cancelled) {
    composer.set({ voicePreview: '' });
    restoreDraft(current.draft);
    setStatus('Запись отменена');
    return;
  }
  transcribing = { abort: current.abort, draft: current.draft };
  composer.set({ transcribing: true });
  setStatus('Распознаю речь…');
  try {
    await Promise.all(current.tasks);
    if (current.abort.signal.aborted || transcribing?.abort !== current.abort) return;
    const text = joinSegments(current.texts).trim();
    if (text) composer.set({ text: [current.draft.trim(), text].filter(Boolean).join(' ') });
    else restoreDraft(current.draft);
    if (current.error) throw current.error;
    if (!text) throw new Error('Речь не распознана');
    setStatus('Запись распознана');
    transcribing = null;
    composer.set({ transcribing: false, voicePreview: '' });
    if (current.submit) await send();
  } catch (error) {
    notify((error as Error).message || 'Не удалось распознать речь', true);
  } finally {
    if (transcribing?.abort === current.abort) transcribing = null;
    composer.set({ transcribing: false, voicePreview: '' });
  }
}

function sendSegment(current: Session, blob: Blob, seconds: number): void {
  const index = current.count;
  current.count += 1;
  const task = (async () => {
    if (current.active >= MAX_PARALLEL) await new Promise<void>((resolve) => current.waiters.push(resolve));
    else current.active += 1;
    try {
      if (current.abort.signal.aborted) return;
      const form = new FormData();
      form.append('audio', new File([blob], `voice-${Date.now()}-${index}.${extensionFor(blob.type)}`, { type: blob.type || 'audio/webm' }));
      form.append('seconds', String(seconds));
      const result = await api<{ text?: string }>('/api/dokky/cloud/speech/transcriptions', { method: 'POST', body: form, signal: current.abort.signal });
      current.texts[index] = String(result.text ?? '').trim();
      composer.set({ voicePreview: joinSegments(current.texts) });
    } catch (error) {
      if ((error as Error).name !== 'AbortError' && !current.abort.signal.aborted) current.error = error as Error;
    } finally {
      const next = current.waiters.shift();
      if (next) next();
      else current.active -= 1;
    }
  })();
  current.tasks.push(task);
}

function startSegment(current: Session): void {
  const mime = mimeFor();
  const recorder = new MediaRecorder(current.stream, { ...(mime ? { mimeType: mime } : {}), audioBitsPerSecond: 48000 });
  const chunks: Blob[] = [];
  const started = Date.now();
  let heard = false;
  let lastSpeech = 0;
  let speechFrames = 0;
  let floor = 3;
  current.recorder = recorder;
  recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
  recorder.onstop = () => {
    window.clearInterval(current.timer);
    const seconds = Math.max(1, Math.ceil((Date.now() - started) / 1000));
    const blob = new Blob(chunks, { type: recorder.mimeType || mime || 'audio/webm' });
    // хвост тишины после последнего куска не отправляем
    const trailingSilence = !heard && current.count > 0 && seconds <= 2;
    if (!current.cancelled && !trailingSilence && blob.size) sendSegment(current, blob, seconds);
    else if (!current.cancelled && !trailingSilence && seconds > 1) {
      current.error = new Error('Не удалось сохранить часть записи');
      current.stopRequested = true;
    }
    if (!current.stopRequested && current.stream.active && Date.now() - current.started < MAX_RECORDING_MS) {
      startSegment(current);
      return;
    }
    void finish(current);
  };
  recorder.start();
  const samples = new Uint8Array(current.analyser?.fftSize ?? 256);
  current.timer = window.setInterval(() => {
    const now = Date.now();
    const elapsed = now - started;
    if (current.analyser) {
      current.analyser.getByteTimeDomainData(samples);
      let peak = 0;
      for (const value of samples) peak = Math.max(peak, Math.abs(value - 128));
      const speaking = peak > Math.max(7, floor * 2.6);
      if (speaking) { speechFrames += 1; if (speechFrames >= 2 || heard) { heard = true; lastSpeech = now; } }
      else { speechFrames = 0; floor = floor * 0.94 + peak * 0.06; }
    }
    if (now - current.started >= MAX_RECORDING_MS) stopVoice(false);
    const silenceNeeded = elapsed >= LONG_SEGMENT_MS ? 900 : 1700;
    if (recorder.state === 'recording' && heard && (elapsed >= LONG_SEGMENT_MS || (current.context?.state === 'running' && elapsed >= 1200 && now - lastSpeech >= silenceNeeded))) recorder.stop();
  }, 120);
}

async function localRecording(stream: MediaStream, draft: string): Promise<void> {
  const recorder = new MediaRecorder(stream);
  const chunks: Blob[] = [];
  recorder.ondataavailable = (event) => chunks.push(event.data);
  let cancelled = false;
  const stopLocal = (cancel: boolean) => { cancelled = cancel; if (recorder.state === 'recording') recorder.stop(); };
  localStop = stopLocal;
  recorder.onstop = () => {
    stream.getTracks().forEach((track) => track.stop());
    localStop = null;
    composer.set({ recording: false });
    if (cancelled) return void setStatus('Запись отменена');
    void (async () => {
      try {
        const form = new FormData();
        form.append('file', new Blob(chunks, { type: recorder.mimeType }), 'voice.webm');
        setStatus('Распознаю речь…');
        const saved = await api<{ stored_path: string }>('/api/attachments', { method: 'POST', body: form });
        const result = await post<{ text?: string; error?: string }>('/api/speech/transcribe', { path: saved.stored_path });
        if (!result.text) throw new Error(result.error || 'Речь не распознана');
        composer.set({ text: [draft.trim(), result.text].filter(Boolean).join(' ') });
        setStatus('Запись распознана');
      } catch (error) {
        notify((error as Error).message, true);
      }
    })();
  };
  recorder.start();
  composer.set({ recording: true });
  setStatus('Идёт запись…');
}

export async function toggleVoice(): Promise<void> {
  if (!navigator.mediaDevices?.getUserMedia) return notify('Голосовой ввод недоступен в этой среде', true);
  if (localStop) return localStop(false);
  if (session) return stopVoice(false);
  if (transcribing) return;
  const account = data.get().account;
  const remote = !account?.internal_test;
  if (remote && !account?.authenticated) return login();
  const consent = remote
    ? { consented: readText('dokky-microphone-consent') === '1', model_status: 'installed' }
    : await api<{ consented: boolean; model_status: string }>('/api/speech/status');
  if (!consent.consented) {
    if (!(await confirmDialog('Голосовой ввод', 'Микрофон используется для записи сообщения. Запись будет отправлена в Докки для распознавания.', 'Разрешить'))) return;
    if (remote) writeText('dokky-microphone-consent', '1');
    else await post('/api/speech/consent', { consented: true });
  }
  if (consent.model_status !== 'installed') return notify('Модуль распознавания речи не установлен. Запись не начата.', true);
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    return notify('Нет доступа к микрофону. Разрешите его в настройках ' + osName(), true);
  }
  const draft = composer.get().text;
  if (!remote) return localRecording(stream, draft);
  const Context = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  let context: AudioContext | null = null;
  let analyser: AnalyserNode | null = null;
  try {
    context = new Context();
    void context.resume().catch(() => undefined);
    analyser = context.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = 0.6;
    context.createMediaStreamSource(stream).connect(analyser);
  } catch {
    context = null;
    analyser = null;
  }
  const current: Session = {
    stream, context, analyser, recorder: null, abort: new AbortController(), texts: [], tasks: [], waiters: [], active: 0, count: 0, error: null, timer: 0,
    started: Date.now(), draft, stopRequested: false, cancelled: false, submit: false, finishing: false,
  };
  session = current;
  composer.set({ recording: true, voicePreview: '' });
  setStatus('Идёт запись…');
  startSegment(current);
}

/** Отмена записи из любого места (смена чата, выход из аккаунта). */
export function abortVoice(): void {
  if (session || transcribing || localStop) {
    if (localStop) localStop(true);
    else cancelVoice();
  }
}

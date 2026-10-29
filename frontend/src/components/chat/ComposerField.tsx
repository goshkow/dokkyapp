import { ArrowUp, ListPlus, Maximize2, Mic, Plus, Square, X, Zap } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { announceComposerFocus } from '../../lib/bridge';
import { isMirror } from '../../lib/mirror';
import { mod } from '../../lib/platform';
import { useChat } from '../../state/chatCore';
import { useData } from '../../state/data';
import { fieldActions, fieldHost, uploadFromField, useField, useFieldHost, voiceMeter } from '../../state/field';
import { useSurface } from '../../state/surface';
import { useUi } from '../../state/ui';
import { VoiceWave } from './VoiceWave';
import { appendPastedText, pastedTextFile } from '../../lib/pastedText';
import { notify } from '../../state/overlay';

const levelsFromMeter = (count: number): number[] => voiceMeter.get().levels.slice(0, count);

/** Класс обёртки: на нём держатся стили записи и распознавания (#composer.recording и т. д.). */
export function fieldClasses(expanded: boolean, dragging: boolean, recording: boolean, transcribing: boolean): string {
  return `${expanded ? 'expanded' : ''}${dragging ? ' file-drop' : ''}${recording ? ' recording' : ''}${transcribing ? ' transcribing' : ''}`;
}

/**
 * Само поле ввода. В главном виде стоит внутри обычного блока #composer; на macOS со стеклом живёт в отдельном виде
 * поверх ленты (роль composer), чтобы нативное стекло под ним размывало ленту, а не пустой фон. Состояние ввода приходит
 * через fieldMirror, действия выполняет главный вид (state/field.ts).
 */
export function ComposerField({ attach, menuOpen, onExpanded }: { attach?: RefObject<HTMLButtonElement | null>; menuOpen?: boolean; onExpanded?: (expanded: boolean) => void }) {
  const scope = useUi((s) => s.scope);
  // поле стоит на месте поля страницы сайта (режим чата со стеклом): отправку, остановку и вложения ведёт страница
  const site = useSurface((s) => s.field);
  const siteBusy = useSurface((s) => s.siteField?.busy ?? false);
  const siteReady = useSurface((s) => s.siteField?.ready ?? false);
  const siteClosed = useSurface((s) => Boolean(s.siteField?.disabled && !s.siteField.busy));
  const sitePlaceholder = useSurface((s) => s.siteField?.placeholder ?? '');
  const development = !site && scope === 'development';
  const search = !site && scope === 'search';
  const settings = useData((s) => s.settings);
  const baseSize = useUi((s) => s.baseSize);
  const uiFont = useUi((s) => s.uiFont);
  const running = useChat((s) => s.running);
  const stopping = useChat((s) => s.stopping);
  const mirrored = useField((s) => s.text);
  const hasFiles = useField((s) => s.hasFiles);
  const recording = useField((s) => s.recording);
  const transcribing = useField((s) => s.transcribing);
  const focus = useField((s) => s.focus);
  const menu = useFieldHost((s) => s.menu);
  const prompt = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [text, setLocal] = useState(mirrored);
  const [tall, setTall] = useState(false);
  const [multiline, setMultiline] = useState(false);
  // свои правки уходят главному виду асинхронно: эхо собственного ввода не должно перезаписывать поле и сбивать курсор
  const inflight = useRef<string[]>([]);
  const [narrow, setNarrow] = useState(() => window.innerWidth < 720);

  useEffect(() => {
    const echo = inflight.current.lastIndexOf(mirrored);
    if (echo >= 0) { inflight.current.splice(0, echo + 1); return; }
    inflight.current = [];
    setLocal(mirrored);
  }, [mirrored]);

  // высота поля растёт вместе с текстом, но не больше 180 px; главный вид получает нужную высоту подложки
  useLayoutEffect(() => {
    const node = prompt.current;
    if (!node) return;
    const field = node.parentElement!;
    let frame = 0;
    let width = -1;
    const measure = () => {
      const style = getComputedStyle(node);
      const single = parseFloat(style.lineHeight) + parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
      node.style.height = 'auto';
      const natural = node.value ? Math.max(single, node.scrollHeight) : single;
      const height = Math.min(180, natural);
      node.style.height = `${height}px`;
      if (!node.value) { node.scrollTop = 0; node.scrollLeft = 0; }
      const expanded = !recording && natural > single + 1;
      setMultiline(expanded);
      setTall(!recording && natural > 100);
      setNarrow(window.innerWidth < 720);
      onExpanded?.(expanded);
      const surface = getComputedStyle(field);
      const insets = ['paddingTop', 'paddingBottom', 'borderTopWidth', 'borderBottomWidth']
        .reduce((sum, key) => sum + (parseFloat(surface[key as keyof CSSStyleDeclaration] as string) || 0), 0);
      const controls = [...field.children].filter((child) => child !== node && getComputedStyle(child).display !== 'none')
        .map((child) => child.getBoundingClientRect().height);
      void fieldActions.report(Math.ceil(Math.max(54, (recording ? 0 : height) + insets, ...controls.map((value) => value + insets))), expanded);
    };
    const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(measure); };
    measure();
    // переносы меняются и без ввода: при ресайзе окна, плитки, панелей и загрузке шрифта
    const resize = new ResizeObserver(() => {
      const next = node.getBoundingClientRect().width;
      if (Math.abs(next - width) > 0.5) { width = next; schedule(); }
    });
    resize.observe(node);
    window.addEventListener('resize', schedule);
    document.fonts?.addEventListener('loadingdone', schedule);
    return () => { resize.disconnect(); cancelAnimationFrame(frame); window.removeEventListener('resize', schedule); document.fonts?.removeEventListener('loadingdone', schedule); };
  }, [text, scope, recording, baseSize, uiFont, onExpanded]);

  useEffect(() => {
    if (!focus) return;
    prompt.current?.focus({ preventScroll: true });
    if (isMirror) announceComposerFocus();
  }, [focus]);

  // в виде поля файлы тоже можно бросить сверху; в главном виде этим занят Composer
  useEffect(() => {
    if (!isMirror) return undefined;
    const over = (event: DragEvent) => { if (event.dataTransfer?.types.includes('Files')) event.preventDefault(); };
    const drop = (event: DragEvent) => {
      if (!event.dataTransfer?.files.length) return;
      event.preventDefault();
      void uploadFromField([...event.dataTransfer.files], !development);
    };
    document.addEventListener('dragover', over);
    document.addEventListener('drop', drop);
    return () => { document.removeEventListener('dragover', over); document.removeEventListener('drop', drop); };
  }, [development]);

  // щелчок внутри поля закрывает меню «Добавить»: оно открыто в главном виде и мимо-щелчка из этого вида не видит
  useEffect(() => {
    if (!isMirror) return undefined;
    const close = (event: PointerEvent) => {
      if (!fieldHost.get().menu || (event.target as Element).closest('#attach')) return;
      void fieldActions.menu(false);
    };
    document.addEventListener('pointerdown', close, true);
    return () => document.removeEventListener('pointerdown', close, true);
  }, []);

  const ready = !(site && siteClosed) && Boolean(text.trim() || hasFiles || (site && siteReady));
  const queueable = development && running && ready;
  const showStop = (development && running) || (site && siteBusy);
  const placeholder = transcribing ? 'Распознаю речь…' : site ? (sitePlaceholder || 'Спросить Докки') : running ? (settings.enter_sends !== false ? `Агент работает. Enter - в очередь, ${mod()}Enter - прервать и отправить` : `Агент работает. ${mod()}Enter - в очередь`) : search ? 'Что найти в интернете?' : development ? (narrow ? 'Опишите задачу' : 'Опишите задачу для проекта') : 'Спросить Докки';
  const open = menuOpen ?? menu;
  const voiceBusy = recording || transcribing;
  const cancelLabel = transcribing ? 'Отменить распознавание' : 'Отменить запись';

  const change = (value: string) => {
    setLocal(value);
    inflight.current.push(value);
    if (inflight.current.length > 64) inflight.current.shift();
    void fieldActions.setText(value);
  };

  return (
    <div className="composer-field" data-field="live" data-multiline={String(multiline)}>
      <button
        id="attach"
        ref={attach}
        type="button"
        className="composer-icon"
        title={voiceBusy ? cancelLabel : 'Добавить'}
        aria-label={voiceBusy ? cancelLabel : 'Добавить'}
        aria-haspopup={voiceBusy || site ? undefined : 'menu'}
        aria-expanded={voiceBusy || site ? undefined : open}
        hidden={search && !voiceBusy}
        // у страницы сайта своё меню вложений нет смысла повторять: «+» сразу открывает выбор файлов этого вида
        onClick={() => void (voiceBusy ? fieldActions.cancelVoice() : site ? picker.current?.click() : fieldActions.menu())}
      >
        {voiceBusy ? <X aria-hidden="true" /> : <Plus aria-hidden="true" />}
      </button>
      {site && (
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          aria-label="Выбрать файлы"
          onChange={(event) => { const picked = [...(event.target.files ?? [])]; event.target.value = ''; void uploadFromField(picked, true); }}
        />
      )}
      <textarea
        id="prompt"
        ref={prompt}
        rows={1}
        autoFocus
        placeholder={placeholder}
        aria-label="Сообщение Докки"
        data-auto-attach-paste={!search ? 'true' : undefined}
        value={text}
        disabled={transcribing}
        onChange={(event) => change(event.target.value)}
        onPaste={(event) => {
          const pasted = [...event.clipboardData.files];
          if (pasted.length) { event.preventDefault(); void uploadFromField(pasted, !development); return; }
          const source = event.clipboardData.getData('text/plain');
          const file = !search && pastedTextFile(source);
          if (file) {
            event.preventDefault();
            // превращение во вложение не удаляет уже набранный текст и его выделение
            void uploadFromField([file], !development, source).catch(() => {
              change(appendPastedText(prompt.current?.value ?? text, source));
              notify('Не удалось прикрепить вставку. Текст возвращён в поле', true);
            });
          }
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && (settings.enter_sends !== false || event.ctrlKey || event.metaKey)) {
            event.preventDefault();
            if (event.repeat) return;
            if (recording) return void fieldActions.stopVoice();
            // пока агент работает: Enter ставит запрос в очередь, Ctrl+Enter прерывает работу и отправляет сразу
            if (queueable && (event.ctrlKey || event.metaKey) && (settings.enter_sends !== false || event.shiftKey)) return void fieldActions.interrupt();
            void fieldActions.send();
          }
        }}
      />
      {recording && <VoiceWave levels={isMirror ? levelsFromMeter : undefined} />}
      {recording && <VoiceTimer />}
      {tall && !site && (
        <button id="expand-composer" type="button" className="composer-icon expand-composer" title="Развернуть поле ввода" aria-label="Развернуть поле ввода" onClick={() => void fieldActions.expand()}>
          <Maximize2 aria-hidden="true" />
        </button>
      )}
      <button id="voice" type="button" className={`composer-icon${recording ? ' recording' : ''}`} hidden={transcribing} title={recording ? 'Завершить запись и распознать' : 'Голосовой ввод'} aria-label={recording ? 'Завершить запись и распознать' : 'Голосовой ввод'} onClick={() => void fieldActions.toggleVoice()}>
        {recording ? <Square aria-hidden="true" /> : <Mic aria-hidden="true" />}
      </button>
      <button id="interrupt-send" type="button" className="composer-icon" title={`Прервать работу и отправить сейчас (${mod()}Enter)`} aria-label="Прервать работу и отправить сейчас" hidden={!queueable} onClick={() => void fieldActions.interrupt()}>
        <Zap aria-hidden="true" />
      </button>
      <button id="queue-draft" type="button" className="composer-icon" title="Добавить в очередь (Enter)" aria-label="Добавить в очередь" hidden={!queueable} onClick={() => void fieldActions.queue()}>
        <ListPlus aria-hidden="true" />
      </button>
      <button
        id="send"
        type="button"
        className={`composer-icon${ready ? ' ready' : ''}${showStop ? ' is-running' : ''}`}
        data-icon={showStop ? 'stop' : 'send'}
        title={stopping ? 'Останавливаю…' : recording ? 'Распознать и отправить' : showStop ? (site ? 'Остановить генерацию' : 'Остановить выполнение') : 'Отправить'}
        aria-label={stopping ? 'Останавливаю…' : recording ? 'Распознать и отправить' : showStop ? (site ? 'Остановить генерацию' : 'Остановить выполнение') : 'Отправить'}
        disabled={stopping || transcribing || (site && siteClosed && !recording)}
        onClick={() => void (recording ? fieldActions.stopVoice() : fieldActions.sendOrStop())}
      >
        {showStop && !recording ? <Square aria-hidden="true" /> : <ArrowUp aria-hidden="true" />}
      </button>
    </div>
  );
}

/** Длительность записи: видно, что микрофон слушает, и сколько осталось до предела в 5 минут. */
function VoiceTimer() {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 500);
    return () => window.clearInterval(timer);
  }, []);
  return <span className="voice-timer" aria-hidden="true">{Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, '0')}</span>;
}

/** Вид поля целиком: блок #composer без остального содержимого, на который опираются стили записи. */
export function TileComposer() {
  const mirror = useField((s) => s);
  const [expanded, setExpanded] = useState(false);
  const running = useChat((s) => s.running);
  useEffect(() => { document.body.classList.toggle('agent-working', running); }, [running]);
  return (
    <section id="composer" className={fieldClasses(expanded, mirror.dragging, mirror.recording, mirror.transcribing)}>
      <ComposerField onExpanded={setExpanded} />
    </section>
  );
}

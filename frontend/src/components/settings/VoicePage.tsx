import { Check, ChevronLeft, ChevronRight } from 'lucide-react';
import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { cloudPath, put } from '../../lib/api';
import { reducedMotion } from '../../lib/motion';
import { Remote, Section, type Say } from './parts';

interface Voice { id: string; group?: string; title: string; description?: string; gender?: string; age?: string; tags?: string[]; colors?: string[]; demos?: string[] }
interface Catalog { voices: Voice[]; voice?: string; chosen?: string | null }

/* файлы голосов лежат на сайте: приложение берёт их через свой сервер и только по точному имени */
export const asset = (path: string | undefined): string | null => {
  const name = /^voices\/([\w-]{1,120}\.(?:mp3|webp))$/.exec(path ?? '')?.[1];
  return name ? `/api/dokky/voice-assets/${name}` : null;
};
const COLOR = /^#[0-9a-f]{3,8}$/i;

/** Цвета голоса для шара и подсветки сцены; страница не вставляет в стили то, что не похоже на цвет. */
function paint(voice: Voice | undefined): CSSProperties | undefined {
  const colors = (voice?.colors ?? []).filter((color) => COLOR.test(color));
  if (colors.length < 3) return undefined;
  return { '--c1': colors[0], '--c2': colors[1], '--c3': colors[2] } as CSSProperties;
}

function Orb({ voice, speaking, className }: { voice: Voice; speaking: boolean; className?: string }) {
  const cover = asset(`voices/${voice.id}.webp`);
  return (
    <span className={`voice-orb${speaking ? ' speaking' : ''}${className ? ` ${className}` : ''}`} style={paint(voice)} aria-hidden="true">
      <i /><i /><i />
      {cover && <img className="voice-orb-cover" src={cover} alt="" decoding="async" onError={(event) => { event.currentTarget.hidden = true; }} />}
      <span className="voice-orb-shine" />
    </span>
  );
}

function Chips({ voice }: { voice: Voice }) {
  const tags = voice.tags ?? [];
  return (
    <div className="voice-chips">
      {voice.gender && <span className="voice-chip">{voice.gender}</span>}
      {voice.age && <span className="voice-chip">{voice.age}</span>}
      {tags.length > 0 && <span className="voice-chip" title={tags.join(', ')}>+{tags.length}</span>}
    </div>
  );
}

function Bars(): ReactNode {
  return <span className="voice-eq" aria-hidden="true"><i /><i /><i /><i /></span>;
}

/** Голос один на весь экран: новый пример останавливает прошлый. */
function useDemo(say: Say) {
  const [playing, setPlaying] = useState('');
  const sound = useRef<HTMLAudioElement | null>(null);
  const released = useRef('');
  const free = useCallback(() => {
    sound.current?.pause();
    sound.current = null;
    if (released.current) URL.revokeObjectURL(released.current);
    released.current = '';
  }, []);
  const stop = useCallback(() => { free(); setPlaying(''); }, [free]);
  useEffect(() => free, [free]);
  const toggle = (voice: Voice) => {
    if (playing === voice.id) { stop(); return; }
    stop();
    const url = asset(voice.demos?.[0]);
    if (!url) { say('Для этого голоса нет примера'); return; }
    // WebKit просит у аудио куски файла, а прокси отдаёт его целиком: поэтому играем готовый blob
    const audio = new Audio();
    audio.onended = () => { if (sound.current === audio) stop(); };
    sound.current = audio;
    setPlaying(voice.id);
    const fail = () => { if (sound.current === audio) { stop(); say('Не удалось воспроизвести звук'); } };
    fetch(url)
      .then((response) => { if (!response.ok) throw new Error('voice'); return response.blob(); })
      .then((blob) => {
        if (sound.current !== audio) return undefined;
        released.current = URL.createObjectURL(blob);
        audio.src = released.current;
        return audio.play();
      })
      .catch(fail);
  };
  return { playing, toggle, stop };
}

/** Карусель как на сайте: шар выбранного голоса по центру, сцена подсвечена его цветом, ниже описание и выбор. */
function VoicePicker({ slides, chosen, busy, playing, onToggle, onStop, onChoose }: {
  slides: Voice[]; chosen: string; busy: boolean; playing: string; onToggle: (voice: Voice) => void; onStop: () => void; onChoose: (voice: Voice) => void;
}) {
  const track = useRef<HTMLDivElement>(null);
  const steering = useRef<number | null>(null);
  const frame = useRef(0);
  const [current, setCurrent] = useState(() => Math.max(0, slides.findIndex((voice) => voice.id === chosen)));
  const voice = slides[current];

  const center = useCallback((index: number, smooth: boolean) => {
    const box = track.current;
    const slide = box?.children[index] as HTMLElement | undefined;
    if (!box || !slide || typeof box.scrollTo !== 'function') return;
    box.scrollTo({ left: slide.offsetLeft - (box.clientWidth - slide.clientWidth) / 2, behavior: smooth && !reducedMotion() ? 'smooth' : 'auto' });
  }, []);
  const select = (index: number) => {
    const next = Math.max(0, Math.min(slides.length - 1, index));
    if (next !== current) onStop();
    setCurrent(next);
  };
  const go = (index: number) => {
    const next = Math.max(0, Math.min(slides.length - 1, index));
    // пока карусель сама доезжает, промежуточные голоса не выбираются
    steering.current = next;
    center(next, true);
    select(next);
  };
  useEffect(() => { center(current, false); }, [center]); // eslint-disable-line react-hooks/exhaustive-deps -- только первая установка
  useEffect(() => () => cancelAnimationFrame(frame.current), []);
  const onScroll = () => {
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      const box = track.current;
      if (!box) return;
      const middle = box.scrollLeft + box.clientWidth / 2;
      let best = 0;
      let distance = Infinity;
      [...box.children].forEach((node, index) => {
        const slide = node as HTMLElement;
        const away = Math.abs(slide.offsetLeft + slide.clientWidth / 2 - middle);
        if (away < distance) { distance = away; best = index; }
      });
      if (steering.current !== null) { if (best === steering.current) steering.current = null; return; }
      if (best !== current) select(best);
    });
  };
  const release = () => { steering.current = null; };
  if (!voice) return null;
  const mine = chosen === voice.id;
  const speaking = playing === voice.id;
  return (
    <section className="voice-picker" style={paint(voice)} data-setting="Голос">
      <div className="voice-carousel" ref={track} role="listbox" aria-label="Голоса" tabIndex={0} onScroll={onScroll} onPointerDown={release} onWheel={release}
        onKeyDown={(event) => {
          const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
          if (step) { event.preventDefault(); go(current + step); }
        }}
      >
        {slides.map((item, index) => (
          <div key={item.id} className="voice-slide" role="option" aria-selected={index === current}>
            <button type="button" className="voice-orb-button" tabIndex={-1} aria-label={`Прослушать голос ${item.title}`}
              onClick={() => { if (index === current) onToggle(item); else go(index); }}
            >
              <Orb voice={item} speaking={playing === item.id} />
            </button>
            <strong className="voice-slide-title">{item.title}</strong>
          </div>
        ))}
      </div>
      {slides.length > 1 && (
        <>
          <button type="button" className="voice-nav prev" aria-label="Предыдущий голос" disabled={current === 0} onClick={() => go(current - 1)}><ChevronLeft aria-hidden="true" /></button>
          <button type="button" className="voice-nav next" aria-label="Следующий голос" disabled={current === slides.length - 1} onClick={() => go(current + 1)}><ChevronRight aria-hidden="true" /></button>
          <div className="voice-dots">
            {slides.map((item, index) => <button key={item.id} type="button" className="voice-dot" tabIndex={-1} aria-label={item.title} aria-current={index === current} onClick={() => go(index)} />)}
          </div>
        </>
      )}
      <div className="voice-picker-info">
        {voice.description && <p className="voice-description">{voice.description}</p>}
        <Chips voice={voice} />
      </div>
      <button type="button" className={`voice-listen${speaking ? ' speaking' : ''}`} aria-label={`${speaking ? 'Остановить' : 'Послушать'}: ${voice.title}`} onClick={() => onToggle(voice)}>
        <Bars />{speaking ? 'Слушаем' : 'Послушать'}
      </button>
      <button type="button" className={`voice-pick${mine ? ' is-current' : ''}`} disabled={busy || mine} onClick={() => onChoose(voice)}>
        {mine && <Check aria-hidden="true" />}{mine ? 'Голос Докки' : `Выбрать «${voice.title}»`}
      </button>
    </section>
  );
}

function VoiceList({ catalog, say }: { catalog: Catalog; say: Say }) {
  const [chosen, setChosen] = useState(catalog.chosen ?? catalog.voice ?? '');
  const [busy, setBusy] = useState(false);
  const { playing, toggle, stop } = useDemo(say);
  const choose = (voice: Voice) => {
    setBusy(true);
    put(cloudPath('voices/choice'), { voice_id: voice.id })
      .then(() => { setChosen(voice.id); say(`Выбран голос «${voice.title}»`); })
      .catch((error: Error) => say(error.message))
      .finally(() => setBusy(false));
  };
  const recommended = catalog.voices.filter((voice) => voice.group === 'recommended');
  const others = catalog.voices.filter((voice) => voice.group !== 'recommended');
  // сервер не разметил рекомендованные: в карусель идут все, отдельного списка нет
  const featured = recommended.length ? recommended : catalog.voices;
  const rest = recommended.length ? others : [];
  // голос, выбранный не из рекомендованных, стоит первым, чтобы экран открывался на нём. Список фиксируется при открытии: карусель не перестраивается от выбора
  const [slides] = useState(() => {
    const guest = rest.find((voice) => voice.id === (catalog.chosen ?? catalog.voice ?? ''));
    return guest ? [guest, ...featured] : featured;
  });
  return (
    <div className="voice-page">
      <p className="voice-intro">Этим голосом Докки читает ответы, когда вы нажимаете «Озвучить».</p>
      <VoicePicker slides={slides} chosen={chosen} busy={busy} playing={playing} onToggle={toggle} onStop={stop} onChoose={choose} />
      {rest.length > 0 && (
        <Section title="Все голоса">
          {rest.map((voice) => {
            const selected = chosen === voice.id;
            return (
              <article key={voice.id} className="voice-row" data-selected={selected || undefined} data-setting={voice.title}>
                <button type="button" className="voice-orb-button" aria-label={playing === voice.id ? `Остановить: ${voice.title}` : `Послушать: ${voice.title}`} onClick={() => toggle(voice)}>
                  <Orb voice={voice} speaking={playing === voice.id} className="voice-orb-small" />
                </button>
                <div className="voice-copy">
                  <strong>{voice.title}</strong>
                  {voice.description && <span>{voice.description}</span>}
                </div>
                {selected
                  ? <span className="voice-chosen"><Check aria-hidden="true" />Выбран</span>
                  : <button type="button" className="pref-button" disabled={busy} onClick={() => choose(voice)}>Выбрать</button>}
              </article>
            );
          })}
        </Section>
      )}
    </div>
  );
}

export function VoicePage({ say }: { say: Say }) {
  return <Remote<Catalog> path={cloudPath('voices')} retryKey="voices">{(catalog) => <VoiceList catalog={catalog} say={say} />}</Remote>;
}

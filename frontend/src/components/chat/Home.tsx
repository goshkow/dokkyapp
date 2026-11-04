import {
  CalendarClock, Cloud, FolderOpen, GitPullRequest, Images, Layers, ListChecks, MessageCircle, Plus, type LucideIcon,
} from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { relativeTime, serverTime } from '../../lib/format';
import { kickGlass } from '../../lib/glass';
import { MARK } from '../../lib/assets';
import { revealFeed } from '../../lib/feedReveal';
import { EASE, reducedMotion } from '../../lib/motion';
import type { Chat } from '../../lib/types';
import { useDeveloperAccess } from '../../state/access';
import { createProject, openFolder } from '../../state/actions';
import { chatActivity } from '../../state/chatActivity';
import { useStore } from '../../lib/store';
import { useChat } from '../../state/chatCore';
import { useData } from '../../state/data';
import { guarded } from '../../state/overlay';
import { openChat, openLibrary, openPage } from '../../state/remote';
import { useSurface } from '../../state/surface';
import { useUi } from '../../state/ui';
import { useView, type PageKey } from '../../state/view';

/* те же длительности, что у нового экрана на сайте (new-home.js): к центру поле быстро встаёт, вниз стартует мягко */
const MORPH_IN = { duration: 340, easing: EASE };
const MORPH_OUT = { duration: 420, easing: 'cubic-bezier(.5,0,.15,1)' };
const LEAVE_MS = 200;
const CONTINUE_DAYS = 14;

const SECTIONS: { key: Exclude<PageKey, 'chat'>; label: string; sub: string; icon: LucideIcon }[] = [
  { key: 'mission', label: 'Задачи', sub: 'Что сейчас делают агенты', icon: ListChecks },
  { key: 'automations', label: 'Автоматизации', sub: 'Работа по расписанию', icon: CalendarClock },
  { key: 'review', label: 'Проверка изменений', sub: 'Дифф, коммит и пуш', icon: GitPullRequest },
  { key: 'skills', label: 'Навыки', sub: 'Навыки и плагины агента', icon: Layers },
];

/** Сколько ширина чата должна простоять без изменений, прежде чем зажжётся подсветка. */
const GLOW_CALM_MS = 260;
/** До этого момента подсветка ещё бледная: можно начать заново на новом месте, рывка не будет. */
const GLOW_RESTART_MS = 700;

/**
 * Подсветка стоит по центру чата, а рабочая область открывается, когда проект уже известен, и сужает чат.
 * Поэтому свет ждёт, пока ширина успокоится: иначе он зажигается на старом месте и съезжает влево.
 */
function playGlow(light: HTMLElement, main: HTMLElement): () => void {
  let motion: Animation | null = null;
  const start = () => {
    light.hidden = false;
    const current = light.animate(
      [{ opacity: 0, scale: 0.92 }, { opacity: 1, scale: 1, offset: 0.28 }, { opacity: 0.72, scale: 1.03, offset: 0.6 }, { opacity: 0, scale: 1.06 }],
      { duration: 2800, easing: 'ease-out' },
    );
    motion = current;
    const done = () => { if (motion === current) { motion = null; light.hidden = true; } };
    void current.finished.then(done, done);
  };
  let timer = window.setTimeout(start, GLOW_CALM_MS);
  const resized = new ResizeObserver(() => {
    if (motion && Number(motion.currentTime) > GLOW_RESTART_MS) return;
    motion?.cancel();
    motion = null;
    light.hidden = true;
    window.clearTimeout(timer);
    timer = window.setTimeout(start, GLOW_CALM_MS);
  });
  resized.observe(main);
  return () => {
    resized.disconnect();
    window.clearTimeout(timer);
    motion?.cancel();
    motion = null;
    light.hidden = true;
  };
}

/** Новый чат: поле по центру, логотип над ним, под ним «Продолжить» и «Начать». В режиме чата это экран сайта, здесь - свой. */
export function useHomeOn(): boolean {
  const scope = useUi((state) => state.scope);
  const page = useView((state) => state.page);
  const chatOpen = useView((state) => state.chat !== null);
  const started = useChat((state) => state.messages.length > 0 || state.optimistic !== null || state.live.length > 0);
  const siteOwns = useSurface((state) => state.status !== 'off') && scope !== 'development';
  const locked = useDeveloperAccess().locked && scope === 'development';
  return page === 'chat' && !chatOpen && !started && !siteOwns && !locked && scope !== 'search';
}

/** Маскот сайта; пока скрипт грузится, на его месте тот же логотип картинкой. */
function Mascot() {
  const box = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let cancelled = false;
    let destroy = () => {};
    void import('../../lib/mascot-rig.js').then(() => {
      const api = window.DokkyMascot;
      if (cancelled || !api || !box.current) return;
      const host = document.createElement('div');
      host.className = 'welcome-rig';
      box.current.append(host);
      const rig = api.create(host, { followPointer: true, pauseOffscreen: true, reduced: reducedMotion() });
      rig.autoplay(5);
      setReady(true);
      destroy = () => { rig.destroy(); host.remove(); };
    }).catch(() => {});
    return () => { cancelled = true; destroy(); };
  }, []);
  return (
    <div className="welcome-mascot" ref={box} aria-hidden="true">
      {!ready && <img className="welcome-mark" src={MARK} alt="" />}
    </div>
  );
}

function HomeRow({ icon: Icon, title, sub, working, onClick }: { icon: LucideIcon; title: string; sub: string; working?: boolean; onClick: () => void }) {
  return (
    <button type="button" className={`home-row${working ? ' is-working' : ''}`} onClick={onClick}>
      <Icon aria-hidden="true" />
      <span className="home-copy"><b>{title}</b><small>{sub}</small></span>
    </button>
  );
}

const stamp = (chat: Chat): number => serverTime(chat.updated_at ?? chat.created_at) || 0;

function Hints({ rows }: { rows: number }) {
  const development = useUi((state) => state.scope === 'development');
  const localChats = useData((state) => state.localChats);
  const cloudChats = useData((state) => state.cloudChats);
  const projects = useData((state) => state.projects);
  const activity = useStore(chatActivity, (state) => state.activity);
  const recent = useMemo(() => {
    const since = Date.now() - CONTINUE_DAYS * 86_400_000;
    const running = (chat: Chat) => Boolean(activity[String(chat.id)]);
    return (development ? localChats : cloudChats)
      .filter((chat) => running(chat) || stamp(chat) >= since)
      .sort((a, b) => Number(running(b)) - Number(running(a)) || stamp(b) - stamp(a))
      .slice(0, rows);
  }, [activity, cloudChats, development, localChats, rows]);
  const detail = (chat: Chat): string => {
    const now = activity[String(chat.id)];
    if (now) return now === 'question' ? 'Докки ждёт ответа' : 'Докки работает';
    const project = development ? projects.find((item) => item.id === chat.project_id)?.name : '';
    return [project || (development ? 'Без проекта' : 'Чат'), relativeTime(chat.updated_at ?? chat.created_at)].filter(Boolean).join(', ');
  };
  const onlyStart = recent.length === 0;
  const shown = onlyStart ? rows * 2 : rows;
  const start = development
    ? [
      <HomeRow key="open" icon={FolderOpen} title="Открыть проект" sub="Папка на этом компьютере" onClick={guarded(openFolder)} />,
      <HomeRow key="new" icon={Plus} title="Новый проект" sub="Пустая папка для новой работы" onClick={guarded(createProject)} />,
      ...SECTIONS.map((item) => <HomeRow key={item.key} icon={item.icon} title={item.label} sub={item.sub} onClick={guarded(() => openPage(item.key))} />),
    ].slice(0, shown)
    : [<HomeRow key="library" icon={Images} title="Библиотека" sub="Ваши файлы и картинки" onClick={openLibrary} />];
  return (
    <div className={`home-hints${onlyStart ? ' only-start' : ''}`}>
      {!onlyStart && (
        <div className="home-column">
          <p className="home-label">Продолжить</p>
          <div className="home-list">
            {recent.map((chat) => (
              <HomeRow
                key={String(chat.id)}
                icon={development ? MessageCircle : Cloud}
                title={chat.title || 'Новый чат'}
                sub={detail(chat)}
                working={activity[String(chat.id)] === 'working'}
                onClick={guarded(() => openChat(chat))}
              />
            ))}
          </div>
        </div>
      )}
      <div className="home-column">
        <p className="home-label">Начать</p>
        <div className="home-list home-sections">
          {start}
        </div>
      </div>
    </div>
  );
}

/**
 * Экран и полёт поля. Атрибут на #main ставится здесь, а не в разметке: положение поля надо снять до смены и сразу после неё,
 * в одном кадре. Полёт идёт через transform translateY: такое движение стекло под полем проигрывает само (lib/glass.ts).
 */
export function Home() {
  const on = useHomeOn();
  const [leaving, setLeaving] = useState(false);
  const [rows, setRows] = useState(3);
  const stage = useRef<HTMLDivElement>(null);
  const slot = useRef<HTMLDivElement>(null);
  const brand = useRef<HTMLDivElement>(null);
  const hints = useRef<HTMLDivElement>(null);
  const glow = useRef<HTMLDivElement>(null);
  const mounted = useRef(false);

  const measure = useCallback(() => {
    const main = document.getElementById('main');
    const composer = document.getElementById('composer');
    if (!main || !composer || !slot.current) return;
    slot.current.style.height = `${composer.offsetHeight}px`;
    main.style.setProperty('--home-field-y', `${Math.round(slot.current.getBoundingClientRect().top - main.getBoundingClientRect().top)}px`);
    setRows(main.clientHeight < 620 ? 2 : 3);
    kickGlass();
  }, []);

  useLayoutEffect(() => {
    const main = document.getElementById('main');
    const composer = document.getElementById('composer');
    if (!main || !composer) return undefined;
    const first = !mounted.current;
    mounted.current = true;
    const before = composer.getBoundingClientRect().top;
    main.toggleAttribute('data-home', on);
    if (on) measure();
    const delta = before - composer.getBoundingClientRect().top;
    const animate = !reducedMotion();
    if (animate && !first && Math.abs(delta) > 1) {
      composer.animate([{ transform: `translateY(${delta}px)` }, { transform: 'translateY(0px)' }], on ? MORPH_IN : MORPH_OUT);
      kickGlass();
    }
    if (on) {
      setLeaving(false);
      if (animate) {
        [brand.current, hints.current].forEach((node, index) => node?.animate(
          [{ opacity: 0, translate: '0 10px' }, { opacity: 1, translate: '0 0' }],
          { duration: 300, delay: 110 + index * 40, easing: EASE, fill: 'backwards' },
        ));
        if (glow.current) return playGlow(glow.current, main);
      }
      return undefined;
    }
    if (first || !animate) return undefined;
    // сообщения появляются, когда логотип и список уже не мешают
    revealFeed(240, LEAVE_MS + 50);
    setLeaving(true);
    const timer = window.setTimeout(() => setLeaving(false), LEAVE_MS);
    return () => window.clearTimeout(timer);
  }, [measure, on]);

  useEffect(() => {
    if (!on) return undefined;
    const main = document.getElementById('main');
    const composer = document.getElementById('composer');
    if (!main || !composer) return undefined;
    const resize = new ResizeObserver(measure);
    resize.observe(main);
    resize.observe(composer);
    void document.fonts?.ready.then(measure);
    return () => resize.disconnect();
  }, [measure, on]);

  useEffect(() => () => document.getElementById('main')?.removeAttribute('data-home'), []);

  if (!on && !leaving) return null;
  return (
    <div className={`home-stage${on ? '' : ' leaving'}`} ref={stage} inert={!on}>
      <div className="home-glow" ref={glow} hidden aria-hidden="true" />
      <div className="home-brand" ref={brand}>
        <Mascot />
        <span className="home-word">Докки</span>
      </div>
      <div className="home-field-slot" ref={slot} />
      <div ref={hints} className="home-hints-box"><Hints rows={rows} /></div>
    </div>
  );
}

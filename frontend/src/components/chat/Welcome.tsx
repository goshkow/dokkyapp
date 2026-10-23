import { FolderOpen, Plus } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { MARK } from '../../lib/assets';
import { reducedMotion } from '../../lib/motion';
import { createProject, openFolder } from '../../state/actions';
import { guarded } from '../../state/overlay';
import { useDeveloperAccess } from '../../state/access';
import { useData } from '../../state/data';
import { useUi } from '../../state/ui';
import { useView } from '../../state/view';
import { DevGateCard } from './DevGate';

const lines = [
  'Привет, {name}! Чем займёмся?',
  'Рад тебя видеть, {name}. Что обсудим?',
  'С чего начнём сегодня, {name}?',
  'Что для тебя сделать, {name}?',
  'Привет, {name}! Я на связи.',
  'Какая задача у нас сегодня, {name}?',
  'Чем могу помочь, {name}?',
  'Что хочешь узнать, {name}?',
  'Есть идея, {name}? Давай разберёмся.',
  'Готов помочь. С чего начнём, {name}?',
  'Что сегодня интересно, {name}?',
  'Давай начнём, {name}. Что нужно?',
  'Привет, {name}! Над чем поработаем?',
  'Какой вопрос разберём, {name}?',
  'Я здесь, {name}. Чем займёмся?',
  'Что у тебя на уме, {name}?',
  'Слушаю тебя, {name}.',
  'Какой план на сегодня, {name}?',
  'Чем займёмся вместе, {name}?',
  'Расскажи, что нужно, {name}.',
];

const codeLines = [
  'Что соберём сегодня, {name}?',
  'Какой баг ловим, {name}?',
  'Что починим или напишем?',
  'С какой задачи начнём, {name}?',
  'Готов писать код. Что нужно?',
  'Что улучшим в проекте, {name}?',
  'Нужна новая фича или рефакторинг?',
  'Давай соберём что-нибудь, {name}.',
  'Какую задачу решаем, {name}?',
  'Что проверим и протестируем?',
  'Над чем поработаем в коде?',
  'Где искать проблему, {name}?',
];

/** Приветствие: фраза печатается по буквам, как на сайте; в режиме разработки фразы про код и работу. */
function Greeting({ epoch, development }: { epoch: number; development: boolean }) {
  const account = useData((state) => state.account);
  const name = String(account?.user?.display_name || '').trim().split(/\s+/)[0] || 'друг';
  const pool = development ? codeLines : lines;
  const line = useMemo(() => (pool[Math.floor(Math.random() * pool.length)] as string).replace('{name}', name), [epoch, name, pool]);
  const chars = useMemo(() => Array.from(line), [line]);
  const [count, setCount] = useState(() => (reducedMotion() ? chars.length : 0));
  useEffect(() => {
    if (reducedMotion()) {
      setCount(chars.length);
      return undefined;
    }
    setCount(0);
    let index = 0;
    let timer = window.setTimeout(function next() {
      setCount(++index);
      if (index < chars.length) timer = window.setTimeout(next, 26);
    }, 30);
    return () => window.clearTimeout(timer);
  }, [chars]);
  return <h1>{chars.slice(0, count).join('')}</h1>;
}

export function Welcome({ epoch }: { epoch: number }) {
  const development = useUi((state) => state.scope === 'development');
  const project = useView((state) => state.project);
  const locked = useDeveloperAccess().locked;
  // без доступа к Докки Code вместо приветствия и выбора проекта стоит приглашение с планом
  if (development && locked) {
    return (
      <section className="empty welcome locked">
        <div className="welcome-mascot" aria-hidden="true"><img className="welcome-mark" src={MARK} alt="" /></div>
        <DevGateCard />
      </section>
    );
  }
  return (
    <section className="empty welcome typing">
      <div className="welcome-mascot" aria-hidden="true"><img className="welcome-mark" src={MARK} alt="" /></div>
      <Greeting epoch={epoch} development={development} />
      {development && (
        <div className="actions">
          <button type="button" className="secondary" onClick={guarded(openFolder)}><FolderOpen aria-hidden="true" /><span>{project ? project.name : 'Открыть проект'}</span></button>
          <button type="button" className="secondary" onClick={guarded(createProject)}><Plus aria-hidden="true" /><span>Новый проект</span></button>
        </div>
      )}
    </section>
  );
}

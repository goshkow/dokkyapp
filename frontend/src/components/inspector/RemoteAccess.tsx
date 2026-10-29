import { Smartphone } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { guarded } from '../../state/overlay';
import { refreshRemote, setChatShared, sharedOnPhone, useRemote } from '../../state/remoteControl';
import { useView } from '../../state/view';
import { Popover } from '../ui/Popover';

/** Remote access чата: виден ли он на сайте и в Докки на телефоне. Что это - подсказка по наведению. */
export function RemoteAccess() {
  const chatId = useView((s) => s.chat?.id);
  const status = useRemote((s) => s.status);
  const [tip, setTip] = useState(false);
  const row = useRef<HTMLDivElement>(null);
  const timer = useRef(0);
  useEffect(() => { if (chatId !== undefined) refreshRemote(); }, [chatId]);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  if (chatId === undefined) return null;
  const on = sharedOnPhone(status, chatId);
  const available = Boolean(status?.enabled);
  // короткая задержка: подсказка не мигает, когда мышь просто проходит мимо
  const hover = (open: boolean) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setTip(open), open ? 350 : 60);
  };
  return (
    <div ref={row} className="environment-remote" onMouseEnter={() => hover(true)} onMouseLeave={() => hover(false)}>
      <Smartphone aria-hidden="true" />
      <span id="remote-access-label">Remote access</span>
      <button
        type="button"
        role="switch"
        className="pref-switch"
        aria-labelledby="remote-access-label"
        aria-describedby="remote-access-tip"
        aria-checked={on}
        disabled={!available}
        onClick={guarded(() => setChatShared(chatId, !on))}
        onFocus={() => hover(true)}
        onBlur={() => hover(false)}
      />
      <Popover open={tip} anchor={row} onClose={() => setTip(false)} role="tooltip" id="remote-access-tip" className="remote-tip" align="end">
        <span className="remote-tip-icon"><Smartphone aria-hidden="true" /></span>
        <span>
          <b>Remote access</b>
          {available
            ? 'Этот чат виден на сайте Докки и в приложении на телефоне: оттуда его можно читать и продолжать работу. Выключите - чат пропадёт с других устройств, на компьютере он останется.'
            : 'Доступ с других устройств выключен. Включить его можно в настройках, раздел «Доступ с телефона».'}
        </span>
      </Popover>
    </div>
  );
}

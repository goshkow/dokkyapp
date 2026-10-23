import { useEffect, useState } from 'react';
import { isMac } from '../../lib/platform';
import { api } from '../../lib/api';
import { confirmDialog } from '../../state/overlay';
import { Action, Section, Toggle, type Say } from './parts';

export function ComputerControl({ say }: { say: Say }) {
  const [state, setState] = useState<{ enabled: boolean; ok: boolean; accessibility?: boolean; screen_capture?: boolean; elevated?: boolean; can_restart_elevated?: boolean } | null>(null);
  const [error, setError] = useState('');
  useEffect(() => { api<NonNullable<typeof state>>('/api/computer/status').then(setState).catch(() => setError('Не удалось проверить доступ')); }, []);
  return <Section title="Управление приложениями" hint={`Агент работает с выбранным приложением после подтверждения каждого действия. Скриншоты могут содержать личные данные. ${isMac() ? 'В «Системных настройках» нужны разрешения «Универсальный доступ» и «Запись экрана».' : ''} Выключатель останавливает управление; кнопка «Стоп» в чате прерывает задачу.`}>
    {error && <p role="status">{error}</p>}
    {state && <Toggle title="Разрешить Computer Use" detail={state.ok ? `Системный доступ: интерфейс ${state.accessibility ? 'разрешён' : 'не разрешён'}, снимки ${state.screen_capture ? 'разрешены' : 'не разрешены'}.` : 'Нативный помощник недоступен в этой сборке.'} checked={state.enabled} save={async (enabled) => {
      if (enabled && !(await confirmDialog('Разрешить управление приложениями?', 'Каждое действие агент запросит отдельно. Системные разрешения выдаются в настройках ОС. Снимки сохраняются в истории выбранного чата.', 'Разрешить'))) throw new Error('Управление не включено');
      setState(await api('/api/computer/consent', { method: 'PUT', body: JSON.stringify({ enabled }) }));
    }} say={say} />}
    {state?.enabled && state.can_restart_elevated && <Action title="Окна с правами администратора" detail="Обычное приложение не может управлять окнами, запущенными от администратора (установщики, диспетчер задач, службы). Докки перезапустится с правами администратора, система спросит подтверждение. После этого агент управляет и такими окнами; закройте Докки и откройте снова, чтобы вернуться к обычным правам." label="Перезапустить от администратора" onClick={async () => {
      if (!(await confirmDialog('Перезапустить Докки от администратора?', 'Окно закроется и откроется снова с правами администратора. Всё, что делает агент, будет выполняться с этими правами, поэтому подтверждайте его действия внимательно.', 'Перезапустить'))) return;
      try { await api('/api/computer/restart-elevated', { method: 'POST' }); } catch { setError('Не удалось запросить права администратора'); }
    }} />}
    {state?.elevated && <p role="status">Докки запущен с правами администратора: агент управляет и такими окнами.</p>}
  </Section>;
}


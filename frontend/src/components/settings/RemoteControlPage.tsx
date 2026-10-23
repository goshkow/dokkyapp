import { ExternalLink, RefreshCw } from 'lucide-react';
import { useEffect, useState } from 'react';
import { openExternal } from '../../lib/external';
import { useData } from '../../state/data';
import { guarded } from '../../state/overlay';
import { loadRemote, reconnectRemote, saveRemote, stateText, useRemote, type PreventSleep } from '../../state/remoteControl';
import { Action, Pick, Row, Section, Toggle, type Say } from './parts';

const SLEEP: Array<[PreventSleep, string]> = [['working', 'Пока агент работает'], ['on_ac', 'Всегда от сети'], ['off', 'Не мешать']];

export function RemoteControlPage({ say }: { say: Say }) {
  const status = useRemote((s) => s.status);
  const projects = useData((s) => s.projects);
  const [failed, setFailed] = useState('');
  useEffect(() => {
    loadRemote().then(() => setFailed('')).catch((error: Error) => setFailed(error.message));
    // состояние подключения меняется само (сеть, отзыв в вебе): страница его подтягивает, пока открыта
    const timer = window.setInterval(() => { loadRemote().catch(() => undefined); }, 5000);
    return () => window.clearInterval(timer);
  }, []);
  if (!status) {
    if (!failed) return <div className="settings-loading skeleton" aria-label="Загрузка настроек" />;
    return (
      <Section>
        <Row title="Не удалось загрузить настройки" detail={failed}>
          <button type="button" className="pref-button" onClick={() => { setFailed(''); loadRemote().catch((error: Error) => setFailed(error.message)); }}>Повторить</button>
        </Row>
      </Section>
    );
  }
  const site = status.site.replace(/\/+$/, '');
  const state = status.enabled ? stateText[status.state] : '';
  const detail = status.enabled && status.state !== 'online' && status.message ? `${state}. ${status.message}` : state;
  return (
    <>
      <Section hint="Чаты Докки Code с этого компьютера видны в Докки на телефоне и на сайте: сообщения, план, действия и вопросы агента. С телефона можно написать, остановить ход, ответить и разрешить действие. Работа идёт здесь, на компьютере: телефону нужен только интернет.">
        <Toggle
          title="Доступ с телефона"
          detail={detail || 'Выключено: чаты остаются только на этом компьютере.'}
          checked={status.enabled}
          save={(enabled) => saveRemote({ enabled })}
          say={say}
        />
        {status.enabled && status.state === 'revoked' && (
          <Action
            title="Компьютер отключён в вебе"
            detail="Чаты с телефона не видны, пока не подключите снова."
            label="Подключить снова"
            icon={<RefreshCw aria-hidden="true" />}
            onClick={guarded(async () => { await reconnectRemote(); say('Подключаю'); })}
          />
        )}
        {status.enabled && (
          <Row title="Имя компьютера" detail="Под этим именем чаты компьютера видны в списке чатов на телефоне и на сайте.">
            <span className="pref-value">{status.name}</span>
          </Row>
        )}
        {status.enabled && (
          <Action
            title="Открыть на телефоне"
            detail="Войдите на сайт Докки на телефоне тем же аккаунтом: чаты этого компьютера будут в списке чатов под его именем."
            label="Открыть сайт"
            icon={<ExternalLink aria-hidden="true" />}
            onClick={() => openExternal(`${site}/`)}
          />
        )}
      </Section>
      <Section title="Сон компьютера">
        <Pick
          title="Не давать компьютеру засыпать"
          detail={`Пока компьютер спит, телефон его не видит и ход останавливается.${status.prevent_sleep_active ? ' Сейчас компьютер не уснёт.' : ''} Закрытая крышка ноутбука всё равно усыпляет его.`}
          options={SLEEP}
          value={status.prevent_sleep}
          save={(prevent_sleep) => saveRemote({ prevent_sleep })}
          say={say}
        />
      </Section>
      {projects.length > 0 && (
        <Section title="Новый чат с телефона">
          <Pick
            title="Проект"
            detail="В нём откроется чат, начатый с телефона. Если не выбран, берётся проект последнего чата."
            options={[[0, 'Последний'], ...projects.map((project): [number, string] => [project.id, project.name])]}
            value={status.new_session_project_id ?? 0}
            save={(id) => saveRemote({ new_session_project_id: id || null })}
            say={say}
          />
        </Section>
      )}
    </>
  );
}

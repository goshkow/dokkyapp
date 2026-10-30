import { useEffect, useState } from 'react';
import { api, post } from '../../lib/api';
import { PERMISSION_OPTIONS } from '../../lib/permissions';
import { savePermission } from '../../state/chat';
import { scheduleKeepalive } from '../../state/codingSession';
import { saveSettings, useData } from '../../state/data';
import { confirmDialog, guarded } from '../../state/overlay';
import { loadRemote, saveRemote, useRemote } from '../../state/remoteControl';
import { useView } from '../../state/view';
import { saveKey } from './GeneralPage';
import { ComputerControl } from './ComputerControl';
import { MemorySettings } from './MemorySettings';
import { QualitySettings } from './QualitySettings';
import { Block, Pick, Row, Section, Toggle, type Say } from './parts';

interface Recovery { id: number; project_id: number; status: string; state: string; files: Array<{ path: string; current_hash: string }> }

const INSTRUCTIONS_MAX = 12000;

export function DevelopmentPage({ say }: { say: Say }) {
  const settings = useData((s) => s.settings);
  const modes = useData((s) => s.modes);
  const project = useView((s) => s.project);
  const [recovery, setRecovery] = useState<Recovery[]>([]);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const remoteStatus = useRemote((s) => s.status);
  useEffect(() => { loadRemote().catch(() => undefined); }, []);
  useEffect(() => {
    if (!project) return;
    api<Recovery[]>(`/api/projects/${project.id}/write-recovery`).then(setRecovery).catch((reason: Error) => setError(reason.message));
  }, [project, attempt]);
  return (
    <>
      <Section
        title="Личные инструкции"
        hint="Ваши постоянные правила для агента: стиль кода, язык комментариев, привычки команды. Агент получает их в начале диалога как инструкции пользователя, а не как системные."
      >
        <Block
          title="Инструкции для агента"
          detail="Действуют во всех проектах и чатах разработки. Правка не сбрасывает кэш: уже начатый диалог получит только изменение, а новые диалоги сразу начнутся с новой версии."
          value={settings.dev_instructions ?? ''}
          placeholder={'Например:\nКомментарии и коммиты пиши по-русски кратко.\nПеред правкой запускай тесты проекта.\nНе добавляй зависимости без необходимости.'}
          max={INSTRUCTIONS_MAX}
          save={saveKey('dev_instructions')}
          say={say}
        />
      </Section>
      <MemorySettings project={project} say={say} />
      <Section title="Агент">
        <Toggle
          title="Обзор проекта и архив задач"
          detail="Экспериментальные инструменты: сводный обзор исходников и восстановление старых действий по запросу агента. Включаются для новых чатов. Влияние на качество и стоимость модели ещё проверяется."
          checked={settings.coding_runtime_extensions === true}
          save={saveKey('coding_runtime_extensions')}
          say={say}
        />
        <Pick
          title="Режим рассуждений"
          detail="Light быстрее и дешевле, Pro разбирает задачу глубже, Max подходит для самых сложных. Режим можно менять в самом диалоге."
          options={[['light', 'Light'], ['pro', 'Pro'], ['max', 'Max']]}
          value={settings.coding_reasoning_mode}
          save={saveKey('coding_reasoning_mode')}
          say={say}
        />
        <Pick title="Подтверждение действий" detail="Вручную - вы подтверждаете каждое изменение. Автоматически - Докки сам проверяет нужность и безопасность действия и спрашивает вас, если сомневается. Разрешить всё - любые действия без вопросов. Режим действует для выбранного проекта и текущего аккаунта." options={PERMISSION_OPTIONS.map((item) => [item.value, item.label])} value={settings.permission_profile === 'read-only' ? 'ask' : settings.permission_profile || 'ask'} save={(value) => savePermission(value as 'ask' | 'trusted' | 'full')} say={say} />
        {project && (
          <Pick
            title="Локальная сеть"
            detail="Разрешить веб-инструментам обращаться к localhost и устройствам вашей сети в этом проекте."
            options={[['off', 'Запрещено'], ['on', 'Разрешено']]}
            value={settings.private_network ? 'on' : 'off'}
            save={async (value) => {
              if (value === 'on' && !(await confirmDialog('Разрешить доступ к локальной сети?', 'Агент сможет обращаться к локальным сервисам. Не включайте для непроверенного проекта.', 'Разрешить'))) return;
              await saveSettings({ private_network: value === 'on', project_id: project.id });
            }}
            say={say}
          />
        )}
        <Toggle
          title="Продлевать кэш разработки"
          detail="Сохраняет кэш открытого диалога, пока вы работаете. Во время задачи продлевается автоматически; после 5 минут без действий останавливается. Продление платное."
          checked={settings.coding_keepalive === true}
          save={async (value) => { await saveSettings({ coding_keepalive: value }); scheduleKeepalive(); }}
          say={say}
        />
      </Section>
      <Section title="Remote access">
        <Toggle
          title="Remote access для новых чатов"
          detail="Новый чат сразу виден на сайте Докки и в приложении на телефоне. Выключите - новые чаты останутся только на этом компьютере, а показать отдельный чат можно переключателем в «Среде» рабочей области."
          checked={remoteStatus?.share_new !== false}
          disabled={!remoteStatus}
          save={(value) => saveRemote({ share_new: value })}
          say={say}
        />
      </Section>
      <QualitySettings project={project} say={say} />
      <ComputerControl say={say} />
      {project && error && <Section><Row title="Записи файлов" detail={error} /></Section>}
      {project && recovery.length > 0 && (
        <Section title="Незавершённые записи файлов">
          {recovery.map((item) => {
            const label = `${item.files.map((file) => file.path).join(', ')} · ${item.state === 'conflict' ? 'Есть конфликт' : item.state === 'applied' ? 'Изменения на диске' : 'Исходные файлы сохранены'}`;
            return (
              <Row key={item.id} title={label}>
                {item.state !== 'conflict' && item.status === 'prepared' && (
                  <button
                    type="button"
                    className="pref-button"
                    onClick={guarded(async () => {
                      if (!(await confirmDialog('Подтвердить состояние файлов?', label, 'Подтвердить'))) return;
                      await post(`/api/projects/${item.project_id}/write-recovery/${item.id}`, {
                        confirm: true, action: item.state === 'applied' ? 'keep' : 'restore', expected_hashes: Object.fromEntries(item.files.map((file) => [file.path, file.current_hash])),
                      });
                      setAttempt((value) => value + 1);
                    })}
                  >
                    {item.state === 'applied' ? 'Сохранить результат' : 'Закрыть восстановление'}
                  </button>
                )}
              </Row>
            );
          })}
        </Section>
      )}
      <Section title="Режимы агента">
        {modes.map((mode) => (
          <Row key={mode.id} title={mode.name} detail={mode.builtin ? 'Встроенный режим' : `${mode.tools?.length ?? 0} инструментов`} />
        ))}
      </Section>
    </>
  );
}

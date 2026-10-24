import { Download, RefreshCw, RotateCw, X } from 'lucide-react';
import { useEffect } from 'react';
import { ApiError } from '../../lib/api';
import { openExternal, SITE } from '../../lib/external';
import { confirmDialog, guarded } from '../../state/overlay';
import {
  cancelUpdate, checkUpdates, downloadUpdate, installUpdate, loadUpdates, megabytes, setAutoDownload, skipUpdate,
  useUpdates, type UpdateState,
} from '../../state/updates';
import { Meter, Row, Section, Toggle, type Say } from './parts';

/** Прямая ссылка на установщик своей системы с нашего сервера, как кнопки на сайте. */
const downloadLink = (state: UpdateState): string =>
  `${SITE.replace(/\/+$/, '')}/api/v1/updates/download/stable/${state.platform ?? 'macos-arm64'}/${state.kind ?? 'dmg'}`;

const checkedText = (state: UpdateState): string => {
  if (!state.checked_at) return 'Ещё не проверялось';
  const date = new Date(state.checked_at * 1000);
  const today = date.toDateString() === new Date().toDateString();
  return `Последняя проверка: ${today ? 'сегодня' : date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })} в ${date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}`;
};

/** Что сказать, если поставить обновление само нельзя. */
const manualText = (state: UpdateState): string => {
  if (state.method === 'move_to_applications') return 'Перенесите Докки в папку «Программы», чтобы обновления ставились сами. Пока новую версию можно скачать с сайта.';
  if (state.method === 'manual') return 'У этой копии нет прав на изменение своей папки. Скачайте новую версию с сайта и установите её поверх.';
  if (state.reason === 'dev') return 'Обновления работают в установленной программе. Эта копия запущена из исходников.';
  return 'Для этой системы обновлений пока нет.';
};

async function restart(say: Say): Promise<void> {
  try {
    await installUpdate();
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 409 || !/Агент/.test(error.message)) throw error;
    if (!(await confirmDialog('Агент ещё работает', 'Перезапуск остановит текущую задачу. Сделанное сохранится, задачу можно будет продолжить после обновления.', 'Перезапустить'))) return;
    await installUpdate(true);
  }
  say('Докки перезапустится через пару секунд');
}

export function UpdatesPage({ say }: { say: Say }) {
  const state = useUpdates((value) => value);
  useEffect(() => { void loadUpdates().catch(() => undefined); }, []);
  if (!state) return <div className="settings-loading skeleton" aria-label="Загрузка обновлений" />;
  const available = state.available;
  const busy = ['checking', 'downloading', 'preparing', 'installing'].includes(state.status);
  const automatic = ['bundle', 'portable', 'setup', 'setup_elevated'].includes(state.method);
  const disabled = state.status === 'disabled';
  const percent = state.progress ? Math.round((state.progress.done / Math.max(1, state.progress.total)) * 100) : null;
  return (
    <>
      <Section>
        <Row title={`Докки ${state.current}`} detail={disabled ? manualText(state) : state.status === 'checking' ? 'Проверяю…' : state.status === 'up_to_date' ? `Установлена последняя версия. ${checkedText(state)}` : checkedText(state)}>
          {!disabled && (
            <button type="button" className="pref-button" disabled={busy} onClick={guarded(async () => { await checkUpdates(); })}>
              <RefreshCw aria-hidden="true" className={state.status === 'checking' ? 'spin' : undefined} /><span>Проверить обновления</span>
            </button>
          )}
        </Row>
        {state.error && <p className="pref-hint update-error" role="alert">{state.error}</p>}
      </Section>
      {available && !disabled && (
        <Section title={`Доступна версия ${available.version}`} hint={state.status === 'skipped' ? 'Вы пропустили эту версию. Её можно установить в любой момент.' : undefined}>
          {state.status === 'downloading' && state.progress && (
            <Row title="Загрузка" detail={`${megabytes(state.progress.done)} из ${megabytes(state.progress.total)}`}>
              <span className="update-progress"><Meter percent={percent} /><span>{percent}%</span></span>
              <button type="button" className="icon-button" title="Остановить загрузку" aria-label="Остановить загрузку" onClick={guarded(cancelUpdate)}><X aria-hidden="true" /></button>
            </Row>
          )}
          {state.status === 'preparing' && <Row title="Подготовка" detail="Проверяю подпись и собираю новую версию рядом с текущей…" />}
          {state.status === 'ready' && (
            <Row title="Готово к установке" detail="Докки закроется, поставит новую версию и откроется снова. Чаты и настройки сохранятся.">
              <button type="button" className="pref-button primary" onClick={guarded(() => restart(say))}><RotateCw aria-hidden="true" /><span>Перезапустить и обновить</span></button>
            </Row>
          )}
          {state.status === 'installing' && <Row title="Обновляю…" detail="Докки закроется и откроется уже новой версией." />}
          {['available', 'skipped', 'error'].includes(state.status) && automatic && (
            <Row title="Скачать и установить" detail={`Размер загрузки: ${megabytes(available.size)}. Работе не мешает.`}>
              <button type="button" className="pref-button primary" onClick={guarded(downloadUpdate)}><Download aria-hidden="true" /><span>Скачать</span></button>
            </Row>
          )}
          {!automatic && (
            <Row title="Установить вручную" detail={manualText(state)}>
              <button type="button" className="pref-button" onClick={() => { if (!openExternal(downloadLink(state))) say('Не удалось открыть сайт'); }}><Download aria-hidden="true" /><span>Скачать установщик</span></button>
            </Row>
          )}
          {state.status === 'available' && (
            <Row title="Пропустить эту версию" detail="Напомню, когда выйдет следующая.">
              <button type="button" className="pref-button" onClick={guarded(skipUpdate)}><span>Пропустить</span></button>
            </Row>
          )}
        </Section>
      )}
      {!disabled && (
        <Section title="Как обновляться">
          <Toggle
            title="Скачивать обновления сами"
            detail="Новая версия скачивается в фоне и ждёт, когда вам удобно перезапустить. Без этого Докки только сообщит о ней."
            checked={state.auto_download}
            save={setAutoDownload}
            say={say}
          />
        </Section>
      )}
    </>
  );
}

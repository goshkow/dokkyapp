import { Download, Eraser, RefreshCw, RotateCcw } from 'lucide-react';
import { isMac, isWindows } from '../../lib/platform';
import { useEffect, useRef, useState } from 'react';
import { api } from '../../lib/api';
import { cacheClear } from '../../lib/cache';
import { openExternal, SITE } from '../../lib/external';
import { saveSettings, useData } from '../../state/data';
import { openSettings } from '../../state/settingsUi';
import { hasUpdate, useUpdates } from '../../state/updates';
import { confirmDialog, guarded } from '../../state/overlay';
import { saveKey } from './GeneralPage';
import { Action, Pick, Row, Section, Toggle, type Say } from './parts';

function useBuild(): string {
  const [build, setBuild] = useState('');
  useEffect(() => {
    api<{ build_number?: string }>('/api/app/diagnostics').then((value) => setBuild(value.build_number || 'неизвестна')).catch(() => setBuild('недоступна'));
  }, []);
  return build;
}

const APPEARANCE_DEFAULTS = { theme: 'dark', ui_surface: 'classic', ui_font: 'inter', text_size: 14, chat_width: 'standard', ui_motion: 'system', code_font: 'system' } as const;

export function SystemPage({ say }: { say: Say }) {
  const settings = useData((s) => s.settings);
  const build = useBuild();
  // ускорение видеокартой настраивается только в Windows
  const windows = isWindows();
  const acceleration = navigator.userAgent.includes('KoddiRender/software') === (settings.render_mode === 'software')
    ? 'Включено по умолчанию: страницы и окно рисует видеокарта. Выключите, если видеокарта или её оверлей мешают: тогда всё считает процессор (многопоточно, без драйвера видеокарты), чаты прокручиваются так же плавно, но процессор занят больше. Выбор действует со следующего запуска.'
    : 'Изменение вступит в силу после перезапуска приложения.';
  return (
    <>
      <Section title="Запуск">
        <Toggle
          title={isMac() ? 'Запускать при входе в macOS' : 'Запускать вместе с Windows'}
          detail="Докки откроется при входе в систему. Доступно у установленной программы."
          checked={settings.launch_at_login === true}
          save={saveKey('launch_at_login')}
          say={say}
        />
      </Section>
      {windows && <Section title="Отрисовка">
        <Pick title="Аппаратное ускорение" detail={acceleration} options={[['gpu', 'Включено'], ['software', 'Выключено']]} value={settings.render_mode ?? 'gpu'} save={saveKey('render_mode')} say={say} />
      </Section>}
      <Section title="Данные на этом компьютере">
        <Action
          title="Очистить кэш чатов"
          detail="Удаляет локальную копию серверных чатов. Они загрузятся заново; сами чаты и локальные чаты разработки не затрагиваются."
          label="Очистить"
          icon={<Eraser aria-hidden="true" />}
          onClick={guarded(async () => {
            if (!(await confirmDialog('Очистить кэш чатов?', 'Серверные чаты Докки загрузятся заново при следующем открытии.', 'Очистить'))) return;
            cacheClear();
            say('Кэш очищен');
          })}
        />
        <Row title="Журнал приложения" detail="Файл с событиями для поиска причин ошибок. Секреты в журнал не попадают.">
          <a className="pref-button" href="/api/logs/export" download="dokky-log.txt"><Download aria-hidden="true" /><span>Сохранить журнал</span></a>
        </Row>
        <Action
          title="Сбросить внешний вид"
          detail="Вернуть оформление, шрифты, размер текста и ширину чата к значениям по умолчанию."
          label="Сбросить"
          icon={<RotateCcw aria-hidden="true" />}
          onClick={guarded(async () => {
            if (!(await confirmDialog('Сбросить внешний вид?', 'Оформление, шрифты, размер текста, ширина чата и анимации вернутся к значениям по умолчанию.', 'Сбросить'))) return;
            await saveSettings({ ...APPEARANCE_DEFAULTS });
            say('Внешний вид сброшен');
          })}
        />
      </Section>
      <Section title="Сборка">
        <Row title="Версия"><span className="pref-value">{build || '…'}</span></Row>
      </Section>
    </>
  );
}

interface LicenseComponent { id: string; name: string; version: string; license: string; status: string; notices: Array<{ id: string; name: string }> }

export function OpenSourceLicenses() {
  const [catalog, setCatalog] = useState<{ components: LicenseComponent[]; limitations: string[] } | null>(null);
  const [error, setError] = useState('');
  const [text, setText] = useState('');
  const [activeComponent, setActiveComponent] = useState('');
  const [query, setQuery] = useState('');
  const noticeSequence = useRef(0);
  const [busy, setBusy] = useState(false);
  const components = catalog?.components.filter((item) => `${item.name} ${item.version} ${item.license}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())) ?? [];
  const load = async () => {
    setBusy(true); setError(''); setText(''); setActiveComponent(''); noticeSequence.current += 1;
    try { setCatalog(await api('/api/licenses')); } catch { setError('Не удалось загрузить каталог лицензий'); } finally { setBusy(false); }
  };
  return <Section title="Open Source Licenses" hint="Компоненты, версии и исходные уведомления. Нерешённые вопросы проверки явно отмечены в каталоге.">
    <button className="pref-button" type="button" disabled={busy} onClick={() => { void load(); }}>{busy ? 'Загрузка…' : catalog ? 'Обновить каталог' : 'Открыть лицензии'}</button>
    {error && <p role="alert">{error}</p>}
    {catalog?.limitations.map((item) => <p className="pref-hint" key={item}>{item}</p>)}
    {catalog && <input className="pref-input license-search" type="search" aria-label="Найти компонент лицензий" placeholder="Найти компонент" value={query} onChange={(event) => setQuery(event.target.value)} />}
    {catalog?.components.length === 0 && <p role="status">Каталог пуст</p>}
    {catalog && catalog.components.length > 0 && components.length === 0 && <p role="status">Компоненты не найдены</p>}
    {components.map((item) => <details key={item.id} className="pref-license">
      <summary>{item.name} {item.version} - {item.license}</summary>
      {item.status !== 'notice_collected' && <p>Требуется дополнительная проверка перед распространением.</p>}
      {item.notices.map((notice) => <button type="button" className="pref-button" key={notice.id} onClick={() => {
        const sequence = ++noticeSequence.current;
        setActiveComponent(item.id); setText('Загрузка…');
        api<{ text: string }>(`/api/licenses/${encodeURIComponent(item.id)}/${encodeURIComponent(notice.id)}`).then((value) => {
          if (sequence === noticeSequence.current) setText(value.text);
        }).catch(() => { if (sequence === noticeSequence.current) setText('Не удалось прочитать уведомление'); });
      }}>{notice.name}</button>)}
      {activeComponent === item.id && text && <pre className="license-notice" tabIndex={0} aria-label="Текст лицензии">{text}</pre>}
    </details>)}
  </Section>;
}

export function AboutPage() {
  const build = useBuild();
  const update = useUpdates((state) => state);
  const line = update?.status === 'ready' ? `Версия ${update.available?.version} готова к установке`
    : update && hasUpdate(update) ? `Доступна версия ${update.available?.version}`
    : update?.status === 'up_to_date' ? 'Установлена последняя версия' : 'Проверка, скачивание и установка новых версий.';
  return (
    <>
      <Section title="Докки">
        <Row title="Приложение для Windows и macOS" detail="Чаты, поиск и разработка с Докки на вашем компьютере.">
          <span className="pref-value">Версия {build || '…'}</span>
        </Row>
        <Action title="Обновления" detail={line} label="Открыть" icon={<RefreshCw aria-hidden="true" />} onClick={() => openSettings('updates')} />
        <Action title="Сайт Докки" detail="Аккаунт, подписка, правила использования и помощь." label="Открыть сайт" onClick={() => openExternal(SITE)} />
      </Section>
      <OpenSourceLicenses />
    </>
  );
}

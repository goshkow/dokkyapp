import { CircleUserRound, Code2, CreditCard, Info, MessagesSquare, Monitor, Search, Settings, Smartphone, X, type LucideIcon } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { closeSettings, openSettings, useSettingsUi, type SettingsSection } from '../../state/settingsUi';
import { ErrorBoundary } from '../ui/ErrorBoundary';
import { PlainDialog } from '../ui/PlainDialog';
import { AccountPage } from './AccountPage';
import { BillingPage } from './BillingPage';
import { DevelopmentPage } from './DevelopmentPage';
import { GeneralPage } from './GeneralPage';
import { RemoteControlPage } from './RemoteControlPage';
import { AboutPage, SystemPage } from './SystemPages';
import { ConversationPage } from './UsageConversationPages';

interface Entry {
  id: SettingsSection;
  title: string;
  Icon: LucideIcon;
  /** Названия настроек раздела: по ним работает поиск. */
  keywords: string;
}

const groups: Array<{ title: string; items: Entry[] }> = [
  {
    title: 'Настройки',
    items: [
      { id: 'general', title: 'Общие', Icon: Settings, keywords: 'оформление тема тёмная светлая системная поверхность liquid glass стекло шрифт интерфейса размер текста ширина чата анимации код шрифт кода отправка enter' },
      { id: 'account', title: 'Аккаунт', Icon: CircleUserRound, keywords: 'профиль имя фото вход выход обновить аккаунт telegram почта' },
      { id: 'billing', title: 'Подписка и оплата', Icon: CreditCard, keywords: 'планы тариф подписка оплата купить plus premium ultra base автопродление пополнение использования чек лимит остаток окно 5 часов неделя токены' },
      { id: 'conversation', title: 'Общение', Icon: MessagesSquare, keywords: 'полнота ответов подробность кратко источники ссылки память режим по умолчанию быстрый улучшенный авто' },
    ],
  },
  {
    title: 'Разработка',
    items: [
      { id: 'development', title: 'Разработка', Icon: Code2, keywords: 'личные инструкции правила агента режим рассуждений light pro max доступ к проекту разрешения локальная сеть computer use управление приложениями экран доступ кэш продлевать режимы агента восстановление файлов' },
      { id: 'remote', title: 'Доступ с телефона', Icon: Smartphone, keywords: 'телефон смартфон удалённый доступ управление с телефона сайт веб компьютер в сети сон засыпать не давать уснуть питание новый чат с телефона' },
    ],
  },
  {
    title: 'Этот компьютер',
    items: [
      { id: 'system', title: 'Система', Icon: Monitor, keywords: 'запуск вместе с windows автозапуск аппаратное ускорение видеокарта отрисовка очистить кэш журнал сбросить внешний вид сборка' },
      { id: 'about', title: 'О приложении', Icon: Info, keywords: 'версия сайт лицензии сторонние компоненты помощь open source licenses' },
    ],
  },
];

const all = groups.flatMap((group) => group.items);

export function SettingsDialog() {
  const open = useSettingsUi((s) => s.open);
  const section = useSettingsUi((s) => s.section);
  const [feedback, setFeedback] = useState('');
  const [query, setQuery] = useState('');
  const current = all.find((item) => item.id === (section === 'usage' ? 'billing' : section)) ?? (all[0] as Entry);
  const body = useRef<HTMLDivElement>(null);
  const needle = query.trim().toLowerCase();
  const matches = useMemo(() => new Set(all.filter((item) => !needle || `${item.title} ${item.keywords}`.toLowerCase().includes(needle)).map((item) => item.id)), [needle]);
  useEffect(() => { setFeedback(''); }, [section]);
  useEffect(() => {
    if (!feedback) return undefined;
    const timer = window.setTimeout(() => setFeedback(''), 3500);
    return () => window.clearTimeout(timer);
  }, [feedback]);
  useEffect(() => { if (!open) setQuery(''); }, [open]);
  // найденная настройка подсвечивается в открытом разделе
  useEffect(() => {
    const rows = () => [...(body.current?.querySelectorAll<HTMLElement>('.pref-row, .billing-limit') ?? [])];
    const mark = () => {
      let first: HTMLElement | null = null;
      for (const row of rows()) {
        const hit = Boolean(needle) && (row.dataset.setting ?? '').toLowerCase().includes(needle);
        row.classList.toggle('is-match', hit);
        if (hit && !first) first = row;
      }
      first?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    };
    const timer = window.setTimeout(mark, 260);
    return () => window.clearTimeout(timer);
  }, [needle, section, open]);
  const goto = (id: SettingsSection) => openSettings(id);
  return (
    <PlainDialog open={open} onClose={closeSettings} className="settings-window" label="Настройки" closeDelay={140}>
      <div className="settings-layout">
        <aside className="settings-nav">
          <label className="settings-search">
            <Search aria-hidden="true" />
            <input type="search" aria-label="Найти настройку" placeholder="Поиск" value={query} onChange={(event) => setQuery(event.target.value)} />
          </label>
          <nav aria-label="Разделы настроек">
            {groups.map((group) => {
              const items = group.items.filter((item) => matches.has(item.id));
              if (!items.length) return null;
              return (
                <div key={group.title} className="settings-group">
                  <span className="settings-group-title">{group.title}</span>
                  {items.map(({ id, title, Icon }) => (
                    <button key={id} type="button" data-settings-section={id} aria-current={id === section ? 'page' : 'false'} onClick={() => goto(id)}>
                      <Icon aria-hidden="true" /><span>{title}</span>
                    </button>
                  ))}
                </div>
              );
            })}
            {matches.size === 0 && <p className="settings-empty">Ничего не найдено</p>}
          </nav>
        </aside>
        <section className="settings-main">
          <header>
            <h2 id="settings-heading">{current.title}</h2>
            <button type="button" className="icon-button" title="Закрыть настройки" aria-label="Закрыть настройки" onClick={closeSettings}><X aria-hidden="true" /></button>
          </header>
          <div id="settings-body" key={section} ref={body} data-overlay-scroll="">
           <ErrorBoundary key={section} title="Не удалось показать этот раздел настроек">
            {section === 'general' && <GeneralPage say={setFeedback} />}
            {section === 'account' && <AccountPage say={setFeedback} close={closeSettings} />}
            {(section === 'billing' || section === 'usage') && <BillingPage say={setFeedback} />}
            {section === 'conversation' && <ConversationPage say={setFeedback} />}
            {section === 'development' && <DevelopmentPage say={setFeedback} />}
            {section === 'remote' && <RemoteControlPage say={setFeedback} />}
            {section === 'system' && <SystemPage say={setFeedback} />}
            {section === 'about' && <AboutPage />}
           </ErrorBoundary>
          </div>
          <p id="settings-feedback" role="status" aria-live="polite">{feedback}</p>
        </section>
      </div>
    </PlainDialog>
  );
}

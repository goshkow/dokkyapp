import { Mail, MessageCircle, Send } from 'lucide-react';
import { openExternal, SITE } from '../../lib/external';
import { newChat } from '../../state/remote';
import { closeSettings } from '../../state/settingsUi';
import type { Say } from './parts';

const SUPPORT = 'support@goshkow.com';
const FAQ: Array<[string, string]> = [
  ['Как отключить продление?', 'Настройки, затем Подписка и оплата. План работает до конца оплаченного срока.'],
  ['Что будет, если закончился лимит?', 'Когда осталось меньше 10%, можно сбросить использование: докупить 25, 50 или 100%, либо перейти на следующий план.'],
  ['Куда приходит чек?', 'На почту, указанную при оплате. Если письма нет, проверьте папку «Спам» или напишите нам.'],
  ['Как связать сайт и Telegram?', 'Настройки, затем Аккаунт, затем «Открыть на сайте» и раздел Telegram. Дальше отправьте команду боту, страница обновится сама.'],
];
const DOCS: Array<[string, string]> = [['Публичная оферта', 'legal/offer.html'], ['Политика конфиденциальности', 'legal/privacy.html'], ['Условия автоплатежей', 'legal/autopay.html']];

export function HelpPage({ say }: { say: Say }) {
  const ask = () => { closeSettings(); void Promise.resolve(newChat()).catch(() => undefined); };
  const copyMail = () => { void navigator.clipboard.writeText(SUPPORT).then(() => say('Почта скопирована'), () => say(`Почта поддержки: ${SUPPORT}`)); };
  return (
    <div className="help-page">
      <div className="help-tiles">
        <button type="button" className="help-tile" onClick={ask}><MessageCircle aria-hidden="true" /><span><b>Спросить Докки</b><small>Ответит сразу</small></span></button>
        <button type="button" className="help-tile" onClick={() => openExternal('https://t.me/goshkow')}><Send aria-hidden="true" /><span><b>Telegram</b><small>@goshkow</small></span></button>
        <button type="button" className="help-tile" onClick={copyMail}><Mail aria-hidden="true" /><span><b>Почта</b><small>{SUPPORT}</small></span></button>
      </div>
      <div className="pref-group help-faq">
        {FAQ.map(([question, answer]) => <details key={question}><summary>{question}</summary><p>{answer}</p></details>)}
      </div>
      <section className="pref-section">
        <h3>Документы</h3>
        <nav className="help-docs" aria-label="Документы">
          {DOCS.map(([title, path]) => <button key={path} type="button" onClick={() => openExternal(SITE + path)}>{title}</button>)}
        </nav>
      </section>
    </div>
  );
}

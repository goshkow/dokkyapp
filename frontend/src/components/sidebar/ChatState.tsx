import type { ChatMark } from '../../state/chatActivity';

const titles = { working: 'Докки работает', question: 'Докки ждёт вашего ответа', done: 'Докки закончил' } as const;

/** Состояние чата в меню, как на сайте: крутилка, пока работает; оранжевая точка, когда ждёт ответа; синяя - готово. */
export function ChatState({ mark, slot }: { mark: ChatMark; slot?: 'time' }) {
  if (!mark) return null;
  return (
    <span className={`chat-state chat-${mark}`} data-slot={slot} title={titles[mark]} role="img" aria-label={titles[mark]}>
      {mark === 'working' ? <span className="spinner" aria-hidden="true" /> : <span className={mark === 'question' ? 'chat-question-dot' : 'chat-unread-dot'} aria-hidden="true" />}
    </span>
  );
}

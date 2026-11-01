import { ChevronDown, ChevronUp, X } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { closeChatSearch, setChatSearchQuery, stepChatSearch, useChatSearch } from '../../state/chatSearch';

/** Строка поиска по чату: Enter - раньше, Shift+Enter - позже, Esc закрывает. */
export function ChatSearchBar() {
  const open = useChatSearch((s) => s.open);
  const tick = useChatSearch((s) => s.focusTick);
  const query = useChatSearch((s) => s.query);
  const count = useChatSearch((s) => s.messages.length);
  const index = useChatSearch((s) => s.index);
  const busy = useChatSearch((s) => s.busy);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (open) { input.current?.focus(); input.current?.select(); }
  }, [open, tick]);
  if (!open) return null;
  const trimmed = query.trim();
  return (
    <div className="chat-search" role="search">
      <input
        ref={input}
        type="text"
        value={query}
        placeholder="Найти в чате"
        aria-label="Найти в чате"
        spellCheck={false}
        onChange={(event) => setChatSearchQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === 'Enter') {
            event.preventDefault();
            // показ начинается с самого нового совпадения, поэтому Enter идёт к более ранним
            stepChatSearch(event.shiftKey ? 1 : -1);
          } else if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            closeChatSearch();
          }
        }}
      />
      <span className="chat-search-count" aria-live="polite">{!trimmed || busy ? '' : count ? `${index + 1} из ${count}` : 'нет'}</span>
      <button type="button" title="Раньше (Enter)" aria-label="Раньше" disabled={!count} onClick={() => stepChatSearch(-1)}><ChevronUp size={14} aria-hidden="true" /></button>
      <button type="button" title="Позже (Shift+Enter)" aria-label="Позже" disabled={!count} onClick={() => stepChatSearch(1)}><ChevronDown size={14} aria-hidden="true" /></button>
      <button type="button" title="Закрыть (Esc)" aria-label="Закрыть поиск" onClick={() => closeChatSearch()}><X size={14} aria-hidden="true" /></button>
    </div>
  );
}

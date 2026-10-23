import { Minimize2, ArrowUp } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { send } from '../../state/chat';
import { useChat } from '../../state/chatCore';
import { composer, draftContent, setText, upload, useComposer } from '../../state/composer';
import { guarded } from '../../state/overlay';
import { pastedTextFile } from '../../lib/pastedText';
import { useUi } from '../../state/ui';

/** Поле ввода на весь чат для длинного текста, как «Развернуть поле ввода» на сайте Докки. */
export function ExpandedEditor() {
  const open = useComposer((s) => s.expanded);
  const text = useComposer((s) => s.text);
  const running = useChat((s) => s.running);
  const attachments = useComposer((s) => s.attachments);
  const scope = useUi((s) => s.scope);
  const area = useRef<HTMLTextAreaElement>(null);
  const close = () => {
    composer.set({ expanded: false, focus: composer.get().focus + 1 });
  };
  useEffect(() => {
    if (!open) return undefined;
    const node = area.current;
    node?.focus({ preventScroll: true });
    node?.setSelectionRange(node.value.length, node.value.length);
    const key = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.stopPropagation();
      close();
    };
    document.addEventListener('keydown', key, true);
    return () => document.removeEventListener('keydown', key, true);
  }, [open]);
  return (
    <div className={`expanded-editor${open ? ' open' : ''}`} aria-hidden={!open} inert={!open}>
      <div className="expanded-editor-head">
        <button type="button" aria-label="Свернуть поле ввода" title="Свернуть поле ввода" onClick={close}><Minimize2 aria-hidden="true" /></button>
        <span>Сообщение</span>
        <button type="button" aria-label="Отправить" title="Отправить" disabled={(!text.trim() && !attachments.length) || running} onClick={() => { if (draftContent()) { close(); guarded(send)(); } }}><ArrowUp aria-hidden="true" /></button>
      </div>
      <textarea ref={area} aria-label="Текст сообщения" data-auto-attach-paste={scope !== 'search' ? 'true' : undefined} value={text} placeholder="Спросить Докки" onChange={(event) => setText(event.target.value)} onPaste={(event) => {
        if (scope === 'search' || event.clipboardData.files.length) return;
        const source = event.clipboardData.getData('text/plain');
        const file = pastedTextFile(source);
        if (!file) return;
        event.preventDefault();
        void upload([file], scope !== 'development', source);
        close();
      }} />
    </div>
  );
}

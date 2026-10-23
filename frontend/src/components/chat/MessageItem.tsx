import { MessageBlocks, type Block } from './MessageBlocks';
import { ChevronLeft, ChevronRight, Copy, Pencil, Smartphone } from 'lucide-react';
import { memo, useCallback, useMemo, useState } from 'react';
import { copyText } from '../../lib/clipboard';
import { attachCitations } from '../../lib/citations';
import type { CloudFile } from '../../lib/cloud';
import type { Message } from '../../lib/journal';
import { chat, releaseFeed } from '../../state/chatCore';
import { editMessage } from '../../state/chat';
import { useSourcesMode } from '../../state/cloudPrefs';
import { guarded } from '../../state/overlay';
import { useUi } from '../../state/ui';
import { openSources } from '../../state/viewers';
import type { Attachment } from '../../state/chatTypes';
import { CloudSources, MessageAttachments, MessageProgress } from './CloudExtras';
import { AssistantBody, UserBody } from './MessageBody';

interface Variant {
  root: string;
  key: string;
  index: number;
  ids: string[];
}

function Actions({ message, editable, variant }: { message: Message; editable: boolean; variant?: Variant }) {
  const select = (index: number) => {
    if (!variant) return;
    releaseFeed();
    chat.set({ variants: { ...chat.get().variants, [variant.key]: variant.ids[index] as string } });
  };
  return (
    <div className="message-actions">
      {variant && (
        <span className="cloud-variants" role="group" aria-label="Варианты ответа">
          <button type="button" className="icon-button" title="Предыдущий вариант" aria-label="Предыдущий вариант" disabled={variant.index === 0} onClick={() => select(variant.index - 1)}><ChevronLeft aria-hidden="true" /></button>
          <span>{variant.index + 1} / {variant.ids.length}</span>
          <button type="button" className="icon-button" title="Следующий вариант" aria-label="Следующий вариант" disabled={variant.index === variant.ids.length - 1} onClick={() => select(variant.index + 1)}><ChevronRight aria-hidden="true" /></button>
        </span>
      )}
      <button type="button" className="icon-button" title="Скопировать сообщение" aria-label="Скопировать сообщение" onClick={(event) => void copyText(message.content, event, 'Сообщение скопировано')}>
        <Copy aria-hidden="true" />
        {message.role !== 'user' && <span className="copy-label">Скопировать</span>}
      </button>
      {message.role === 'user' && editable && (
        <button type="button" className="icon-button" title="Изменить сообщение" aria-label="Изменить сообщение" onClick={guarded(() => editMessage(message))}>
          <Pencil aria-hidden="true" />
        </button>
      )}
    </div>
  );
}

/** Вложения локального режима приводятся к виду плиток: картинка по ссылке содержимого, остальное - подпись. */
function localFiles(items: unknown[]): CloudFile[] {
  return (items as Attachment[]).map((item) => ({
    id: String(item.id),
    name: item.original_name ?? item.name ?? 'Вложение',
    media_type: item.media_type ?? item.mime_type ?? (item.preview ? 'image/*' : ''),
    viewer_url: item.preview ?? `/api/attachments/${item.id}/content`,
    local: true,
  }));
}

export const MessageItem = memo(function MessageItem({ message, isNew }: { message: Message; isNew: boolean }) {
  const development = useUi((state) => state.scope === 'development');
  const sources = useSourcesMode();
  const metadata = message.metadata ?? {};
  const user = message.role === 'user';
  const running = !user && metadata.status === 'running';
  const [settled, setSettled] = useState(!running);
  const id = String(message.id);
  const pending = id.startsWith('pending');
  const citations = (message.citations ?? []) as unknown[];
  const inline = !development && !user && (sources === 'both' || sources === 'inline') && citations.length > 0;
  const decorate = useCallback((node: HTMLElement) => {
    if (inline) attachCitations(node, message, { open: openSources });
  }, [inline, message]);
  const files = useMemo(() => (development && user && message.attachments?.length ? localFiles(message.attachments) : undefined), [development, user, message.attachments]);
  const failed = metadata.status === 'failed' && !message.content && !metadata.events?.length;
  const variant = metadata.cloud_variant as Variant | undefined;
  const cloudAssistant = !development && !user;
  return (
    <section className={`turn-message ${message.role}${isNew ? ' is-new' : ''}`} data-message-id={id}>
      {cloudAssistant && !running && !pending && <MessageProgress message={message} />}
      {development && user && metadata.origin === 'remote' && <span className="message-origin"><Smartphone aria-hidden="true" />С телефона</span>}
      {user && (files || message.attachments?.length) ? <MessageAttachments message={message} files={files} /> : null}
      {failed ? (
        <p className="error-detail">Ответ прерван. Повторите запрос.</p>
      ) : message.content || (cloudAssistant && message.attachments?.length) ? (
        <article className={`message ${message.role}${running || !settled ? ' streaming' : ''}`}>
          {user ? (
            <UserBody content={message.content} />
          ) : (
            <AssistantBody
              id={id}
              content={message.content}
              running={running}
              message={message}
              onSettled={setSettled}
              decorate={decorate}
              decorateKey={inline ? `i${citations.length}` : ''}
            />
          )}
          {cloudAssistant && settled && !running && <MessageBlocks blocks={metadata.cloud_blocks as Block[] | undefined} />}
          {cloudAssistant && settled && !running && <MessageAttachments message={message} />}
          {!running && settled && !pending && <Actions message={message} editable variant={variant} />}
          {!running && settled && pending && user && <Actions message={message} editable={false} />}
        </article>
      ) : null}
      {cloudAssistant && settled && !running && !pending && (sources === 'both' || sources === 'end') && <CloudSources citations={citations} />}
    </section>
  );
});

import { X } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef } from 'react';
import { safeSource, sourceDomain, sourceLetter } from '../../lib/cloud';
import { closeSources, useViewers } from '../../state/viewers';
import { PlainDialog } from '../ui/PlainDialog';

/* источники одного утверждения */
export function SourcePopover() {
  const state = useViewers((s) => s.sources);
  const last = useRef(state);
  if (state) last.current = state;
  const shown = state ?? last.current;
  const dialog = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const node = document.querySelector<HTMLDialogElement>('dialog.ai-source-popover');
    if (!node || !shown) return;
    const rect = shown.anchor.getBoundingClientRect();
    const box = node.getBoundingClientRect();
    node.style.left = `${Math.max(12, Math.min(rect.left, innerWidth - box.width - 12))}px`;
    node.style.top = `${Math.max(12, Math.min(rect.bottom + 8, innerHeight - box.height - 12))}px`;
  }, [shown, state]);
  useEffect(() => {
    if (!state) return undefined;
    window.addEventListener('resize', closeSources);
    return () => window.removeEventListener('resize', closeSources);
  }, [state]);
  return (
    <PlainDialog open={Boolean(state)} onClose={closeSources} className="ai-source-popover" label="Источники утверждения" closeDelay={0}>
      <div ref={dialog} style={{ display: 'contents' }}>
        <header className="ai-source-popover-head">
          <strong>Источники</strong>
          <button type="button" className="icon-button" title="Закрыть источники" aria-label="Закрыть источники" onClick={closeSources}><X aria-hidden="true" /></button>
        </header>
        {shown?.sources.map((source) => {
          const safe = safeSource(source);
          if (!safe) return null;
          return (
            <a
              key={safe.href}
              className="ai-source-popover-row"
              href={safe.href}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(event) => {
                if (!window.koddiDesktop) return;
                event.preventDefault();
                window.location.href = `koddi://open-url?url=${encodeURIComponent(safe.href)}`;
              }}
            >
              <span className="ai-source-icon" aria-hidden="true">{sourceLetter(source)}</span>
              <span>{source.title || sourceDomain(source)}</span>
            </a>
          );
        })}
      </div>
    </PlainDialog>
  );
}

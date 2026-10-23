import { ChevronDown, ChevronLeft, ChevronRight, Check, Clock, FileCode2, X } from 'lucide-react';
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { playX, snapshotX } from '../../lib/flip';
import {
  actionText, conciseStatus, duration, journalDuration, journalLiveLabel, journalSegments, journalSummary, journalTime, orderedJournalEntries,
  type ActionEvent, type JournalBlock, type JournalEntry, type Message, type MessageMeta,
} from '../../lib/journal';
import { useChat } from '../../state/chatCore';
import { Disclosure } from '../ui/Disclosure';
import { openFile } from '../../state/workspaceRemote';
import { useView } from '../../state/view';
import { guarded } from '../../state/overlay';
import { AssistantBody } from './MessageBody';
import { changeDiffModel } from '../../lib/diff';
import { DiffView } from './DiffView';

const PAGE = 100;

const Dots = () => <i className="journal-dots" aria-hidden="true"><b /><b /><b /><b /></i>;

/** Секундомер идущего этапа: обновляется раз в секунду, пока этап активен. */
function useElapsed(startedAt: number, active: boolean): string {
  const [, tick] = useState(0);
  const peak = useRef(0);
  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setInterval(() => tick((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  if (!active || !Number.isFinite(startedAt)) return '';
  peak.current = Math.max(peak.current, Date.now() - startedAt);
  return peak.current >= 0 ? duration(peak.current) : '';
}

function ActionRow({ entry, canOpen }: { entry: JournalEntry; canOpen: boolean }) {
  const event = entry.event as ActionEvent;
  const result = event.data?.result;
  const change = useChat((state) => result?.change_set_id == null ? undefined : state.changes.find((item) => String(item.id) === String(result.change_set_id)));
  const [expanded, setExpanded] = useState(false);
  const changed = useMemo(() => change ? changeDiffModel(change) : [], [change]);
  const path = (event.data?.arguments as { path?: string } | undefined)?.path;
  const pending = event.event === 'tool.call';
  const openable = Boolean(path && canOpen);
  const open = () => guarded(() => openFile(path as string))();
  return (
    <div className="journal-action">
    <div
      className="activity-line"
      role={openable ? 'button' : undefined}
      tabIndex={openable ? 0 : undefined}
      onClick={openable ? open : undefined}
      onKeyDown={openable ? (key) => { if (key.key === 'Enter' || key.key === ' ') { key.preventDefault(); open(); } } : undefined}
    >
      <span className="action-state">{pending ? <Clock size={14} aria-hidden="true" /> : result?.ok === false ? <X size={14} aria-hidden="true" /> : <Check size={14} aria-hidden="true" />}</span>
      <span>{actionText(event)}</span>
      {(result?.error || result?.reason) && <p className="error-detail">{result.error || result.reason}</p>}
    </div>
    {change && changed.length > 0 && <details className="journal-change" open={expanded} onToggle={(toggle) => setExpanded(toggle.currentTarget.open)}>
      <summary><ChevronRight size={14} aria-hidden="true" /><FileCode2 size={15} aria-hidden="true" /><span>{changed.length === 1 ? changed[0]?.path : `Изменено файлов: ${changed.length}`}</span><b className="positive">+{changed.reduce((sum, file) => sum + file.added, 0)}</b><b className="danger">−{changed.reduce((sum, file) => sum + file.removed, 0)}</b></summary>
      {expanded && <div className="journal-change-body"><DiffView files={changed} id={`change-${change.id}`} inline /></div>}
    </details>}
    </div>
  );
}

function Pager({ count, offset, onChange }: { count: number; offset: number; onChange: (offset: number) => void }) {
  if (count <= PAGE) return null;
  return (
    <div className="journal-pagination">
      <button type="button" title="Предыдущие действия" aria-label="Предыдущие действия" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - PAGE))}><ChevronLeft size={16} aria-hidden="true" /></button>
      <button type="button" title="Следующие действия" aria-label="Следующие действия" disabled={offset + PAGE >= count} onClick={() => onChange(Math.min(count - PAGE, offset + PAGE))}><ChevronRight size={16} aria-hidden="true" /></button>
    </div>
  );
}

function Segment({ block, live, status, canOpen, open, onToggle }: {
  block: JournalBlock; live: boolean; status: string; canOpen: boolean; open: boolean; onToggle: (open: boolean) => void;
}) {
  const running = Boolean(block.running);
  const rows = useMemo(() => block.entries.filter((entry) => entry.type !== 'status' || !/^(Думаю|Подготавливаю)/.test(entry.text ?? '')), [block.entries]);
  const [offset, setOffset] = useState<number | null>(null);
  const end = Math.max(0, rows.length - PAGE);
  const from = Math.min(offset ?? end, end);
  const summary = useMemo(() => journalSummary(block.entries), [block.entries]);
  const actionCount = block.entries.filter((entry) => entry.type === 'action').length;
  const lastEntry = [...block.entries].sort((a, b) => (a.position ?? 0) - (b.position ?? 0)).at(-1) as JournalEntry;
  let copy: string;
  if (running) copy = conciseStatus((live && status) || (lastEntry.type === 'action' ? journalLiveLabel(lastEntry.event as ActionEvent) : lastEntry.text ?? ''));
  else if (actionCount > 1) copy = summary.text;
  else if (lastEntry.type === 'action') copy = actionText(lastEntry.event as ActionEvent);
  else copy = (lastEntry.text ?? '').startsWith('Думаю') ? 'Думал' : 'Работал';
  const showBadge = !running && summary.edits > 0 && (summary.added > 0 || summary.removed > 0);
  const elapsed = useElapsed(journalTime(block.created_at), running);
  const time = running ? elapsed : (journalDuration({ created_at: block.created_at, completed_at: block.end }) ?? '');
  // подпись меняется на другую по длине: счётчик и стрелка плавно переезжают, а не прыгают
  const details = useRef<HTMLDetailsElement>(null);
  const shown = useRef({ copy, before: null as Map<Element, number> | null });
  if (shown.current.copy !== copy && details.current) {
    shown.current.before = snapshotX(details.current.querySelectorAll(':scope > summary > :not(.journal-summary-copy)'));
  }
  useLayoutEffect(() => {
    const before = shown.current.before;
    shown.current = { copy, before: null };
    if (before && !matchMedia('(prefers-reduced-motion: reduce)').matches) playX(before, 240, 'cubic-bezier(0.22, 1, 0.36, 1)');
  }, [copy]);
  return (
    <Disclosure
      innerRef={details}
      className={`activity turn-journal journal-segment${running ? ' is-running' : ''}`}
      data-journal-active={String(running)}
      open={open}
      onOpenChange={onToggle}
      summary={(
        <>
          <Dots />
          <span key={copy} className="journal-summary-copy swap" title={copy}>{copy}</span>
          {showBadge && <span className="journal-badge"><b className="positive">+{summary.added}</b><b className="danger">−{summary.removed}</b></span>}
          <span className="journal-duration">{time}</span>
          <ChevronDown size={14} aria-hidden="true" />
        </>
      )}
    >
      <div className="activity-body">
        <Pager count={rows.length} offset={from} onChange={setOffset} />
        <div className="activity-content journal-history">
          {rows.slice(from, from + PAGE).map((entry) => (
            entry.type === 'action'
              ? <ActionRow key={entry.key} entry={entry} canOpen={canOpen} />
              : <div key={entry.key} className="journal-stage">{entry.text}</div>
          ))}
        </div>
      </div>
    </Disclosure>
  );
}

const messageIdOf = (key: string): string => /^message:(.+):text$/.exec(key)?.[1] ?? key;

export const Journal = memo(function Journal({ message, turnId }: { message: Message; turnId: string }) {
  const metadata = (message.metadata ?? {}) as MessageMeta;
  const turnEnded = useChat((state) => state.turnEnded);
  const liveStatus = useChat((state) => state.liveStatus);
  const project = useView((state) => state.project);
  const running = metadata.status === 'running' && !turnEnded;
  const entries = useMemo(() => orderedJournalEntries(metadata), [metadata]);
  const completed = metadata.status === 'completed' && entries.some((entry) => entry.type === 'final' && entry.complete);
  const blocks = useMemo(() => journalSegments(entries, { ...metadata, status: running ? 'running' : metadata.status === 'running' ? 'completed' : metadata.status }), [entries, metadata, running]);
  const [opened, setOpened] = useState<Record<string, boolean>>({});
  const [group, setGroup] = useState(false);
  const [offset, setOffset] = useState<number | null>(null);
  const end = Math.max(0, blocks.length - PAGE);
  const from = Math.min(offset ?? end, end);
  const visible = blocks.slice(from, from + PAGE);
  const finished = metadata.status === 'failed' || metadata.status === 'cancelled';
  const reason = metadata.status === 'failed' && typeof metadata.turn?.summary === 'string' ? metadata.turn.summary.trim() : '';
  const total = journalDuration(metadata.turn);
  const hidden = !(visible.length || completed || finished || reason);
  const activeKey = [...blocks].reverse().find((block) => block.type === 'segment')?.key;

  const timeline: ReactNode = (
    <div className="journal-window">
      <Pager count={blocks.length} offset={from} onChange={setOffset} />
      <div className="journal-timeline">
        {visible.map((block) => {
          if (block.type === 'compaction') return <div key={block.key} className="journal-stage">{block.text}</div>;
          if (block.type === 'commentary') {
            const entry = block.entries[0] as JournalEntry;
            return (
              <AssistantBody
                key={block.key}
                id={messageIdOf(block.key)}
                content={entry.text ?? ''}
                running={Boolean(running && entry.running && !matchMedia('(prefers-reduced-motion: reduce)').matches)}
                highlight={false}
                variant="commentary"
              />
            );
          }
          return (
            <Segment
              key={block.key}
              block={block}
              live={running && block.key === activeKey}
              status={liveStatus}
              canOpen={Boolean(project)}
              open={opened[block.key] ?? false}
              onToggle={(open) => setOpened((state) => (state[block.key] === open ? state : { ...state, [block.key]: open }))}
            />
          );
        })}
      </div>
    </div>
  );

  if (hidden) return null;
  return (
    <section className="turn-message assistant local-work" data-journal-running={String(running)} data-turn-id={turnId}>
      {completed ? (
        <Disclosure
          className="activity turn-journal journal-completed"
          open={group}
          onOpenChange={setGroup}
              summary={(
            <>
              <Dots />
              <span className="journal-summary-copy">{total ? `Работал ${total}` : 'Работа завершена · время не сохранено'}</span>
              <span className="journal-badge" hidden />
              <span className="journal-duration" />
              <ChevronDown size={14} aria-hidden="true" />
            </>
          )}
        >
          <div className="activity-body"><div className="activity-content journal-history">{timeline}</div></div>
        </Disclosure>
      ) : timeline}
      {finished && (
        <p className={metadata.status === 'failed' ? 'error-detail journal-error' : 'journal-stage journal-outcome'}>
          {(metadata.status === 'failed' ? 'Работа завершилась с ошибкой' : 'Работа остановлена') + (total ? ` · ${total}` : '')}
        </p>
      )}
      {reason && <p className="error-detail journal-error-reason">{reason}</p>}
    </section>
  );
});

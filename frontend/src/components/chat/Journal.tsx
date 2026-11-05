import { ChevronDown, ChevronLeft, ChevronRight, Check, Clock, FileCode2, MousePointer2, X } from 'lucide-react';
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { playX, snapshotX } from '../../lib/flip';
import {
  actionText, conciseStatus, duration, journalDuration, journalLiveLabel, journalSegments, journalSummary, journalTime, orderedJournalEntries,
  type ActionEvent, type JournalBlock, type JournalEntry, type Message, type MessageMeta,
} from '../../lib/journal';
import { ApiError, post } from '../../lib/api';
import { currentChatId, useChat } from '../../state/chatCore';
import { terminalTail, useCommandOutput } from '../../state/commandOutput';
import { useSubagentRun } from '../../state/subagents';
import { Markdown } from './Markdown';
import { Disclosure } from '../ui/Disclosure';
import { openFile } from '../../state/workspaceRemote';
import { useView } from '../../state/view';
import { guarded, notify } from '../../state/overlay';
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

// команды, вывод которых идёт в ленту вживую (все они исполняются через shell.run)
const COMMAND_TOOLS = new Set(['shell.run', 'test.run', 'build.run', 'lint.run', 'format.run']);

/** Идущая команда: хвост вывода, как в терминале, и остановка только её - ход агента продолжается. */
export function CommandLive({ id }: { id: string }) {
  const output = useCommandOutput(id);
  const live = useChat((state) => state.running && !state.turnEnded);
  const [stopping, setStopping] = useState(false);
  const screen = useRef<HTMLPreElement>(null);
  const tail = useMemo(() => terminalTail(output), [output]);
  useLayoutEffect(() => {
    const node = screen.current;
    // прокрутка вниз только если человек не листает вывод выше
    if (node && node.scrollHeight - node.scrollTop - node.clientHeight < 48) node.scrollTop = node.scrollHeight;
  }, [tail]);
  const stop = async () => {
    setStopping(true);
    try {
      await post(`/api/chats/${currentChatId()}/tools/${encodeURIComponent(id)}/stop`);
    } catch (error) {
      // 404 - команда закончилась сама, пока человек нажимал
      if (!(error instanceof ApiError && error.status === 404)) {
        setStopping(false);
        notify((error as Error).message || 'Не удалось остановить команду', true);
      }
    }
  };
  if (!live) return null;
  return (
    <div className="command-live">
      {tail && <pre ref={screen} className="command-screen" aria-live="off">{tail}</pre>}
      <button type="button" className="command-stop" disabled={stopping} onClick={() => void stop()} title="Остановить эту команду, агент продолжит работу">
        <span className="command-stop-mark" aria-hidden="true" />{stopping ? 'Останавливаю…' : 'Остановить'}
      </button>
    </div>
  );
}

/** Идущий сабагент: его текущее действие одной тихой строкой, как у Claude Code. Свои шаги он ведёт в своём контексте. */
export function SubagentLive({ id }: { id: string }) {
  const run = useSubagentRun(id);
  const live = useChat((state) => state.running && !state.turnEnded);
  if (!live) return null;
  const steps = run?.actions ?? 0;
  return (
    <p className="subagent-live">
      {run?.last || 'Начинаю'}
      {steps > 0 && <span> · шагов: {steps}</span>}
    </p>
  );
}

interface ComputerState { app: string; steps: number; current: string; image: string }

/** Сводка работы с приложением в этапе: какое приложение, что делается сейчас, последний снимок окна. */
export function computerState(entries: JournalEntry[]): ComputerState | null {
  let state: ComputerState | null = null;
  for (const entry of [...entries].sort((a, b) => (a.position ?? 0) - (b.position ?? 0))) {
    const event = entry.event as ActionEvent | undefined;
    if (entry.type !== 'action' || event?.data?.name !== 'computer.use') continue;
    const args = (event.data.arguments ?? event.data.args ?? {}) as { app?: unknown };
    const result = event.data.result as { data?: { image_url?: unknown } } | undefined;
    state ??= { app: '', steps: 0, current: '', image: '' };
    state.steps += 1;
    if (typeof args.app === 'string' && args.app.trim()) state.app = args.app.trim();
    state.current = event.event === 'tool.call' ? journalLiveLabel(event) : actionText(event);
    const image = result?.data?.image_url;
    // снимок только из своих вложений: внешний адрес в ленту не попадает
    if (typeof image === 'string' && image.startsWith('/api/attachments/')) state.image = image;
  }
  return state;
}

/** Идёт работа с приложением: тихая карточка, как у Codex и Claude, с остановкой хода. */
export function ComputerLive({ state }: { state: ComputerState }) {
  const [stopping, setStopping] = useState(false);
  const stop = async () => {
    setStopping(true);
    try {
      const { stopRun } = await import('../../state/chat');
      await stopRun();
    } catch (error) {
      setStopping(false);
      notify((error as Error).message || 'Не удалось остановить работу', true);
    }
  };
  return (
    <div className="computer-live" role="status" aria-live="polite">
      {state.image && <img className="computer-live-shot" src={state.image} alt={state.app ? `Окно ${state.app}` : 'Окно приложения'} />}
      <div className="computer-live-copy">
        <b><MousePointer2 size={13} aria-hidden="true" />{state.app ? `Докки работает в ${state.app}` : 'Докки работает с приложением'}</b>
        <span title={state.current}>{state.current}{state.steps > 1 ? ` · шагов: ${state.steps}` : ''}</span>
      </div>
      <button type="button" className="command-stop" disabled={stopping} onClick={() => void stop()} title="Остановить работу агента">
        <span className="command-stop-mark" aria-hidden="true" />{stopping ? 'Останавливаю…' : 'Остановить'}
      </button>
    </div>
  );
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
  const command = COMMAND_TOOLS.has(String(event.data?.name ?? ''));
  const delegated = event.data?.name === 'task.delegate';
  const report = delegated && !pending && typeof result?.summary === 'string' ? result.summary.trim() : '';
  const printed = command && !pending ? terminalTail([result?.stdout, result?.stderr].filter((part) => typeof part === 'string' && part.trim()).join('\n'), 400) : '';
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
    {command && pending && <CommandLive id={String(event.data?.id ?? '')} />}
    {delegated && pending && <SubagentLive id={String(event.data?.id ?? '')} />}
    {report && <details className="command-result">
      <summary><ChevronRight size={14} aria-hidden="true" /><span>Отчёт агента</span></summary>
      <Markdown text={report} className="message-body subagent-report" highlight={false} />
    </details>}
    {printed && <details className="command-result">
      <summary><ChevronRight size={14} aria-hidden="true" /><span>Вывод команды</span></summary>
      <pre className="command-screen">{printed}</pre>
    </details>}
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
  const computer = useMemo(() => (running && live ? computerState(block.entries) : null), [block.entries, running, live]);
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
    <>
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
    {computer && <ComputerLive state={computer} />}
    </>
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

import { CircleAlert, Hourglass, RotateCw } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { announceAgentNotice } from '../../lib/bridge';
import type { Message } from '../../lib/journal';
import { limitState, recoveryKind, turnErrorCode, untilText, type RecoveryKind } from '../../lib/recovery';
import { resumeChat } from '../../state/chat';
import { useChat } from '../../state/chatCore';
import { limitsStore, loadLimits, useLimits } from '../../state/limits';
import { guarded } from '../../state/overlay';
import { nextPlan, usePlans } from '../../state/plans';
import { openBilling } from '../../state/settingsUi';
import { useView } from '../../state/view';
import { resetTime } from './UsageNotice';

interface Failure { kind: RecoveryKind; turnId: string }

/** Последний ход оборвался сбоем сервера или лимитом - тогда над полем плашка, иначе ничего. */
export function lastFailure(messages: Message[]): Failure | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const turn = (messages[index]?.metadata as { turn?: { id?: unknown; status?: string; error_json?: unknown } } | undefined)?.turn;
    if (!turn) continue;
    if (turn.status !== 'failed') return null;
    const kind = recoveryKind(turnErrorCode(turn.error_json));
    return kind ? { kind, turnId: String(turn.id ?? '') } : null;
  }
  return null;
}

const AUTO_KEY = 'dokky.autoResume';
const readAuto = (): boolean => {
  try {
    return localStorage.getItem(AUTO_KEY) === '1';
  } catch {
    return false;
  }
};
const writeAuto = (value: boolean): void => {
  try {
    localStorage.setItem(AUTO_KEY, value ? '1' : '0');
  } catch {
    /* хранилище недоступно - галочка живёт до перезагрузки */
  }
};

// сам продолжает не чаще раза в 15 минут на чат: если сервер снова отказал по лимиту, новые ходы по кругу не идут
const AUTO_GAP = 15 * 60_000;
const autoTried = new Map<number, number>();
export const resetAutoResume = (): void => autoTried.clear();

/** Часы плашки лимита: пересчитывают «через N мин» и включают кнопку, когда время пришло. */
function useNow(active: boolean, step = 15_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), step);
    return () => window.clearInterval(timer);
  }, [active, step]);
  return now;
}

/** Плашка после оборванного хода: «Повторить попытку» при сбое сервера, «Продолжить работу» после лимита. */
export function RecoveryBanner() {
  const messages = useChat((state) => state.messages);
  const running = useChat((state) => state.running);
  // пока грузится другой чат, в ленте ещё сообщения прежнего: плашка и автопродолжение ждут
  const ready = useChat((state) => state.load === 'ready');
  const chatId = useView((state) => state.chat?.id);
  const title = useView((state) => state.chat?.title ?? '');
  const failure = useMemo(() => (running || !ready ? null : lastFailure(messages)), [messages, running, ready]);
  const limits = useLimits((state) => state.value);
  const upper = usePlans((state) => nextPlan(state.catalog));
  const [busy, setBusy] = useState(false);
  const [auto, setAuto] = useState(readAuto);
  const limit = failure?.kind === 'limit';
  const now = useNow(limit);
  const { restored, overall, resetsAt } = limitState(limits, now);
  const key = `${String(chatId ?? '')}:${failure?.turnId ?? ''}`;

  useEffect(() => {
    if (limit) void loadLimits();
  }, [limit, key]);
  // после времени сброса лимиты перечитываются: сервер скажет, действительно ли окно открылось
  const due = limit && Number.isFinite(resetsAt) && now >= resetsAt;
  useEffect(() => {
    if (due) void loadLimits();
  }, [due]);

  const resume = async (): Promise<void> => {
    if (busy || chatId == null) return;
    setBusy(true);
    try {
      // false - чат сменился или уже идёт ход: плашка остаётся, нажать можно снова
      await resumeChat(Number(chatId));
    } finally {
      setBusy(false);
    }
  };

  // автопродолжение: только пока окно Докки живо (в том числе спрятано в трей) и галочка стоит
  useEffect(() => {
    if (!limit || !restored || !auto || busy || chatId == null) return;
    const id = Number(chatId);
    if (Date.now() - (autoTried.get(id) ?? 0) < AUTO_GAP) return;
    autoTried.set(id, Date.now());
    void (async () => {
      // решает свежий ответ сервера, а не значение из кэша
      await loadLimits();
      if (!limitState(limitsStore.get().value).restored) return;
      announceAgentNotice(id, 'limit', title);
      await guarded(resume)();
    })();
    // повтор только при смене хода, восстановлении лимита или галочке
  }, [limit, restored, auto, key]);

  if (!failure || chatId == null) return null;
  if (failure.kind === 'stalled') {
    return (
      <section className="approval-panel recovery-panel" role="alert">
        <b><CircleAlert aria-hidden="true" />Докки временно не отвечает</b>
        <p>Повторы не помогли. Попробуйте повторить попытку позже - история и задача сохранены, работа продолжится с того же места.</p>
        <div className="actions">
          <button type="button" className="primary" disabled={busy} onClick={guarded(resume)}><RotateCw aria-hidden="true" /><span>Повторить попытку</span></button>
        </div>
      </section>
    );
  }
  const when = Number.isFinite(resetsAt) ? resetTime(limits?.windowResetsAt ?? null) : '';
  const text = restored
    ? 'Лимит восстановился - можно продолжить работу.'
    : overall
      ? 'Использование по подписке закончилось. Сбросьте использование или улучшите план - задача сохранена и продолжится с того же места.'
      : when
        ? `Лимит восстановится ${when.includes(' ') ? '' : 'в '}${when} (${untilText(resetsAt, now)}). Задача сохранена, работа продолжится с того же места.`
        : 'Лимит скоро восстановится. Задача сохранена, работа продолжится с того же места.';
  return (
    <section className="approval-panel recovery-panel" role="status" aria-live="polite">
      <b><Hourglass aria-hidden="true" />Закончился лимит использования</b>
      <p>{text}</p>
      <div className="actions">
        {!overall && (
          <label className="recovery-auto">
            <input type="checkbox" checked={auto} onChange={(event) => { setAuto(event.target.checked); writeAuto(event.target.checked); }} />
            <span>Продолжить автоматически</span>
          </label>
        )}
        {upper && !restored && <button type="button" onClick={guarded(() => openBilling(upper.id))}><span>Улучшить до {upper.name}</span></button>}
        <button type="button" className="primary" disabled={!restored || busy} onClick={guarded(resume)}><span>Продолжить работу</span></button>
      </div>
    </section>
  );
}

import { CircleCheck, CircleDashed, CircleSlash, CircleX, LoaderCircle, ChevronRight, Download, PanelRight, Play } from 'lucide-react';
import { useMemo, useState } from 'react';
import { executePlan } from '../../state/chat';
import { planDisplaySteps } from '../../state/chatAgent';
import { useChat } from '../../state/chatCore';
import type { PlanSnapshot, PlanStep } from '../../state/chatTypes';
import { guarded } from '../../state/overlay';
import { selectInspector } from '../../state/workspaceRemote';
import { Disclosure } from '../ui/Disclosure';
import { Markdown } from './Markdown';

const labels: Record<string, string> = { pending: 'Ожидает', running: 'Выполняется', completed: 'Готово', blocked: 'Заблокирован', cancelled: 'Отменён', failed: 'Ошибка', interrupted: 'Не завершён' };

/** шаги плана: остановленный пункт не продолжает изображать выполнение */
export function PlanList({ steps, status }: { steps: PlanStep[]; status?: string }) {
  const displayed = planDisplaySteps(steps, status);
  return (
    <ol className="plan-list">
      {displayed.map((step, index) => {
        const Icon = step.status === 'running' ? LoaderCircle : step.status === 'completed' ? CircleCheck : step.status === 'cancelled' || step.status === 'interrupted' ? CircleSlash : step.status === 'failed' || step.status === 'blocked' ? CircleX : CircleDashed;
        return (
          <li key={index} className={step.status} aria-label={`${labels[step.status] ?? labels.pending}: ${step.title}`} aria-current={step.status === 'running' ? 'step' : undefined}>
            <Icon className="plan-status" size={16} aria-hidden="true" />
            <span>{step.title}</span>
          </li>
        );
      })}
    </ol>
  );
}

/** Сколько шагов готово: кольцо вместо отдельного списка на весь экран. */
function PlanRing({ part }: { part: number }) {
  return (
    <svg className="plan-ring" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="8" r="6.25" />
      <circle className="plan-ring-value" cx="8" cy="8" r="6.25" pathLength={1} strokeDasharray={`${part} 1`} />
    </svg>
  );
}

const STOPPED = ['failed', 'blocked', 'cancelled', 'interrupted'];

/** Ход плана в ленте одной строкой: текущий шаг и счётчик, весь список по щелчку. */
export function LivePlan({ steps, snapshot }: { steps: PlanStep[]; snapshot?: PlanSnapshot }) {
  const [open, setOpen] = useState(false);
  const done = steps.filter((step) => step.status === 'completed').length;
  const shown = planDisplaySteps(steps, snapshot?.tracking_status);
  const stopped = shown.find((step) => STOPPED.includes(step.status));
  const current = shown.find((step) => step.status === 'running') ?? stopped ?? shown.find((step) => step.status === 'pending');
  const finished = steps.length > 0 && done === steps.length;
  return (
    <section id="live-plan" className="live-plan" aria-label="Ход выполнения плана" data-stopped={!finished && current === stopped ? '' : undefined}>
      <Disclosure
        open={open}
        onOpenChange={setOpen}
        summary={(
          <>
            <PlanRing part={steps.length ? done / steps.length : 0} />
            <span className="live-plan-current">{finished ? 'План выполнен' : current?.title ?? 'План'}</span>
            <span className="live-plan-count" role="status" aria-live="polite">{done} из {steps.length}</span>
            <ChevronRight aria-hidden="true" />
          </>
        )}
      >
        <div className="live-plan-steps"><PlanList steps={steps} status={snapshot?.tracking_status} /></div>
      </Disclosure>
    </section>
  );
}

const stepsWord = (count: number) => {
  const tens = count % 100;
  const ones = count % 10;
  if (tens > 10 && tens < 20) return 'шагов';
  return ones === 1 ? 'шаг' : ones > 1 && ones < 5 ? 'шага' : 'шагов';
};

/** План реализации одной строкой: действия сразу под рукой, сам документ раскрывается по щелчку. */
export function PlanApproval({ snapshot }: { snapshot: PlanSnapshot }) {
  const running = useChat((state) => state.running);
  const busy = useChat((state) => state.reasoningBusy);
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const heading = useMemo(() => /^#\s+(.+)$/m.exec(snapshot.document ?? '')?.[1] ?? 'План реализации', [snapshot.document]);
  const count = snapshot.steps?.length ?? 0;
  const note = snapshot.ready ? (count ? `${count} ${stepsWord(count)}` : 'plan.md') : snapshot.executed ? 'Принят к выполнению' : running ? 'Составляется…' : 'Сохранён';
  const download = () => {
    const url = URL.createObjectURL(new Blob([snapshot.document ?? ''], { type: 'text/markdown;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'plan.md';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const execute = guarded(async () => {
    if (running || busy || pending) return;
    setPending(true);
    try {
      await executePlan(snapshot);
    } finally {
      setPending(false);
    }
  });
  return (
    <section id="plan-approval" className="plan-document" data-open={open ? '' : undefined}>
      <div className="plan-bar">
        <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
          <summary aria-controls="plan-document-content" aria-expanded={open}>
            <ChevronRight aria-hidden="true" />
            <strong>{heading}</strong>
            <small>{note}</small>
          </summary>
        </details>
        <div className="plan-actions">
          <button type="button" className="icon-button" aria-label="Скачать план" title="Скачать план" onClick={download}><Download aria-hidden="true" /></button>
          <button type="button" className="icon-button" aria-label="Открыть в панели" title="Открыть в панели" onClick={guarded(() => selectInspector('plan'))}><PanelRight aria-hidden="true" /></button>
          {snapshot.ready ? <button type="button" className="primary" aria-label="Реализуй план" disabled={pending} onClick={execute}><Play aria-hidden="true" /><span>Реализуй план</span></button> : null}
        </div>
      </div>
      <div id="plan-document-content" className="plan-document-body message-body" inert={!open}>
        <Markdown text={snapshot.document ?? ''} className="" highlight={false} />
      </div>
    </section>
  );
}

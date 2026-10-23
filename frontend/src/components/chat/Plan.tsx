import { CircleCheck, CircleDashed, CircleSlash, CircleX, LoaderCircle, ChevronRight, Download, FileText, PanelRight, Play } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import { executePlan } from '../../state/chat';
import { planDisplaySteps } from '../../state/chatAgent';
import { useChat } from '../../state/chatCore';
import type { PlanSnapshot, PlanStep } from '../../state/chatTypes';
import { guarded } from '../../state/overlay';
import { selectInspector } from '../../state/workspaceRemote';
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

/** Ход выполнения плана прямо в ленте, сразу после журнала хода. */
export function LivePlan({ steps, snapshot }: { steps: PlanStep[]; snapshot?: PlanSnapshot }) {
  const done = steps.filter((step) => step.status === 'completed').length;
  return (
    <section id="live-plan" className="live-plan" aria-label="Ход выполнения плана">
      <div className="live-plan-heading" role="status" aria-live="polite">План · {done} из {steps.length}</div>
      <div className="live-plan-steps"><PlanList steps={steps} status={snapshot?.tracking_status} /></div>
    </section>
  );
}

/** План реализации документом: сворачивается, скачивается и по кнопке уходит в работу. */
export function PlanApproval({ snapshot }: { snapshot: PlanSnapshot }) {
  const running = useChat((state) => state.running);
  const busy = useChat((state) => state.reasoningBusy);
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const body = useRef<HTMLDivElement>(null);
  const heading = useMemo(() => /^#\s+(.+)$/m.exec(snapshot.document ?? '')?.[1] ?? 'План реализации', [snapshot.document]);
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
    <section id="plan-approval" className="plan-document">
      <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary aria-controls="plan-document-content" aria-expanded={open}>
          <ChevronRight aria-hidden="true" />
          <span><strong>{heading}</strong><small>plan.md</small></span>
          <FileText aria-hidden="true" />
        </summary>
      </details>
      <div id="plan-document-content" className="plan-document-body message-body" ref={body} inert={!open}>
        <Markdown text={snapshot.document ?? ''} className="" highlight={false} />
      </div>
      <footer>
        <div className="actions">
          <button type="button" className="secondary" onClick={download}><Download aria-hidden="true" /><span>Скачать план</span></button>
          <button type="button" className="secondary" onClick={guarded(() => selectInspector('plan'))}><PanelRight aria-hidden="true" /><span>Открыть в панели</span></button>
        </div>
        {snapshot.ready ? (
          <button type="button" className="primary" disabled={pending} onClick={execute}><Play aria-hidden="true" /><span>Реализуй план</span></button>
        ) : (
          <span className="muted">{snapshot.executed ? 'План принят к выполнению' : running ? 'План составляется…' : 'План сохранён'}</span>
        )}
      </footer>
    </section>
  );
}

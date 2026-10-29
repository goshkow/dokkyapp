import { Bug, Check, Flame, Play, RefreshCw, Trash2, Wrench } from 'lucide-react';
import { useEffect, useState } from 'react';
import { guarded } from '../../state/overlay';
import {
  applyProposal, decide, deleteProposal, GATE_LABELS, gateDetail, latestByGate, loadQuality, runQuality, STAGE_LABELS, STATUS_LABELS,
  toggleHeat, useQuality, type Finding, type GateRun,
} from '../../state/quality';
import { useView } from '../../state/view';
import { openFile } from '../../state/workspace';

type DecidableGate = 'dry' | 'mutation' | 'architecture';
const DECIDABLE = new Set<string>(['dry', 'mutation', 'architecture']);
const DECISION_LABELS = { review: 'на разборе', accepted: 'принято человеком', refactor: 'к исправлению' } as const;
const FINDINGS_SHOWN = 6;
const DECISION_KINDS: Record<string, string> = {
  approach: 'подход', tradeoff: 'компромисс', scope: 'объём', rollback: 'откат', premise: 'предпосылка',
  root_cause: 'причина', architecture: 'архитектура', quality: 'качество', other: 'другое',
};

function where(item: Finding): string {
  if (!item.path) return item.symbol || item.key || '';
  return item.line_start ? `${item.path}:${item.line_start}` : item.path;
}

/** Что именно найдено: у мутанта - замена, у дубля - второе место, у слоя - запрещённый импорт. */
function findingText(run: GateRun, item: Finding): string {
  const evidence = (item.evidence ?? {}) as Record<string, unknown>;
  if (run.gate === 'mutation' && evidence.original !== undefined) return `${evidence.original} → ${evidence.replacement} (${item.kind})`;
  if (run.gate === 'dry' && item.other?.path) return `похоже на ${item.other.path}:${item.other.line_start ?? ''} · ${item.value ?? ''}`;
  if (run.gate === 'architecture' && item.target) return `импорт ${item.target}`;
  if (run.gate === 'crap') return `${item.symbol ?? ''} · CRAP ${item.value ?? '—'}`;
  return String(evidence.line ?? item.kind ?? '');
}

function FindingRow({ run, item }: { run: GateRun; item: Finding }) {
  // у мутаций решать есть что только по выжившим; убитые и непокрытые - не предмет решения человека
  const decidable = DECIDABLE.has(run.gate) && Boolean(item.key) && (run.gate !== 'mutation' || item.kind === 'survived');
  const gate = run.gate as DecidableGate;
  return (
    <li className="quality-finding" data-severity={item.severity || undefined}>
      {item.path
        ? <button type="button" className="quality-where" title={where(item)} onClick={() => void openFile(item.path as string)}>{where(item)}</button>
        : <span className="quality-where">{where(item)}</span>}
      <span className="quality-what">{findingText(run, item)}</span>
      {item.advice && <small>{item.advice}</small>}
      {decidable && (
        item.decision && item.decision !== 'review'
          ? <span className="quality-decision">{DECISION_LABELS[item.decision]}</span>
          : (
            <span className="quality-decide">
              <button type="button" title="Находка допустима (эквивалентный мутант, осознанный дубль) - больше не блокирует" onClick={guarded(() => decide(gate, item.key as string, 'accepted'))}>
                <Check aria-hidden="true" />Принять
              </button>
              <button type="button" title="Отметить к исправлению" onClick={guarded(() => decide(gate, item.key as string, 'refactor'))}>
                <Wrench aria-hidden="true" />Исправить
              </button>
            </span>
          )
      )}
    </li>
  );
}

function GateRow({ run }: { run: GateRun }) {
  const [open, setOpen] = useState(run.status === 'failed');
  const notes = run.summary?.notes ?? [];
  const findings = run.findings ?? [];
  const detail = gateDetail(run) || run.error || '';
  const expandable = findings.length > 0 || notes.length > 0;
  return (
    <li className="quality-gate" data-status={run.status}>
      <button type="button" className="quality-gate-head" aria-expanded={expandable ? open : undefined} disabled={!expandable} onClick={() => setOpen(!open)}>
        <strong>{GATE_LABELS[run.gate]}</strong>
        <span className="quality-status">{STATUS_LABELS[run.status]}</span>
        <small>{detail}</small>
      </button>
      {open && expandable && (
        <div className="quality-gate-body">
          {notes.length > 0 && <p className="muted">{notes.join(' · ')}</p>}
          {findings.length > 0 && (
            <ul className="quality-findings">
              {findings.slice(0, FINDINGS_SHOWN).map((item, index) => <FindingRow key={`${item.key ?? ""}:${index}`} run={run} item={item} />)}
            </ul>
          )}
          {findings.length > FINDINGS_SHOWN && <p className="muted">и ещё {findings.length - FINDINGS_SHOWN} - полный отчёт у агента (quality.run report)</p>}
        </div>
      )}
    </li>
  );
}

function Workflow() {
  const workflow = useQuality((s) => s.workflow);
  if (!workflow.stage && !workflow.red && !workflow.green?.length && !workflow.units?.length) return null;
  return (
    <section className="quality-section">
      <div className="quality-flow">
        {workflow.stage && <span className="quality-chip" data-tone="accent">{STAGE_LABELS[workflow.stage]}</span>}
        {workflow.red && (
          <span className="quality-chip" data-tone={workflow.red.verified ? 'danger' : 'muted'} title={workflow.red.why ?? ''}>
            {workflow.red.verified ? 'red подтверждён' : 'red не подтверждён'}
          </span>
        )}
        {workflow.green?.length ? <span className="quality-chip" data-tone="positive">green: {workflow.green.length}</span> : null}
      </div>
      {workflow.premise_required && <p className="quality-alert">Проверка {workflow.premise_required} упала на двух версиях кода - агент должен пересмотреть исходное допущение.</p>}
      {workflow.root_cause && <p className="muted">Причина: {workflow.root_cause}</p>}
      {workflow.units?.length ? (
        <ul className="quality-units">
          {workflow.units.map((unit, index) => (
            <li key={index} data-verified={unit.verified || undefined}><span>{unit.title}</span><small>{unit.verified ? 'проверено' : unit.check}</small></li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function Proposals() {
  const proposals = useQuality((s) => s.proposals);
  const label = useQuality((s) => s.proposalLabel);
  if (!proposals.length) return null;
  return (
    <section className="quality-section">
      <h3>Разметка слоёв от агента</h3>
      {proposals.map((item) => (
        <div key={item.id} className="quality-proposal">
          <span className="quality-chip" data-tone="warn">{item.label || label}</span>
          <strong>{item.name}</strong>
          {item.note && <p className="muted">{item.note}</p>}
          <p className="quality-layers">{item.policy.layers.map((layer) => layer.name).join(' → ')}</p>
          <span className="quality-decide">
            <button type="button" title="Сделать политикой слоёв проекта" onClick={guarded(() => applyProposal(item.id))}><Check aria-hidden="true" />Применить</button>
            <button type="button" title="Удалить предложение" onClick={guarded(() => deleteProposal(item.id))}><Trash2 aria-hidden="true" />Удалить</button>
          </span>
        </div>
      ))}
    </section>
  );
}

export function QualityView({ active }: { active: boolean }) {
  const load = useQuality((s) => s.load);
  const error = useQuality((s) => s.error);
  const runs = useQuality((s) => s.runs);
  const decisions = useQuality((s) => s.decisions);
  const running = useQuality((s) => s.running);
  const heatOn = useQuality((s) => s.heatOn);
  // перечитываем при смене проекта или чата, а не при записи их id в хранилище самой загрузкой
  const projectId = useView((s) => s.project?.id);
  const chatId = useView((s) => s.chat?.id);
  const latest = latestByGate(runs);

  useEffect(() => {
    if (active) void loadQuality();
  }, [active, projectId, chatId]);

  return (
    <div className="quality-card">
      <div className="quality-actions">
        <button type="button" className="secondary" disabled={running} title="Сигналы, CRAP, дубли и слои по незакоммиченным изменениям" onClick={guarded(() => runQuality(['signals', 'crap', 'dry', 'architecture']))}>
          {running ? <RefreshCw className="spin" aria-hidden="true" /> : <Play aria-hidden="true" />}<span>Проверить</span>
        </button>
        <button type="button" className="secondary" disabled={running} title="Мутационное тестирование изменённых строк в изолированной копии проекта: запускает тесты проекта" onClick={guarded(() => runQuality(['mutation']))}>
          <Bug aria-hidden="true" /><span>Мутации</span>
        </button>
        <button type="button" className="secondary" aria-pressed={heatOn} title="Подсветить на карте проекта файлы с долгом качества" onClick={guarded(toggleHeat)}>
          <Flame aria-hidden="true" /><span>На карте</span>
        </button>
      </div>
      {load === 'failed' && <p className="quality-alert">{error || 'Не удалось загрузить данные качества'}</p>}
      {load === 'loading' && !runs.length && <p className="muted">Загрузка…</p>}
      <Workflow />
      {latest.length ? (
        <ul className="quality-gates">{latest.map((run) => <GateRow key={run.id} run={run} />)}</ul>
      ) : load === 'ready' ? (
        <p className="muted">Гейты ещё не запускались. Докки прогоняет их сам после правок кода, или нажми «Проверить».</p>
      ) : null}
      <Proposals />
      {decisions.length > 0 && (
        <section className="quality-section">
          <h3>Журнал решений</h3>
          <ul className="quality-log">
            {decisions.slice(0, 12).map((item) => (
              <li key={item.id}><span className="quality-chip" data-tone="muted">{DECISION_KINDS[item.kind] ?? item.kind}</span><span>{item.summary}</span></li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

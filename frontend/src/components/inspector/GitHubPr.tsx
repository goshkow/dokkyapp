import { RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { openExternal } from '../../lib/external';
import { useGit, type GitChecks } from '../../state/git';
import { guarded } from '../../state/overlay';
import { refreshGithub } from '../../state/remote';

const STATE: Record<string, string> = { OPEN: 'Открыт', MERGED: 'Влит', CLOSED: 'Закрыт' };

/** Сводка проверок одной строкой: «прошли 3 · упали 1 · идут 2». */
export function checksText(checks: GitChecks): string {
  const parts = [
    checks.passed ? `прошли ${checks.passed}` : '',
    checks.failed ? `упали ${checks.failed}` : '',
    checks.pending ? `идут ${checks.pending}` : '',
  ].filter(Boolean);
  return parts.join(' · ');
}

/** PR текущей ветки и CI рядом с коммитом. Пусто, если gh нет, он не вошёл или репозиторий не на GitHub. */
export function GitHubPr() {
  const github = useGit((s) => s.github);
  const showable = useGit((s) => Boolean(s.repo && s.remote && s.branch));
  const [busy, setBusy] = useState(false);
  if (!showable || !github?.available) return null;
  const { pr } = github;
  const refresh = guarded(async () => {
    setBusy(true);
    try { await refreshGithub(); } finally { setBusy(false); }
  });
  const state = pr ? (pr.draft && pr.state === 'OPEN' ? 'draft' : pr.state.toLowerCase()) : 'none';
  return (
    <div className="git-pr" data-state={state}>
      <div className="git-pr-head">
        {pr ? (
          <>
            <button type="button" className="git-pr-link" title={`${pr.title}: открыть на GitHub`} onClick={() => { openExternal(pr.url); }}>
              <b>#{pr.number}</b> <span>{pr.title}</span>
            </button>
            <span className="git-pr-state" data-state={state}>{state === 'draft' ? 'Черновик' : STATE[pr.state] ?? pr.state}</span>
          </>
        ) : (
          <span className="git-pr-none">Pull request для ветки не создан</span>
        )}
        <button type="button" className="git-pr-refresh" aria-label="Обновить" title="Обновить" disabled={busy} onClick={refresh}>
          <RefreshCw size={13} aria-hidden="true" className={busy ? 'spin' : undefined} />
        </button>
      </div>
      {pr && pr.checks.total > 0 && (
        <div className="git-pr-checks" data-state={pr.checks.state} title={pr.checks.failed_names.length ? `Упали: ${pr.checks.failed_names.join(', ')}` : undefined}>
          <i aria-hidden="true" />
          <span>Проверки: {checksText(pr.checks)}</span>
        </div>
      )}
    </div>
  );
}

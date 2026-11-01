import { ChevronRight } from 'lucide-react';
import { useEffect, useState } from 'react';
import { canPush, useGit, type GitFile } from '../../state/git';
import { useChat } from '../../state/chatCore';
import { guarded } from '../../state/overlay';
import { commitFiles, pushBranch, refreshGit } from '../../state/remote';
import { FILE_SAVED } from '../../state/workspace';

const MARK: Record<GitFile['status'], string> = { new: 'новый', modified: 'изменён', deleted: 'удалён' };

/** Коммит из «Изменений»: выбранные файлы, сообщение предлагает агент; отправка - отдельной кнопкой с подтверждением. */
export function GitCommit() {
  const state = useGit((s) => s);
  const running = useChat((s) => s.running);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<string | null>(null);
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const message = draft ?? state.suggestion;
  // другой проект или ветка: выбор и черновик от прошлого не тянем
  useEffect(() => { setDraft(null); setPicked({}); }, [state.projectId, state.branch]);
  useEffect(() => {
    const update = () => { void Promise.resolve(refreshGit()).catch(() => undefined); };
    window.addEventListener(FILE_SAVED, update);
    return () => window.removeEventListener(FILE_SAVED, update);
  }, []);
  if (!state.repo) return null;
  const selected = state.files.filter((file) => picked[file.path] ?? file.suggested).map((file) => file.path);
  const push = canPush(state);
  if (!state.files.length && !push) return null;
  const act = (work: () => Promise<unknown>) => guarded(async () => {
    setBusy(true);
    try { await work(); } finally { setBusy(false); }
  });
  return (
    <div className="git-commit">
      {state.files.length > 0 && (
        <>
          <button type="button" className="git-commit-files" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
            <ChevronRight className="change-chevron" size={13} aria-hidden="true" />
            <span>Не закоммичено: {state.files.length}{state.truncated ? '+' : ''}</span>
            <small>выбрано {selected.length}</small>
          </button>
          {open && (
            <div className="git-commit-list">
              {state.files.map((file) => (
                <label key={file.path} title={file.path}>
                  <input type="checkbox" checked={picked[file.path] ?? file.suggested} onChange={(event) => setPicked((value) => ({ ...value, [file.path]: event.target.checked }))} />
                  <span>{file.path}</span>
                  <small>{MARK[file.status]}</small>
                </label>
              ))}
            </div>
          )}
          <textarea
            className="git-commit-message"
            rows={2}
            value={message}
            placeholder="Сообщение коммита"
            aria-label="Сообщение коммита"
            onChange={(event) => setDraft(event.target.value)}
          />
        </>
      )}
      <div className="git-commit-actions">
        {state.files.length > 0 && (
          <button
            type="button"
            className="secondary"
            disabled={busy || running || !message.trim() || !selected.length}
            title={running ? 'Агент работает: коммит подхватил бы половину его правок' : `Закоммитить в ${state.branch ?? state.head}`}
            onClick={act(async () => {
              await commitFiles(message, selected);
              setDraft(null);
              setPicked({});
            })}
          >
            Закоммитить
          </button>
        )}
        {push && (
          <button
            type="button"
            className="secondary"
            disabled={busy || (state.behind ?? 0) > 0}
            title={(state.behind ?? 0) > 0 ? 'На сервере есть новые коммиты: сначала заберите их' : `Отправить ${state.branch} в ${state.remote}`}
            onClick={act(pushBranch)}
          >
            Отправить{state.ahead ? ` ↑${state.ahead}` : ''}
          </button>
        )}
      </div>
    </div>
  );
}

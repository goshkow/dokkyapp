import { Check, GitBranch } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { guarded } from '../../state/overlay';
import { switchBranch, useGit } from '../../state/git';
import { useChat } from '../../state/chatCore';
import { Popover } from '../ui/Popover';

// те же границы, что у сервера: остальное git отвергнет и так, а здесь просто не предлагаем
const VALID_NAME = /^(?![-./])(?!.*(\.\.|\/\/|@\{|\.lock$|[/.]$))[^\s~^:?*[\\]+$/;

/** Ветка проекта в шапке: показывает текущую, переключает и создаёт новую. */
export function BranchPicker() {
  const repo = useGit((s) => s.repo);
  const branch = useGit((s) => s.branch);
  const head = useGit((s) => s.head);
  const branches = useGit((s) => s.branches);
  const running = useChat((s) => s.running);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const button = useRef<HTMLButtonElement>(null);
  // Popover появляется не сразу и при открытии сам ставит фокус на первый пункт: поиск забирает его кадром позже
  const focusSearch = useCallback((node: HTMLInputElement | null) => {
    if (node) requestAnimationFrame(() => node.focus());
  }, []);
  const shown = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return needle ? branches.filter((name) => name.toLocaleLowerCase().includes(needle)) : branches;
  }, [branches, query]);
  const wanted = query.trim();
  const creatable = VALID_NAME.test(wanted) && !branches.includes(wanted);
  useEffect(() => {
    if (open) setQuery('');
  }, [open]);
  if (!repo) return null;
  const pick = (name: string, create = false) => {
    setOpen(false);
    if (name !== branch || create) guarded(switchBranch)(name, create);
  };
  return (
    <>
      <button
        ref={button}
        id="branch-button"
        type="button"
        className="branch-button"
        title={running ? 'Ветка проекта. Пока агент работает, переключать нельзя' : 'Ветка проекта'}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={running}
        onClick={() => setOpen((value) => !value)}
      >
        <GitBranch size={13} aria-hidden="true" />
        <span>{branch ?? head}</span>
      </button>
      <Popover open={open} anchor={button} onClose={() => setOpen(false)} className="branch-menu" align="end" label="Ветки" minWidth={240}>
        <input
          ref={focusSearch}
          className="branch-search"
          type="text"
          value={query}
          placeholder="Найти или создать ветку"
          aria-label="Найти или создать ветку"
          spellCheck={false}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              if (shown[0]) pick(shown[0]);
              else if (creatable) pick(wanted, true);
            } else if (!['ArrowDown', 'ArrowUp', 'Tab', 'Escape'].includes(event.key)) {
              // обычные буквы идут в поле, а не в поиск пункта по первой букве меню
              event.stopPropagation();
            }
          }}
        />
        <div className="branch-list">
          {shown.map((name) => (
            <button key={name} type="button" role="menuitem" className="menu-item" title={name} onClick={() => pick(name)}>
              <span>{name}</span>
              {name === branch && <Check size={14} aria-label="текущая" />}
            </button>
          ))}
          {creatable && (
            <button type="button" role="menuitem" className="menu-item" onClick={() => pick(wanted, true)}>
              <span>Создать ветку {wanted}</span>
            </button>
          )}
          {!shown.length && !creatable && <div className="branch-empty">Ничего не нашлось</div>}
        </div>
      </Popover>
    </>
  );
}

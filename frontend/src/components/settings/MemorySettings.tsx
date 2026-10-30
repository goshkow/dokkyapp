import { Pencil, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api, del, patch, post, put } from '../../lib/api';
import { confirmDialog } from '../../state/overlay';
import { Row, Section, Toggle, type Say } from './parts';

type Scope = 'user' | 'project';
interface Item { id: number; content: string; source: 'auto' | 'manual'; created_at: string }
interface Memory { enabled: boolean; user: Item[]; project: Item[] }

const MAX = 300;

/** Память о пользователе и проекте: что агент запомнил сам и что добавили вручную (agents/profile_memory.py). */
export function MemorySettings({ project, say }: { project: { id: number; name: string } | null; say: Say }) {
  const [memory, setMemory] = useState<Memory | null>(null);
  const [error, setError] = useState('');
  const query = project ? `?project_id=${project.id}` : '';
  useEffect(() => {
    let alive = true;
    setError('');
    api<Memory>(`/api/memory${query}`).then((value) => { if (alive) setMemory(value); }).catch((reason: Error) => { if (alive) setError(reason.message); });
    return () => { alive = false; };
  }, [query]);
  const run = async (action: () => Promise<Memory>, done = 'Сохранено') => {
    try {
      setMemory(await action());
      say(done);
    } catch (reason) {
      say((reason as Error).message);
      throw reason;
    }
  };
  if (error) return <Section title="Память"><Row title="Память" detail={error} /></Section>;
  if (!memory) return <Section title="Память"><div className="settings-loading skeleton" aria-label="Загрузка памяти" /></Section>;
  const scopes: Array<[Scope, string, string]> = [
    ['user', 'О вас', 'Действует во всех проектах: ваш стек, стиль кода, как вам удобнее получать ответы.'],
    ...(project ? [['project', `О проекте «${project.name}»`, 'Только для чатов этого проекта: стек, архитектура, договорённости.'] as [Scope, string, string]] : []),
  ];
  return (
    <Section
      title="Память"
      hint="Докки сам запоминает устойчивые сведения о вас и проекте из ваших запросов (когда вы просите запомнить, рассказываете о себе, и раз в несколько сообщений), чтобы не объяснять одно и то же заново. Хранится только на этом компьютере. Распознавание - отдельный небольшой запрос к модели."
    >
      <Toggle
        title="Запоминать сведения о вас и проектах"
        detail="Выключите - агент перестанет запоминать новое и не будет видеть сохранённое. Записи останутся, пока вы их не удалите."
        checked={memory.enabled}
        save={(enabled) => run(() => put<Memory>(`/api/memory/settings${query}`, { enabled }))}
        say={say}
      />
      {scopes.map(([scope, title, detail]) => (
        <MemoryScope
          key={scope}
          title={title}
          detail={detail}
          items={memory[scope]}
          add={(content) => run(() => post<Memory>('/api/memory', { scope, project_id: project?.id ?? null, content }), 'Добавлено')}
          edit={(item, content) => run(() => patch<Memory>(`/api/memory/${item.id}${query}`, { content }))}
          remove={(item) => run(() => del<Memory>(`/api/memory/${item.id}${query}`), 'Удалено')}
          clear={async () => {
            if (!(await confirmDialog(`Очистить память «${title}»?`, 'Все пункты этого раздела удалятся без возможности восстановления.', 'Очистить'))) return;
            await run(() => post<Memory>('/api/memory/clear', { scope, project_id: project?.id ?? null }), 'Очищено');
          }}
        />
      ))}
    </Section>
  );
}

function MemoryScope({ title, detail, items, add, edit, remove, clear }: {
  title: string; detail: string; items: Item[];
  add: (content: string) => Promise<void>; edit: (item: Item, content: string) => Promise<void>;
  remove: (item: Item) => Promise<void>; clear: () => Promise<void>;
}) {
  const [draft, setDraft] = useState('');
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const act = (action: () => Promise<void>) => {
    setBusy(true);
    action().catch(() => undefined).finally(() => setBusy(false));
  };
  const submit = () => {
    const text = draft.trim();
    if (text) act(async () => { await add(text); setDraft(''); });
  };
  const save = (item: Item) => {
    const text = editing?.text.trim() ?? '';
    if (!text || text === item.content) { setEditing(null); return; }
    act(async () => { await edit(item, text); setEditing(null); });
  };
  return (
    <Row title={title} detail={detail} stack>
      {items.length === 0 ? (
        <p className="memory-empty">Пока пусто. Скажите в чате «запомни, что…» или добавьте пункт сами.</p>
      ) : (
        <ul className="memory-list" aria-label={title}>
          {items.map((item) => (
            <li key={item.id}>
              {editing?.id === item.id ? (
                <input
                  className="pref-input"
                  value={editing.text}
                  maxLength={MAX}
                  aria-label="Изменить пункт памяти"
                  autoFocus
                  disabled={busy}
                  onChange={(event) => setEditing({ id: item.id, text: event.target.value })}
                  onBlur={() => save(item)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') { event.preventDefault(); save(item); }
                    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setEditing(null); }
                  }}
                />
              ) : (
                <span className="memory-text">{item.content}{item.source === 'manual' && <em className="memory-tag">вручную</em>}</span>
              )}
              <span className="memory-actions">
                <button type="button" className="icon-button" title="Изменить" aria-label={`Изменить: ${item.content}`} disabled={busy} onClick={() => setEditing({ id: item.id, text: item.content })}><Pencil aria-hidden="true" /></button>
                <button type="button" className="icon-button" title="Удалить" aria-label={`Удалить: ${item.content}`} disabled={busy} onClick={() => act(() => remove(item))}><Trash2 aria-hidden="true" /></button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="memory-add">
        <input
          className="pref-input"
          value={draft}
          maxLength={MAX}
          placeholder="Например: пишу на TypeScript, комментарии по-русски"
          aria-label={`Добавить в память: ${title}`}
          disabled={busy}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); submit(); } }}
        />
        <button type="button" className="pref-button" disabled={busy || !draft.trim()} onClick={submit}>Добавить</button>
        {items.length > 0 && <button type="button" className="pref-button danger" disabled={busy} onClick={() => act(clear)}>Очистить</button>}
      </div>
    </Row>
  );
}

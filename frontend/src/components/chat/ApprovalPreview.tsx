import { useMemo } from 'react';
import { parseUnifiedDiff } from '../../lib/diff';
import { DiffView } from './DiffView';

const MAX_CHARS = 20000;
const MAX_LINES = 200;

/** интерфейс показывает понятные команды и код, а не сериализованный объект инструмента */
export function previewAction(name?: string, raw?: unknown): { title: string; text: string; truncated: boolean; patch: boolean } | null {
  const args = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw as Record<string, unknown> : {};
  const tool = (name ?? '').replaceAll('__', '.');
  const command = typeof args.command === 'string' ? args.command : null;
  const patch = tool.startsWith('patch.') && (typeof args.patch === 'string' ? args.patch : typeof args.content === 'string' ? args.content : null);
  const content = typeof args.content === 'string' ? args.content : typeof args.new_text === 'string' ? args.new_text : null;
  const source = command ?? patch ?? content;
  if (typeof source !== 'string') return null;
  const lines = source.split(/\r?\n/);
  const bounded = lines.slice(0, MAX_LINES).join('\n').slice(0, MAX_CHARS);
  const safe = bounded
    .replace(/((?:[\w]*_)?(?:TOKEN|PASSWORD|SECRET|API_KEY|ACCESS_KEY)\s*=\s*)(?:"[^"]*"|'[^']*'|[^\s;]+)/gi, '$1[скрыто]')
    .replace(/(Bearer\s+)[a-z\d_.~+/-]{12,}/gi, '$1[скрыто]')
    .replace(/([?&](?:token|key|secret|password)=)[^&#\s"']+/gi, '$1[скрыто]');
  return { title: command ? 'Команда' : patch ? 'Предлагаемые изменения' : 'Предлагаемый фрагмент файла', text: safe, truncated: source.length > MAX_CHARS || lines.length > MAX_LINES, patch: Boolean(patch) };
}

export function ApprovalPreview({ name, args, id }: { name?: string; args?: unknown; id: string }) {
  const preview = useMemo(() => previewAction(name, args), [name, args]);
  const files = useMemo(() => preview?.patch && !preview.truncated ? parseUnifiedDiff(preview.text) : [], [preview]);
  if (!preview) return null;
  return <section className="approval-preview" aria-label={preview.title}>
    <span className="approval-preview-label">{preview.title}</span>
    {files.length ? <DiffView files={files} id={`proposed-${id}`} inline /> : <pre><code>{preview.text}</code></pre>}
    {preview.truncated && <small>Показано не больше {MAX_LINES} строк и {MAX_CHARS.toLocaleString('ru-RU')} символов.</small>}
  </section>;
}

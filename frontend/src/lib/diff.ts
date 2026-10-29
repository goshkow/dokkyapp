/* Единый вид диффа для наборов изменений проекта. */

export interface DiffRow {
  kind: 'added' | 'removed' | 'context' | 'marker';
  text: string;
  old?: number | null;
  fresh?: number | null;
}
export interface DiffHunk {
  header: string;
  rows: DiffRow[];
}
export interface DiffFile {
  path: string;
  oldPath?: string | null;
  newPath?: string | null;
  hunks: DiffHunk[];
  added: number;
  removed: number;
  status?: 'created' | 'deleted' | 'modified';
}

export interface ChangeMeta {
  path: string;
  before_hash: string | null;
  after_hash: string | null;
}
export interface Change {
  id: number | string;
  description: string;
  status?: string;
  turn_id?: number | null;
  diff: string;
  files?: ChangeMeta[];
}

const clean = (path: string): string | null => (path === '/dev/null' ? null : (path.replace(/^[ab]\//, '').split('\t')[0] ?? null));

export function parseUnifiedDiff(source: string, metadata: ChangeMeta[] = []): DiffFile[] {
  const lines = String(source ?? '').split(/\r?\n/);
  const files: DiffFile[] = [];
  let file: DiffFile | null = null;
  let hunk: DiffHunk | null = null;
  let old = 0, fresh = 0, leftOld = 0, leftNew = 0;
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index] ?? '';
    if ((!hunk || (!leftOld && !leftNew)) && line.startsWith('--- ') && lines[index + 1]?.startsWith('+++ ')) {
      const oldPath = clean(line.slice(4));
      const newPath = clean((lines[++index] ?? '').slice(4));
      file = { path: newPath || oldPath || 'Файл', oldPath, newPath, hunks: [], added: 0, removed: 0 };
      files.push(file);
      hunk = null;
      continue;
    }
    const match = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$/.exec(line);
    if (match) {
      if (!file) {
        file = { path: metadata[0]?.path || 'Файл', hunks: [], added: 0, removed: 0 };
        files.push(file);
      }
      old = Number(match[1]);
      fresh = Number(match[3]);
      leftOld = Number(match[2] ?? 1);
      leftNew = Number(match[4] ?? 1);
      hunk = { header: line, rows: [] };
      file.hunks.push(hunk);
      continue;
    }
    if (!hunk || !file) continue;
    if (line.startsWith('\\ ')) {
      hunk.rows.push({ kind: 'marker', text: 'Нет перевода строки в конце файла' });
      continue;
    }
    const prefix = line[0];
    if (!prefix || ![' ', '+', '-'].includes(prefix) || (!leftOld && !leftNew)) continue;
    const row: DiffRow = { kind: prefix === '+' ? 'added' : prefix === '-' ? 'removed' : 'context', text: line.slice(1), old: null, fresh: null };
    if (prefix !== '+') {
      row.old = old++;
      leftOld = Math.max(0, leftOld - 1);
    }
    if (prefix !== '-') {
      row.fresh = fresh++;
      leftNew = Math.max(0, leftNew - 1);
    }
    if (prefix === '+') file.added++;
    if (prefix === '-') file.removed++;
    hunk.rows.push(row);
  }
  for (const item of metadata) {
    let found = files.find((entry) => entry.path === item.path);
    if (!found) {
      found = { path: item.path, hunks: [], added: 0, removed: 0 };
      files.push(found);
    }
    found.status = item.before_hash === null ? 'created' : item.after_hash === null ? 'deleted' : 'modified';
  }
  return files;
}

export const diffStats = (files: DiffFile[]) => files.reduce((sum, file) => ({ add: sum.add + file.added, remove: sum.remove + file.removed }), { add: 0, remove: 0 });

const cache = new Map<Change['id'], { source: string; meta: string; files: DiffFile[] }>();

export function changeDiffModel(change: Change): DiffFile[] {
  const meta = JSON.stringify(change.files ?? []);
  const cached = cache.get(change.id);
  if (cached && cached.source === change.diff && cached.meta === meta) return cached.files;
  const files = parseUnifiedDiff(change.diff, change.files ?? []);
  cache.set(change.id, { source: change.diff, meta, files });
  return files;
}

export function pruneDiffCache(alive: Set<Change['id']>): void {
  for (const id of cache.keys()) if (!alive.has(id)) cache.delete(id);
}

export type FlatRow =
  | { kind: 'file'; file: DiffFile }
  | { kind: 'hunk'; text: string }
  | { kind: 'marker'; text: string }
  | (DiffRow & { path: string });

export function diffRows(files: DiffFile[]): FlatRow[] {
  const rows: FlatRow[] = [];
  for (const file of files) {
    rows.push({ kind: 'file', file });
    for (const hunk of file.hunks) {
      rows.push({ kind: 'hunk', text: hunk.header });
      for (const row of hunk.rows) rows.push({ ...row, path: file.path });
    }
    if (!file.hunks.length) {
      rows.push({ kind: 'marker', text: file.status === 'created' ? 'Создан пустой файл' : file.status === 'deleted' ? 'Удалён пустой файл' : 'Текстовых изменений нет' });
    }
  }
  return rows;
}

export const languageFor = (path: string): string | undefined =>
  ({ py: 'python', js: 'javascript', ts: 'typescript', tsx: 'typescript', json: 'json', html: 'xml', css: 'css', sh: 'bash', sql: 'sql' } as Record<string, string>)[String(path).split('.').pop()?.toLowerCase() ?? ''];

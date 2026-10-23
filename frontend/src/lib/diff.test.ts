import { describe, expect, it } from 'vitest';
import { changeDiffModel, diffRows, diffStats, languageFor, parseUnifiedDiff } from './diff';

const diff = '--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,4 @@\n import os\n-print(1)\n+print(2)\n+print(3)\n x = 1\n';

describe('diff', () => {
  it('разбирает унифицированный дифф со строками и нумерацией', () => {
    const [file] = parseUnifiedDiff(diff);
    expect(file).toMatchObject({ path: 'app.py', added: 2, removed: 1 });
    const rows = file?.hunks[0]?.rows ?? [];
    expect(rows.map((row) => row.kind)).toEqual(['context', 'removed', 'added', 'added', 'context']);
    expect(rows[1]).toMatchObject({ old: 2, fresh: null });
    expect(rows[4]).toMatchObject({ old: 3, fresh: 4 });
  });

  it('помечает создание и удаление файлов по метаданным', () => {
    const files = parseUnifiedDiff('', [{ path: 'new.txt', before_hash: null, after_hash: 'x' }, { path: 'old.txt', before_hash: 'y', after_hash: null }]);
    expect(files.map((file) => file.status)).toEqual(['created', 'deleted']);
  });

  it('считает итоги и плоские строки', () => {
    const files = parseUnifiedDiff(diff);
    expect(diffStats(files)).toEqual({ add: 2, remove: 1 });
    expect(diffRows(files)[0]?.kind).toBe('file');
    expect(diffRows([{ path: 'e.txt', hunks: [], added: 0, removed: 0, status: 'created' }]).at(-1)).toMatchObject({ text: 'Создан пустой файл' });
  });

  it('кэширует разбор по номеру набора и перестраивает при смене текста', () => {
    const change = { id: 1, description: 'x', diff, files: [] };
    const first = changeDiffModel(change);
    expect(changeDiffModel(change)).toBe(first);
    expect(changeDiffModel({ ...change, diff: diff + '\n' })).not.toBe(first);
  });

  it('определяет язык по расширению', () => {
    expect(languageFor('a/b.TSX')).toBe('typescript');
    expect(languageFor('README')).toBeUndefined();
  });
});

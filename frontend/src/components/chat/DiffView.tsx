import hljs from 'highlight.js/lib/common';
import { FileCode2 } from 'lucide-react';
import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { diffRows, languageFor, type DiffFile, type FlatRow } from '../../lib/diff';

const STEP = 800;

const highlighted = (text: string, path: string): string => {
  const language = languageFor(path);
  if (language && hljs.getLanguage(language) && text.length <= 4000) return hljs.highlight(text, { language, ignoreIllegals: true }).value;
  return '';
};

const Row = memo(function Row({ row }: { row: FlatRow }) {
  if (row.kind === 'file') {
    return (
      <div className="diff-file-heading">
        <FileCode2 size={15} aria-hidden="true" />
        <span>{row.file.path}</span>
        <small className="positive">+{row.file.added}</small>
        <small className="danger">−{row.file.removed}</small>
      </div>
    );
  }
  if (row.kind === 'hunk' || row.kind === 'marker') {
    return <div className={`diff-line ${row.kind}`}><span /><span /><code>{row.text}</code></div>;
  }
  const html = highlighted(row.text, row.path);
  return (
    <div className={`diff-line ${row.kind}`}>
      <span>{row.old ?? ''}</span>
      <span>{row.fresh ?? ''}</span>
      {html ? <code dangerouslySetInnerHTML={{ __html: html }} /> : <code>{row.text || ' '}</code>}
    </div>
  );
});

/** Дифф одного файла или набора: строки подгружаются порциями, положение прокрутки сохраняется. */
export function DiffView({ files, id, bare = false, inline = false }: { files: DiffFile[]; id: string; bare?: boolean; inline?: boolean }) {
  // bare: заголовок файла рисует сам список (карточка «Изменения»)
  const rows = useMemo(() => (bare ? diffRows(files).filter((row) => row.kind !== 'file') : diffRows(files)), [files, bare]);
  const [shown, setShown] = useState(STEP);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => setShown(STEP), [id]);
  return (
    <div className="code-diff" id={inline ? `diff-${id}` : 'diff'} ref={root}>
      {rows.slice(0, shown).map((row, index) => <Row key={index} row={row} />)}
      {shown < rows.length && (
        <button type="button" className="secondary diff-load-more" onClick={() => setShown((value) => value + STEP)}>
          Показать ещё строки ({rows.length - shown})
        </button>
      )}
    </div>
  );
}

import { FileArchive, FileText, Presentation, Sheet, Download, ExternalLink } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { cloudFileUrl, fileExtension, formatBytes, previewableText, type CloudFile } from '../../lib/cloud';
import { openArtifact } from '../../state/viewers';
import { Popover } from '../ui/Popover';

function icon(name: string): ReactNode {
  const ext = fileExtension(name).toLowerCase();
  if (['ppt', 'pptx', 'key'].includes(ext)) return <Presentation aria-hidden="true" />;
  if (['xls', 'xlsx', 'csv', 'ods'].includes(ext)) return <Sheet aria-hidden="true" />;
  if (['zip', '7z', 'rar', 'tar', 'gz'].includes(ext)) return <FileArchive aria-hidden="true" />;
  return <FileText aria-hidden="true" />;
}

/** Скачивание по ссылке файла: в окне приложения откроется выбор места сохранения. */
export function downloadFile(file: CloudFile): void {
  const url = cloudFileUrl(file);
  if (!url) return;
  const link = document.createElement('a');
  link.href = url;
  link.download = file.name || 'file';
  document.body.append(link);
  link.click();
  link.remove();
}

/** Файл ответа (презентация, документ, таблица...): щелчок открывает текст или скачивает файл, правая кнопка - меню. */
export function FileTile({ file }: { file: CloudFile }) {
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [open, setOpen] = useState(false);
  const name = file.name || 'Файл';
  const meta = [fileExtension(name), formatBytes(file.size)].filter(Boolean).join(' · ');
  const text = previewableText(file);
  return (
    <div className="message-file-group">
      <button
        type="button"
        className="message-file-tile"
        title={name}
        aria-label={`${text ? 'Открыть' : 'Скачать'} файл: ${name}`}
        disabled={file.local}
        onClick={() => (text ? openArtifact(file) : downloadFile(file))}
        onContextMenu={(event) => {
          if (file.local) return;
          // своё меню файла вместо общего меню окна
          event.preventDefault();
          setMenu({ x: event.clientX, y: event.clientY });
          setOpen(true);
        }}
      >
        <span className="tile-icon">{icon(name)}</span>
        <span className="tile-copy">
          <span className="tile-name">{name}</span>
          {meta && <span className="tile-meta">{meta}</span>}
        </span>
      </button>
      <Popover open={open && Boolean(menu)} anchor={menu} onClose={() => setOpen(false)} label={`Действия с файлом ${name}`} minWidth={180} className="context-menu">
        <button type="button" role="menuitem" className="menu-item" onClick={() => { setOpen(false); openArtifact(file); }}><ExternalLink aria-hidden="true" /><span>Открыть</span></button>
        <button type="button" role="menuitem" className="menu-item" onClick={() => { setOpen(false); downloadFile(file); }}><Download aria-hidden="true" /><span>Скачать</span></button>
      </Popover>
    </div>
  );
}

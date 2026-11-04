import { ChevronDown, ChevronRight, Folder } from 'lucide-react';
import { pressBarControl, type BarControl, type BarControlId } from '../../state/composerBar';

/** Переключатель над полем внутри нативной плитки. Клавиатура остаётся у настоящей кнопки на странице главного вида. */
export function TileControl({ id, control }: { id: BarControlId; control: BarControl }) {
  const project = id === 'pill';
  return (
    // те же обёртки, что у ряда над полем: размеры кнопки задают их правила, и плитка совпадает с рамкой на странице
    <div className={`composer-toolbar${project ? '' : ' agent-controls'}`}>
      <button
        type="button"
        tabIndex={-1}
        className={`tile-control ${project ? 'workspace-selector chat-control' : 'select-trigger'}`}
        aria-label={control.title}
        disabled={control.disabled}
        data-open={control.open || undefined}
        data-focus={control.focus || undefined}
        onClick={() => void pressBarControl(id)}
      >
        {project && <Folder size={15} aria-hidden="true" />}
        <span>{control.label}</span>
        {project ? <ChevronRight size={12} aria-hidden="true" /> : <ChevronDown aria-hidden="true" />}
      </button>
    </div>
  );
}

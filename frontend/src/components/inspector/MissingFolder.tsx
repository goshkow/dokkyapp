import { FolderX } from 'lucide-react';
import type { Project } from '../../lib/types';
import { useData } from '../../state/data';
import { guarded } from '../../state/overlay';
import { relocateProject, removeProject } from '../../state/remote';

/** Список проектов обновляется при возврате в окно: папку могли удалить, пока приложение работало. */
export const useFolderMissing = (project: Project | null | undefined): boolean =>
  useData((state) => project != null && state.projects.find((item) => item.id === project.id)?.exists === false);

export function MissingFolder({ project }: { project: Project }) {
  return (
    <div className="empty-list">
      <FolderX size={24} aria-hidden="true" />
      <h3>Папка не найдена</h3>
      <p>{project.path}</p>
      <button type="button" className="secondary" onClick={guarded(() => relocateProject(project))}><span>Указать заново</span></button>
      <button type="button" className="secondary" onClick={guarded(() => removeProject(project))}><span>Убрать из списка</span></button>
    </div>
  );
}

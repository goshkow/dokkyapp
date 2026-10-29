/* Связь разметки ответов с приложением: открытие файлов проекта, копирование кода, режим Докки (картинки, видео, файлы сайта). */
import { cloudFileUrl } from '../lib/cloud';
import { openInBrowserPanel } from './browser';
import { copyText } from '../lib/clipboard';
import { markdownHooks } from '../lib/markdown';
import { richHooks } from '../lib/rich';
import { streamHooks } from '../lib/streamMarkdown';
import { followFeed } from './chatCore';
import { guarded } from './overlay';
import { ui } from './ui';
import { view } from './view';
import { openImage } from './viewers';
import { openFile } from './workspaceRemote';

const development = (): boolean => ui.get().scope === 'development';

markdownHooks.richEnabled = () => !development();
markdownHooks.hasProject = () => development() && Boolean(view.get().project);
markdownHooks.openProjectFile = (path) => { void guarded(() => openFile(path))(); };
markdownHooks.copy = (text, event, success) => { void copyText(text, event, success); };
markdownHooks.openLink = (href) => {
  if (!development()) return false;
  void openInBrowserPanel(href, view.get().project?.id ?? null);
  return true;
};

richHooks.fileUrl = (file) => cloudFileUrl(file);
richHooks.openImage = (file, image) => openImage(file, cloudFileUrl(file) ?? image.currentSrc ?? image.src, image);

// после каждой порции текста лента следует за ответом, если читатель внизу
streamHooks.follow = () => followFeed();

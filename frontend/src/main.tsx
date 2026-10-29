import './styles/tokens.css';
import './styles/base.css';
import './styles/compat.css';
import './styles/legacy/chat.css';
import './styles/legacy/work-progress.css';
import './styles/legacy/local-journal.css';
import './styles/legacy/dokky-message.css';
import './styles/legacy/cloud-search.css';
import './styles/legacy/cloud-media.css';
import './styles/legacy/surface-controls.css';
import './styles/legacy/rich-content.css';
import './styles/legacy/site-chat.css';
import './styles/legacy/chat-shell.css';
import './styles/legacy/settings.css';
import './styles/attachments.css';
import './styles/composer.css';
import './styles/rail.css';
import './styles/scroll.css';
import './styles/shell.css';
import './styles/ui.css';
import './styles/sidebar.css';
import './styles/auth.css';
import './styles/header.css';
import './styles/journal.css';
import './styles/library.css';
import './styles/blocks.css';
import './styles/viewer.css';
import './styles/settings.css';
import './styles/billing.css';
import './styles/pages.css';
import './styles/typography.css';
import './styles/glass.css';
import './styles/surface.css';

import { role } from './lib/mirror';

// главный вид - всё приложение; вид плитки (sidebar) - только зеркало левого меню внутри нативного стекла
if (role === 'main') void import('./bootMain');
else void import('./bootTile');

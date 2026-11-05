export interface ApprovalSummary { title: string; scope: string[]; details: string[] }
const text = (value: unknown, limit = 160): string => {
  if (typeof value !== 'string') return '';
  const clean = value.replace(/[\u0000-\u001f]+/g, ' ').trim();
  return clean.length > limit ? `${clean.slice(0, limit - 1)}…` : clean;
};
const object = (value: unknown): Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const labels: Record<string, string> = {
  'file.create': 'Создать файл', 'file.write_patch': 'Изменить файл', 'file.edit': 'Изменить файл',
  'file.search_replace': 'Заменить текст в файле', 'file.delete': 'Удалить файл',
  'file.move': 'Переместить файл', 'file.copy': 'Скопировать файл', 'file.restore': 'Восстановить файлы',
  'patch.apply': 'Применить изменения к файлам', 'patch.preview': 'Проверить изменения файлов',
  'workspace.open': 'Открыть рабочую папку', 'workspace.create': 'Создать рабочую папку',
  'agent.continue': 'продолжить работу', 'workspace.snapshot': 'Сохранить резервную копию проекта', 'mcp.tools_list': 'Подключить внешние инструменты',
  'mcp.tool_call': 'Выполнить действие внешнего инструмента', 'process.kill': 'Остановить процесс',
  'git.commit': 'Создать коммит', 'git.add': 'Подготовить файлы к коммиту', 'git.checkout': 'Переключить ветку',
  'git.restore_file': 'Восстановить файл из Git', 'git.create_branch': 'Создать ветку',
  'git.push': 'Отправить ветку на сервер', 'git.pull': 'Забрать изменения с сервера', 'git.merge': 'Слить ветки',
  'git.stash': 'Отложить правки', 'github.pr_create': 'Создать pull request на GitHub',
  'devserver.start': 'Запустить сервер проекта', 'devserver.stop': 'Остановить сервер проекта',
  'test.run': 'Запустить тесты', 'build.run': 'Собрать проект', 'lint.run': 'Проверить код', 'format.run': 'Отформатировать код',
  'refactor.rename': 'Переименовать символ в проекте', 'refactor.extract': 'Изменить структуру кода',
  'qa.run': 'Запустить проверки проекта', 'quality.run': 'Проверить качество кода',
};
const computerActions: Record<string, string> = {
  guide: 'Прочитать правила управления приложениями', status: 'Проверить доступ к управлению приложениями',
  'list-apps': 'Посмотреть запущенные приложения', 'list-windows': 'Посмотреть окна приложений', 'list-displays': 'Посмотреть экраны',
  snapshot: 'Прочитать окно приложения', find: 'Найти элемент в приложении', get: 'Прочитать свойство элемента', is: 'Проверить состояние элемента',
  screenshot: 'Сделать снимок окна или экрана', 'list-surfaces': 'Посмотреть меню и панели приложения',
  click: 'Нажать элемент в приложении', 'double-click': 'Дважды нажать элемент в приложении', 'right-click': 'Открыть контекстное меню элемента',
  type: 'Ввести текст в поле приложения', 'set-value': 'Записать значение в поле приложения', clear: 'Очистить поле приложения',
  focus: 'Перевести фокус на элемент', select: 'Выбрать значение в списке приложения', toggle: 'Переключить элемент в приложении',
  check: 'Отметить элемент в приложении', uncheck: 'Снять отметку с элемента', expand: 'Раскрыть элемент в приложении', collapse: 'Свернуть элемент в приложении',
  scroll: 'Прокрутить окно приложения', 'scroll-to': 'Прокрутить до элемента', press: 'Нажать сочетание клавиш в приложении',
  hover: 'Навести указатель на элемент', drag: 'Перетащить элемент', 'mouse-click': 'Нажать мышью в точке экрана',
  'mouse-move': 'Переместить указатель', 'mouse-wheel': 'Прокрутить колесом мыши', launch: 'Открыть приложение', 'close-app': 'Закрыть приложение',
  'focus-window': 'Выбрать окно приложения', 'resize-window': 'Изменить размер окна', 'move-window': 'Переместить окно',
  minimize: 'Свернуть окно', maximize: 'Развернуть окно', restore: 'Восстановить окно', wait: 'Подождать в приложении',
  'clipboard-get': 'Прочитать буфер обмена', 'clipboard-set': 'Записать текст в буфер обмена', 'list-notifications': 'Прочитать уведомления',
  batch: 'Выполнить несколько действий в приложении',
};

/** описание строится по структуре вызова, содержимое файлов и произвольный JSON в подтверждение не попадают */
export function summarizeApproval(name: unknown, raw: unknown): ApprovalSummary {
  const tool = text(name, 100).replaceAll('__', '.');
  const args = object(raw);
  const scope: string[] = [];
  const details: string[] = [];
  const description = text(args.description);
  if (description) details.push(description);
  const add = (value: unknown) => { const label = text(value, 240); if (label && !scope.includes(label)) scope.push(label); };
  for (const key of ['path', 'source', 'destination', 'working_directory', 'cwd']) add(args[key]);
  if (Array.isArray(args.paths)) args.paths.slice(0, 100).forEach(add);
  const patch = typeof args.patch === 'string' ? args.patch : tool.startsWith('patch.') && typeof args.content === 'string' ? args.content : '';
  for (const match of patch.matchAll(/^(?:\*\*\* (?:Add|Update|Delete) File: |\+\+\+ (?:b\/)?)([^\r\n]+)/gm)) if (match[1] !== '/dev/null') add(match[1]);
  let title = labels[tool] ?? (tool.startsWith('browser.') ? 'Выполнить действие в браузере' : 'Выполнить действие инструмента');
  if (tool === 'computer.use') {
    title = computerActions[text(args.command)] ?? 'Управлять приложением';
    add(args.app);
    if (typeof args.window_id === 'string') add(`Окно ${text(args.window_id, 64)}`);
    if (typeof args.text === 'string') details.push(`Текст: ${args.text.length.toLocaleString('ru-RU')} символов`);
    if (typeof args.combo === 'string') details.push(`Клавиши: ${text(args.combo, 60)}`);
    if (Array.isArray(args.items)) details.push(`Действий: ${args.items.length.toLocaleString('ru-RU')}`);
    if (args.headed === true) details.push('Физический ввод: мышь и клавиатура двигаются на экране');
  }
  if (typeof args.url === 'string') {
    try { const url = new URL(args.url); add(`${url.protocol}//${url.host}${url.pathname}`); } catch { /* некорректный адрес не выводим */ }
  }
  if (tool === 'shell.run' || (tool !== 'computer.use' && typeof args.command === 'string')) {
    if (tool === 'shell.run') title = 'Выполнить команды в терминале';
    const command = typeof args.command === 'string' ? args.command : '';
    const lines = command.split(/\r?\n/).filter((line) => line.trim() && !line.trim().startsWith('#'));
    if (lines.length > 1) details.push(`Сценарий: ${lines.length.toLocaleString('ru-RU')} строк`);
    // показываем программу и подкоманду, но не данные, скрипты, токены и тела heredoc
    const first = lines[0]?.trim() ?? '';
    const head = /^(?:[\w]+=(?:"[^"]*"|'[^']*'|\S+)\s+)*([\w./-]+)(?:\s+([\w./:-]+))?/.exec(first);
    if (head) details.push(`Запуск: ${text(head[1], 80)}${head[2] && !head[2].startsWith('-') ? ` ${text(head[2], 60)}` : ''}`);
    else details.push('Сценарий командной оболочки');
  }
  if (typeof args.content === 'string' && !tool.startsWith('patch.')) details.push(`Содержимое файла: ${args.content.split('\n').length.toLocaleString('ru-RU')} строк`);
  if (Array.isArray(args.edits)) details.push(`Замен в файле: ${args.edits.length.toLocaleString('ru-RU')}`);
  else if (typeof args.new_text === 'string') details.push('Заменить выбранный фрагмент текста');
  if (args.replace_all === true) details.push('Заменить все совпадения');
  if (tool === 'quality.run') {
    const gates = Array.isArray(args.gates) ? args.gates.filter((item): item is string => typeof item === 'string').slice(0, 8) : [];
    if (gates.length) details.push(`Гейты: ${gates.map((item) => text(item, 20)).join(', ')}`);
    if (gates.includes('mutation')) details.push('Мутации запускают тесты проекта в изолированной копии; файлы проекта не меняются');
    if (args.coverage === 'collect') details.push('Сбор покрытия запускает тесты проекта');
  }
  if (typeof args.branch === 'string') add(`Ветка: ${text(args.branch)}`);
  if (typeof args.server === 'string') add(`Сервер: ${text(args.server)}`);
  if (!labels[tool] && tool !== 'shell.run' && tool !== 'computer.use') details.push(`Инструмент: ${tool || 'внешний'}`);
  if (scope.length === 0) scope.push(tool === 'computer.use' ? 'Локальное приложение' : tool === 'shell.run' ? 'Терминал на этом компьютере' : labels[tool] ? 'Текущий проект' : `Инструмент: ${tool || 'внешний'}`);
  return { title, scope: scope.slice(0, 100), details };
}

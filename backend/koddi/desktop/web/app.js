/* интерфейс локального проекта, ответы модели приходят через адаптер API Докки */
window.koddiDesktop = window.koddiDesktop === true || /\bKoddiDesktop\//.test(navigator.userAgent);
// без GPU сообщения выносятся в слои: прокрутка не перерисовывает текст на каждом кадре
if (/\bKoddiRender\/software\b/.test(navigator.userAgent))
  document.documentElement.dataset.render = "software";
const $ = (selector, root = document) => root.querySelector(selector);
// запись в DOM только при изменении: так не сбрасываются hover, анимации и выделение
const setText = (node, text) => {
  if (node && node.textContent !== text) node.textContent = text;
};
const setHtml = (node, html) => {
  if (node && node._html !== html) {
    node._html = html;
    node.innerHTML = html;
  }
};
// путь без середины: C:\...\Desktop\project
const compactPath = (path) => {
  const parts = String(path || "").split(/[\\/]+/).filter(Boolean);
  if (parts.length <= 3) return String(path || "");
  return parts[0] + "\\...\\" + parts.slice(-2).join("\\");
};
const state = {
  chat: null,
  project: null,
  projects: [],
  chats: [],
  models: [],
  modes: [],
  settings: {},
  selectedModel: "",
  page: "chat",
  running: false,
  attachments: [],
  uploads: new Map(),
  viewVersion: 0,
  viewAbort: null,
  streamAbort: null,
  streamBuffers: new Map(),
  messages: new Map(),
  messageSources: new Map(),
  followFeed: true,
  streamFrame: 0,
  syncTimer: 0,
  inspector: "diff",
  changes: [],
  changeSelection: null,
  editor: null,
  editorRequest: 0,
  fileListRequest: 0,
  inspectorProjectId: null,
  editorDrafts: new Map(),
  recording: null,
  draft: "",
  chatLoadState: "ready",
  codingSession: null,
  reasoningBusy: false,
  codingRequest: 0,
  latestUsage: null,
  planSnapshot: null,
};
const iconNames = {
  plus: "Plus",
  chat: "SquarePen",
  folder: "Folder",
  model: "Bot",
  user: "LogIn",
  graph: "ListTodo",
  clock: "CalendarClock",
  layers: "Layers",
  globe: "Globe",
  check: "Check",
  refresh: "RefreshCw",
  logs: "ScrollText",
  settings: "Settings",
  attach: "Paperclip",
  send: "ArrowUp",
  mic: "Mic",
  stop: "Square",
  trash: "Trash2",
  panelLeft: "PanelLeft",
  panelRight: "PanelRight",
  search: "Search",
  close: "X",
  copy: "Copy",
  edit: "Pencil",
  chevron: "ChevronRight",
  arrowDown: "ArrowDown",
  file: "FileCode2",
  download: "Download",
  save: "Save",
  terminal: "Terminal",
  code: "Code2",
  link: "ExternalLink",
  more: "Ellipsis",
  shield: "ShieldCheck",
  undo: "Undo2",
};
function icon(name, size = 18) {
  const definition = lucide.icons[iconNames[name] || name];
  return definition
    ? lucide.createElement(definition, {
        width: size,
        height: size,
        "aria-hidden": "true",
      }).outerHTML
    : "";
}
const svg = new Proxy({}, { get: (_, name) => icon(name) });
const escapeHtml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
function button(label, symbol, kind = "secondary", attrs = "") {
  return `<button type="button" class="${kind}" ${attrs}>${symbol ? icon(symbol) : ""}<span>${escapeHtml(label)}</span></button>`;
}
function iconButton(label, symbol, attrs = "") {
  return `<button type="button" class="icon-button" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}" ${attrs}>${icon(symbol)}</button>`;
}
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...(options.body && !(options.body instanceof FormData)
        ? { "content-type": "application/json" }
        : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    if (response.status === 401) window.koddiAuthRevoked?.();
    let detail;
    try {
      detail = (await response.json()).detail;
    } catch {
      detail = `Ошибка HTTP ${response.status}`;
    }
    throw new Error(
      typeof detail === "string"
        ? detail
        : detail?.message || `Запрос не выполнен: ${response.status}`,
    );
  }
  return response.json();
}
const post = (path, body = {}) =>
  api(path, { method: "POST", body: JSON.stringify(body) });
function status(text = "") {
  $("#status").textContent = text;
  $("#status").title = text;
  $("#status").dataset.idle = String(!text || text === "Готово");
}
function notify(text, error = false) {
  status(text);
  let toast = $("#toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "toast";
    toast.className = "toast";
    toast.setAttribute("role", "status");
    document.body.append(toast);
  }
  toast.textContent = text;
  toast.classList.toggle("error", error);
  toast.hidden = false;
  clearTimeout(window.toastTimer);
  window.toastTimer = setTimeout(() => (toast.hidden = true), 4500);
}
function guarded(action) {
  return (...args) => {
    let pending;
    try {
      pending = action(...args);
    } catch (error) {
      pending = Promise.reject(error);
    }
    return Promise.resolve(pending).catch((error) => {
      if (error.name !== "AbortError") notify(error.message, true);
    });
  };
}
async function copyText(text, event, success) {
  event.preventDefault();
  event.stopPropagation();
  if (!event.isTrusted) return;
  try {
    if (window.koddiDesktop) {
      if (typeof window.koddiClipboardWrite !== "function" ||
          !await window.koddiClipboardWrite(text, event)) throw new Error();
    } else {
      await navigator.clipboard.writeText(text);
    }
    notify(success);
  } catch {
    notify("Не удалось скопировать в буфер обмена", true);
  }
}
function clearFeed() {
  if (typeof closeCloudImage === "function") closeCloudImage(true);
  document.querySelector(".ai-source-popover")?.remove();
  if (typeof clearCloudProgress === "function") clearCloudProgress();
  $("#feed").replaceChildren();
  state.messages.clear();
  state.messageSources.clear();
  state.streamBuffers.clear();
  if (typeof updateJumpBottom === "function") updateJumpBottom();
}
function setRunning(value) {
  state.running = Boolean(value);
  const send = $("#send");
  const sendIcon = state.running ? "stop" : "send";
  if (send.dataset.icon !== sendIcon) {
    send.dataset.icon = sendIcon;
    send.innerHTML = icon(sendIcon);
  }
  send.classList.toggle("is-running", state.running);
  send.title = state.running ? "Остановить выполнение" : "Отправить";
  send.setAttribute("aria-label", send.title);
  if ($('#queue-draft')) $('#queue-draft').hidden = !(state.running && window.koddiProductScope === 'development' && $('#prompt').value.trim());
  document.body.classList.toggle("agent-working", state.running);
  if (typeof localWaiting === "function") localWaiting(state.running);
  if (typeof updateProductNavigation === "function") updateProductNavigation();
  $("#prompt").placeholder = state.running
    ? "Агент работает…"
    : window.koddiProductScope === "chat"
      ? "Спросить Докки"
    : window.koddiProductScope === "search"
      ? "Что найти в интернете?"
    : state.project
      ? "Что сделаем в проекте?"
      : "Опишите задачу";
}
// пока панель едет, ширина колонки меняется и строки перестраиваются:
// выбранный абзац удерживается на том же месте экрана, лента не «улетает»
function holdReadingPosition(duration = 520) {
  const feed = $("#feed");
  if (!feed || state.page !== "chat") return;
  const box = feed.getBoundingClientRect();
  const target = state.followFeed
    ? null
    : document
        .elementsFromPoint(box.left + box.width / 2, box.top + box.height / 2)
        .find((node) => feed.contains(node) && node.matches("p,li,h1,h2,h3,h4,pre,table,blockquote,.code-block,.activity,.journal-commentary,.message-actions"));
  const offset = target ? target.getBoundingClientRect().top - box.top : 0;
  const end = performance.now() + duration;
  cancelAnimationFrame(holdReadingPosition.frame);
  const tick = () => {
    if (target?.isConnected) {
      const delta = target.getBoundingClientRect().top - feed.getBoundingClientRect().top - offset;
      if (Math.abs(delta) > 0.5) feed.scrollTop += delta;
    } else if (state.followFeed) follow();
    if (performance.now() < end) holdReadingPosition.frame = requestAnimationFrame(tick);
  };
  holdReadingPosition.frame = requestAnimationFrame(tick);
}
function setPanel(name, hidden, persist = true) {
  const shell = $("#shell");
  holdReadingPosition();
  shell.classList.toggle(`${name}-collapsed`, hidden);
  $(`#toggle-${name}`)?.setAttribute("aria-expanded", String(!hidden));
  if (persist)
    api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ [`${name}_panel_collapsed`]: hidden }),
    }).catch((error) => notify(error.message, true));
}
function switchView(key) {
  clearAttachments();
  ++state.viewVersion;
  state.viewAbort?.abort();
  state.viewAbort = new AbortController();
  state.streamAbort?.abort();
  state.streamAbort = null;
  clearTimeout(state.syncTimer);
  state.page = key;
  state.codingSession = null;
  state.reasoningBusy = false;
  ++state.codingRequest;
  state.latestUsage = null;
  state.planSnapshot = null;
  state.changeSelection=null;
  renderChangeInspector([]);
  $('#task-plan').replaceChildren();
  clearWorkspaceEditor();
  state.inspectorProjectId=null;
  $('#file-tree').replaceChildren();
  setChatLoadState('ready');
  clearFeed();
  document.querySelector("main").classList.toggle("chat-page", key === "chat");
  // «Новый чат» - обычная кнопка: подсвечиваются только разделы с data-page
  document
    .querySelectorAll(".nav[data-page]")
    .forEach((node) =>
      node.classList.toggle("active", key !== "chat" && node.dataset.page === key),
    );
  if (window.innerWidth < 720) setPanel("left", true, false);
  return { version: state.viewVersion, signal: state.viewAbort.signal };
}
function skeleton() {
  $("#feed").innerHTML =
    '<div class="chat-skeleton skeleton" aria-label="Загрузка..."><i></i><i></i><i></i></div>';
}
function setChatLoadState(value) {
  state.chatLoadState=value;
  $('#composer').inert=value!=='ready';
  $('#feed').setAttribute('aria-busy',String(value==='loading'));
}
async function loadChatView(chat,view,requests) {
  setChatLoadState('loading');
  setRunning(false);
  try {
    const values=await Promise.all(requests);
    if(view.signal.aborted||view.version!==state.viewVersion||state.chat?.id!==chat.id)return null;
    setChatLoadState('ready');
    return values;
  } catch(error) {
    if(error.name==='AbortError'||view.signal.aborted||view.version!==state.viewVersion||state.chat?.id!==chat.id)return null;
    clearFeed();setChatLoadState('failed');
    const root=document.createElement('section');root.className='chat-load-error';
    const title=document.createElement('h2');title.textContent='Не удалось открыть чат';
    const detail=document.createElement('p');detail.textContent=error.message;
    root.append(title,detail);root.insertAdjacentHTML('beforeend',button('Повторить загрузку','refresh'));
    $('button',root).onclick=guarded(()=>openChat(chat));
    $('#feed').append(root);status('Не удалось загрузить историю чата');
    return null;
  }
}
function empty(root, title, detail = "") {
  root.innerHTML = `<div class="empty-list">${icon("folder", 24)}<h3>${escapeHtml(title)}</h3>${detail ? `<p>${escapeHtml(detail)}</p>` : ""}</div>`;
}
function renderMarkdown(target, source, highlight = true, options = {}) {
  // ответы Докки (чат и поиск) приводятся к виду сайта: картинки, видео, файлы, спойлеры
  const rich =
    typeof richPrepare === "function" &&
    window.koddiProductScope &&
    window.koddiProductScope !== "development";
  const parsed = marked.parse(
    rich ? richPrepare(source, options) : String(source || ""),
    { gfm: true, breaks: false },
  );
  const fragment = DOMPurify.sanitize(
    rich ? richChecklist(parsed) : parsed,
    {
      USE_PROFILES: { html: true },
      FORBID_TAGS: ["style", "iframe", "form", "input"],
      FORBID_ATTR: ["style"],
      RETURN_DOM_FRAGMENT: true,
    },
  );
  fragment.querySelectorAll("img").forEach((image) => {
    const src = image.getAttribute("src") || "";
    // картинки сайта и внешние адреса обрабатывает richHydrate, остальное не показывается
    if (rich && (src.startsWith("/__rich__/") || /^https?:\/\//i.test(src))) return;
    if (!/^\/api\/attachments\/\d+\/content$/.test(src)) image.remove();
  });
  target.replaceChildren(fragment);
  target.querySelectorAll("a").forEach((link) => {
    const href = link.getAttribute("href") || "";
    if (state.project && href && !/^[a-z]+:|^\/\/|^#|^\/api\//i.test(href)) {
      link.onclick = guarded((event) => {
        event.preventDefault();
        return openFile(decodeURIComponent(href));
      });
      link.title = "Открыть файл в проекте";
    } else if (window.koddiDesktop && /^https?:\/\//i.test(href)) {
      link.onclick = (event) => {
        event.preventDefault();
        window.location.href = `koddi://open-url?url=${encodeURIComponent(href)}`;
      };
    }
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  });
  target.querySelectorAll("pre").forEach((pre) => {
    const code = pre.querySelector("code");
    if (!code) return;
    const language =
      (code.className.match(/language-([^\s]+)/) || [])[1] || "Код";
    if (
      highlight &&
      window.hljs &&
      hljs.getLanguage(language) &&
      code.textContent.length < 50000
    ) {
      try {
        code.innerHTML = hljs.highlight(code.textContent, {
          language,
          ignoreIllegals: true,
        }).value;
      } catch {}
    }
    const container = document.createElement("div");
    container.className = "code-block";
    const head = document.createElement("div");
    head.className = "code-head";
    head.innerHTML = `<span>${escapeHtml(language)}</span>${iconButton("Скопировать код", "copy")}`;
    head.querySelector("button").onclick = (event) => copyText(
      event.currentTarget.closest(".code-block").querySelector("pre code").textContent,
      event,
      "Код скопирован",
    );
    pre.replaceWith(container);
    container.append(head, pre);
  });
  target.querySelectorAll("table").forEach((table) => {
    const wrapper = document.createElement("div");
    wrapper.className = "table-scroll";
    table.replaceWith(wrapper);
    wrapper.append(table);
  });
  if (rich) richHydrate(target, options);
}
function isNearFeedBottom() {
  const feed = $("#feed");
  return feed.scrollHeight - feed.scrollTop - feed.clientHeight < 64;
}
function updateJumpBottom() {
  const hasMessages = Boolean(
    $("#feed [data-message-id]") || $("#feed .cloud-work"),
  );
  const hidden = state.page !== "chat" || !hasMessages || isNearFeedBottom();
  if ($("#jump-bottom").hidden !== hidden) $("#jump-bottom").hidden = hidden;
}
function follow() {
  if (state.followFeed) $("#feed").scrollTop = $("#feed").scrollHeight;
  updateJumpBottom();
}
function initChatInsets() {
  const main = $('main'), feed = $('#feed'), header = $('.chat-header'), composer = $('#composer');
  let frame = 0;
  const update = () => {
    frame = 0;
    if (!main.classList.contains('chat-page')) return;
    const top = Math.ceil(header.getBoundingClientRect().height) + 20;
    const bottom = Math.ceil(composer.getBoundingClientRect().height) + 20;
    const before = parseFloat(getComputedStyle(feed).paddingTop) || 0;
    const scroll = feed.scrollTop, following = state.followFeed;
    main.style.setProperty('--feed-top', `${top}px`);
    main.style.setProperty('--feed-bottom', `${bottom}px`);
    if (following) follow();
    else feed.scrollTop = scroll + top - before;
  };
  const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
  const observer = new ResizeObserver(schedule);
  for (const node of [header, composer, main]) observer.observe(node);
  new MutationObserver(schedule).observe(main, {attributes:true, attributeFilter:['class']});
  window.visualViewport?.addEventListener('resize', schedule);
  schedule();
}
function actionText(event) {
  const data = event.data || {},
    args = data.arguments || data.args || {},
    result = data.result || {};
  const pending = event.event === "tool.call",
    path = args.path || args.source || result.path || "";
  const added = result.added_lines ?? result.lines_added,
    removed = result.removed_lines ?? result.lines_removed;
  const delta = added !== undefined ? ` · +${added} −${removed || 0}` : "";
  const displayName = data.display_name || data.name;
  // коротко и в одну строку: команда, запрос или сайт видны без раскрытия
  const brief = (value) => {
    const line = String(value ?? "").split(/[\r\n]/)[0].trim();
    return line.length > 90 ? line.slice(0, 87) + "…" : line;
  };
  const command = brief(args.command || args.cmd),
    query = brief(args.query || args.pattern);
  let site = "";
  try { site = new URL(String(args.url || "")).hostname.replace(/^www\./, ""); } catch {}
  const titles = {
    'tool.output_read': ['Читаю полный результат действия','Полный результат действия прочитан'],
    "project.instructions": ["Читаю правила проекта", "Правила проекта изучены"],
    "file.create": [`Создаю файл: ${path}`, `Создан файл: ${path}${delta}`],
    "file.write_patch": [
      `Редактирую: ${path}`,
      `Изменён файл: ${path}${delta}`,
    ],
    "file.edit": [`Редактирую: ${path}`, `Изменён файл: ${path}${delta}`],
    "file.apply_patch": ["Применяю изменения", "Изменения применены"],
    "file.read": [`Читаю: ${path}`, `Прочитан файл: ${path}`],
    "directory.list": ["Изучаю папку проекта", "Папка проекта изучена"],
    "directory.tree": ["Изучаю структуру проекта", "Структура проекта изучена"],
    "search.text": [query ? `Ищу в проекте: ${query}` : "Ищу в проекте", query ? `Поиск в проекте: ${query}` : "Поиск в проекте завершён"],
    "web.search": [query ? `Ищу в интернете: ${query}` : "Ищу информацию в интернете", query ? `Поиск в интернете: ${query}` : "Поиск в интернете завершён"],
    "web.fetch": [site ? `Открываю ${site}` : "Открываю источник", site ? `Изучен источник: ${site}` : "Источник изучен"],
    "shell.run": [command ? `Выполняю: ${command}` : "Выполняю команду", command ? `Выполнена команда: ${command}` : "Команда выполнена"],
    "verification.run": [command ? `Проверяю: ${command}` : "Проверяю результат", command ? `Проверено: ${command}` : "Результат проверен"],
    "test.run": [command ? `Запускаю тесты: ${command}` : "Запускаю тесты", command ? `Тесты выполнены: ${command}` : "Тесты выполнены"],
    "plan.update": ["Обновляю план", "План обновлён"],
    "task.delegate": ["Передаю подзадачу агенту", "Подзадача выполнена"],
    "task.complete": ["Подвожу итог", "Задача завершена"],
    "task.ask_user": ["Ожидаю уточнения", "Уточнение получено"],
  };
  if (!pending && result.ok === false)
    return `Не выполнено: ${titles[data.name]?.[1] || displayName}`;
  return (titles[data.name] || [
    `Выполняю: ${displayName}`,
    `Выполнено: ${displayName}`,
  ])[pending ? 0 : 1];
}
function activityActions(events) {
  const calls = new Map();
  for (const event of events || []) {
    if (!["tool.call", "tool.result"].includes(event.event)) continue;
    const key = event.data?.id || event.data?.name;
    calls.set(key, event);
  }
  return [...calls.values()];
}
function addActivity(metadata, parent, previousOpen = false) {
  const actions = activityActions(metadata.events);
  if (!actions.length) return;
  const node = document.createElement("details");
  node.className = "activity";
  node.open = previousOpen;
  const running = metadata.status === "running",
    failed =
      metadata.status === "failed" ||
      actions.some((e) => e.data?.result?.ok === false);
  const label = running
    ? actionText(actions.at(-1))
    : metadata.status === "cancelled"
      ? "Выполнение остановлено"
      : failed
        ? "Есть действия с ошибкой"
        : `Выполнено действий: ${actions.length}`;
  node.classList.toggle("is-running", running);
  node.innerHTML = `<summary>${icon("chevron")}<span>${escapeHtml(label)}</span></summary><div class="activity-body"><div class="activity-content"></div></div>`;
  for (const event of actions) {
    const line = document.createElement("div");
    line.className = "activity-line";
    line.classList.toggle("pending", event.event === "tool.call");
    const result = event.data?.result;
    line.innerHTML = `<span class="action-state">${icon(event.event === "tool.call" ? "clock" : result?.ok === false ? "close" : "check", 14)}</span><span>${escapeHtml(actionText(event))}</span>`;
    if (result?.error || result?.reason) {
      const reason = document.createElement("p");
      reason.className = "error-detail";
      reason.textContent = result.error || result.reason;
      line.append(reason);
    }
    if (event.data?.arguments?.path && state.project)
      line.onclick = guarded(() => openFile(event.data.arguments.path));
    $(".activity-content", node).append(line);
  }
  parent.append(node);
}
function createMessage(item) {
  const node = document.createElement("section");
  node.className = `turn-message ${item.role}`;
  node.dataset.messageId = item.id;
  updateMessage(node, item);
  return node;
}
function messageSignature(item) {
  if(item.metadata?.kind==='journal'&&typeof localJournalSignature==='function')return localJournalSignature(item);
  // результаты инструментов могут весить мегабайты: в подпись идёт только то, что рисуется
  const { events, ...rest } = item.metadata || {};
  const digest = (events || []).map((event) => {
    const data = event.data || {},
      args = data.arguments || data.args || {},
      result = data.result || {};
    return [
      event.event,
      data.id,
      data.name,
      data.display_name,
      args.path || args.source,
      result.path,
      result.ok,
      result.added_lines ?? result.lines_added,
      result.removed_lines ?? result.lines_removed,
      result.error,
      result.reason,
    ];
  });
  return JSON.stringify([item.content, rest, digest]);
}
function messageActions(item) {
  const actions = document.createElement("div");
  actions.className = "message-actions";
  actions.innerHTML =
    iconButton("Скопировать сообщение", "copy") +
    (item.role === "user" ? iconButton("Изменить сообщение", "edit") : "");
  actions.firstElementChild.onclick = (event) =>
    copyText(item.content, event, "Сообщение скопировано");
  if (item.role === "user")
    actions.lastElementChild.onclick = guarded(() => editMessage(item));
  return actions;
}
// готовый ответ заменяет живой в том же узле: без мигания и повторной анимации
function continueStreamedMessage(node, item, metadata, body, previousOpen) {
  const article = body.parentElement;
  node.querySelectorAll(":scope > .activity").forEach((line) => line.remove());
  if (metadata.events?.length) {
    const holder = document.createElement("div");
    addActivity(metadata, holder, previousOpen);
    node.prepend(...holder.children);
  }
  node._messageSignature = messageSignature(item);
  if (metadata.status === "running") {
    body._settle = (body._settle || 0) + 1;
    cancelCloudSettle(body);
    renderCloudStreamMarkdown(body, item.content);
    return;
  }
  const token = (body._settle = (body._settle || 0) + 1);
  settleCloudStream(body, async () => {
    await waitStreamAnimations(body);
    if (body._settle !== token || !body.isConnected) return;
    const draft = document.createElement("div");
    renderMarkdown(draft, item.content, true, { message: item });
    reconcileStreamNodes(body, draft);
    article.classList.remove("streaming");
    article.append(messageActions(item));
    if (window.koddiProductScope && window.koddiProductScope !== "development")
      renderCloudExtras(node, item);
  });
}
function updateMessage(node, item) {
  if (typeof disposeCloudMessage === "function") disposeCloudMessage(node);
  const metadata = item.metadata || {},
    previousOpen = $("details", node)?.open || false;
  if (metadata.kind === "journal") {
    renderLocalJournal(node, item, previousOpen);
    node._messageSignature = messageSignature(item);
    return;
  }
  const live =
    item.role === "assistant" && item.content
      ? node.querySelector(":scope > article.streaming > .message-body")
      : null;
  if (live) {
    continueStreamedMessage(node, item, metadata, live, previousOpen);
    return;
  }
  node.replaceChildren();
  if (metadata.events?.length) addActivity(metadata, node, previousOpen);
  if (item.content) {
    const article = document.createElement("article");
    article.className = `message ${item.role}`;
    // идущий ответ продолжится потоком и завершится в этом же узле
    if (item.role === "assistant" && metadata.status === "running")
      article.classList.add("streaming");
    const body = document.createElement("div");
    body.className = "message-body";
    if (item.role === "user") body.textContent = item.content;
    else renderMarkdown(body, item.content, metadata.status !== "running", { message: item, streaming: metadata.status === "running" });
    body._shownLength = item.content.length;
    article.append(body);
    if (metadata.status !== "running") article.append(messageActions(item));
    node.append(article);
  }
  if (metadata.status === "failed" && !item.content && !metadata.events?.length)
    node.innerHTML =
      '<p class="error-detail">Ответ прерван. Повторите запрос.</p>';
  node._messageSignature = messageSignature(item);
  if (window.koddiProductScope && window.koddiProductScope !== "development")
    renderCloudExtras(node, item);
}
function renderChatMessages(messages) {
  messages = messages.map((item) => {
    const id = String(item.id);
    state.messageSources.set(id, item);
    const buffer = state.streamBuffers.get(id);
    if (buffer) buffer.synced = true;
    if (item.role === "assistant" && item.metadata?.status !== "running") {
      state.streamBuffers.delete(id);
    } else if (buffer && buffer.content.length >= (item.content || "").length) {
      return { ...item, content: buffer.content };
    }
    return item;
  });
  const grouped = [];
  if (typeof projectCloudVariants === "function")
    messages = projectCloudVariants(messages);
  if (typeof projectLocalJournal === "function")
    messages = projectLocalJournal(messages);
  for (const item of messages) {
    if (
      !item.content &&
      !item.metadata?.events?.length &&
      !item.attachments?.length &&
      !item.artifacts?.length &&
      !item.activity?.length &&
      item.metadata?.kind !== "journal"
    )
      continue;
    const previous = grouped.at(-1);
    if (
      !item.content &&
      item.metadata?.events?.length &&
      previous &&
      !previous.content &&
      previous.metadata?.events?.length
    ) {
      previous.metadata = {
        ...item.metadata,
        events: [...previous.metadata.events, ...item.metadata.events],
      };
    } else grouped.push({ ...item, metadata: { ...item.metadata } });
  }
  messages = grouped;
  const root = $("#feed");
  $(".chat-skeleton", root)?.remove();
  $(".empty", root)?.remove();
  const hadMessages = Boolean($("[data-message-id]", root));
  const wanted = new Set(messages.map((m) => String(m.id)));
  // временный узел (поток или только что отправленный текст) становится сообщением сервера
  for (const item of messages) {
    const id = String(item.id);
    if (state.messages.get(id)?.isConnected || id.startsWith("pending")) continue;
    const donor = [...root.children].find(
      (candidate) =>
        String(candidate.dataset.messageId || "").startsWith("pending") &&
        !wanted.has(candidate.dataset.messageId) &&
        candidate.classList.contains(item.role) &&
        (item.role !== "assistant" ||
          typeof dokkyState === "undefined" ||
          !dokkyState.run ||
          candidate.dataset.messageId === `pending_${dokkyState.run}`) &&
        (item.role === "assistant"
          ? candidate.querySelector(":scope > article.streaming")
          : $(".message-body", candidate)?.textContent === item.content),
    );
    if (!donor) continue;
    state.messages.delete(donor.dataset.messageId);
    state.messages.set(id, donor);
    donor.dataset.messageId = id;
    donor._messageSignature = null;
    donor.classList.remove("is-new");
  }
  // живой текст переезжает между лентой и журналом тем же узлом
  state.orphanBodies = new Map();
  state.adoptedRows = new Map();
  if (typeof journalRowFor === "function")
    for (const item of messages) {
      const id = String(item.id);
      if (item.role !== "assistant" || item.metadata?.kind === "journal" || state.messages.get(id)?.isConnected) continue;
      const row = journalRowFor(id);
      if (row) {
        row._adoptedAway = true;
        state.adoptedRows.set(id, row);
      }
    }
  for (const child of [...root.children])
    if (child.dataset.messageId && !wanted.has(child.dataset.messageId)) {
      const body = child.classList.contains("assistant") && $(":scope > article > .message-body", child);
      if (body) state.orphanBodies.set(child.dataset.messageId, body);
      if (typeof disposeCloudMessage === "function") disposeCloudMessage(child);
      state.messages.delete(child.dataset.messageId);
      child.remove();
    }
  let cursor = [...root.children].find(child=>child.dataset.messageId) || null;
  for (const item of messages) {
    const id = String(item.id);
    let node = state.messages.get(id);
    if (!node?.isConnected) {
      node = createMessage(item);
      const row = state.adoptedRows.get(id);
      if (row) {
        const body = $(":scope > article > .message-body", node);
        if (body) {
          body.replaceWith(row);
          row.className = "message-body";
          row._adoptedAway = false;
          const draft = document.createElement("div");
          renderMarkdown(draft, item.content, true, { message: item });
          settleCloudStream(row, async () => {
            await waitStreamAnimations(row);
            if (row.isConnected) reconcileStreamNodes(row, draft);
          });
        }
      } else if (hadMessages) node.classList.add("is-new");
      root.append(node);
      state.messages.set(id, node);
    } else if (
      node._messageSignature !==
      messageSignature(item)
    )
      updateMessage(node, item);
    if (node !== cursor) root.insertBefore(node, cursor);
    cursor = node.nextElementSibling;
    while(cursor && !cursor.dataset.messageId) cursor=cursor.nextElementSibling;
  }
  state.orphanBodies = null;
  state.adoptedRows = null;
  if (typeof placeCloudProgress === "function") placeCloudProgress();
  follow();
}
function queueStreamDelta(chatId, data) {
  if (
    state.page !== "chat" ||
    state.chat?.id !== chatId ||
    typeof data.content !== "string"
  )
    return;
  const id = String(data.message_id || "stream");
  const buffer = state.streamBuffers.get(id) || {
    content: state.messageSources.get(id)?.content || "",
    node: null,
    dirty: false,
  };
  const previousContent = buffer.content;
  if (data.replace) buffer.content = data.content;
  else if (Number.isInteger(data.offset)) {
    buffer.content = buffer.content.slice(0, data.offset) + data.content;
  } else buffer.content += data.content;
  buffer.dirty ||= previousContent !== buffer.content;
  state.streamBuffers.set(id, buffer);
  if (state.streamFrame) return;
  state.streamFrame = requestAnimationFrame(flushStreamBuffers);
}
function flushStreamBuffers() {
  state.streamFrame = 0;
  let held = false;
  for (const [key, value] of state.streamBuffers) {
    if (!value.dirty) continue;
    value.dirty = false;
    let node = state.messages.get(key);
    if (!node?.isConnected && typeof journalRowFor === "function") {
      // шаг, который станет комментарием, пишется прямо в журнале, а не отдельным блоком
      const row = journalRowFor(key);
      if (row) {
        renderCloudStreamMarkdown(row, value.content);
        continue;
      }
      // пока сервер не ответил, неизвестно, комментарий это или итог: не создаём блок вне журнала
      value.heldAt ??= performance.now();
      if (journalRunning() && !value.synced && performance.now() - value.heldAt < 700) {
        if (!value.syncAsked) {
          value.syncAsked = true;
          scheduleChatSync(0);
        }
        value.dirty = true;
        held = true;
        continue;
      }
    }
    if (!node?.isConnected) {
      node = createMessage({
        id: key,
        role: "assistant",
        content: "",
        metadata: { status: "running" },
      });
      $("#feed").append(node);
      state.messages.set(key, node);
    }
    let article = $("article", node);
    if (!article) {
      article = document.createElement("article");
      article.className = "message assistant streaming";
      article.innerHTML = '<div class="message-body"></div>';
      node.append(article);
    }
    renderCloudStreamMarkdown($(".message-body", article), value.content);
  }
  follow();
  if (typeof placeCloudProgress === "function") placeCloudProgress();
  if (held) state.streamFrame = requestAnimationFrame(flushStreamBuffers);
}
function resetStreamMessage(data) {
  const id = String(data.message_id || 'stream');
  state.streamBuffers.delete(id);
  const source = state.messageSources.get(id);
  if (source) state.messageSources.set(id, {...source, content:''});
  const node = state.messages.get(id);
  if (node?.isConnected) {
    const item = source || {id, role:'assistant', metadata:{status:'running'}};
    updateMessage(node, {...item, content:'', metadata:{...item.metadata, status:'running'}});
  }
}
function pendingTool(data) {
  if (!data.id || !data.name) return;
  let node = [...document.querySelectorAll(".pending-tool")].find(
    (item) => item.dataset.callId === data.id,
  );
  if (!node) {
    node = document.createElement("details");
    node.className = "activity is-running pending-tool";
    node.dataset.callId = data.id;
    $("#feed").append(node);
  }
  const title = actionText({
    event: "tool.call",
    data: { name: data.name, arguments: {} },
  });
  node.innerHTML = `<summary>${icon("chevron")}<span>${escapeHtml(title)}<small> · получаю параметры, ${(Number(data.bytes_received || 0) / 1024).toFixed(1)} КБ</small></span></summary>`;
  follow();
}
function updateEnvironment() {
  if (typeof refreshProjectPermissions === 'function') guarded(refreshProjectPermissions)();
  if (typeof refreshLocalQueue === 'function') guarded(refreshLocalQueue)();
  const project = state.project;
  if(state.editor&&state.editor.projectId!==project?.id)clearWorkspaceEditor();
  if(state.inspectorProjectId!==project?.id) {
    state.inspectorProjectId=project?.id;
    ++state.fileListRequest;
    $('#file-tree').replaceChildren();
    if(state.inspector==='files'&&!$('#shell').classList.contains('right-collapsed'))guarded(loadFiles)();
  }
  setHtml(
    $("#environment"),
    `<dl class="environment-list">${project?.path ? `<dt>Папка</dt><dd class="path-value" title="${escapeHtml(project.path)}">${escapeHtml(compactPath(project.path))}</dd>` : ""}<dt>Проект</dt><dd>${escapeHtml(project?.name || "Не выбран")}</dd><dt>Модель</dt><dd>${escapeHtml(state.chat?.model || state.selectedModel || "Не выбрана")}</dd><dt>Подтверждения</dt><dd>${escapeHtml(state.settings.permission_profile === "trusted" ? "Автоматически" : "Вручную")}</dd></dl>`,
  );
  setHtml(
    $("#workspace-selector"),
    icon("folder", 15) +
      `<span>${escapeHtml(project?.name || "Выбрать проект")}</span>` +
      icon("chevron", 12),
  );
  // подпись аккаунта (имя пользователя) выставляет dokkyChrome, здесь только состояние
  const connection = $("#connection-badge");
  const connected = state.models.length ? "connected" : "offline";
  if (connection.dataset.state !== connected && connection.dataset.state !== "ready")
    connection.dataset.state = connected;
}
function renderModels() {
  const saved =
    (state.page === "chat" ? state.chat?.model : "") ||
    state.settings.selected_model ||
    state.selectedModel;
  $("#model")._signature = null;
  $("#model").innerHTML =
    state.models
      .map(
        (m) =>
          `<option value="${escapeHtml(m.tag)}">${escapeHtml(m.name || m.tag)}</option>`,
      )
      .join("") || '<option value="">Подключить Докки</option>';
  if (state.models.some((m) => m.tag === saved)) $("#model").value = saved;
  state.selectedModel = $("#model").value;
}
// какие проекты раскрыты, помним между запусками
const openProjects = {
  read() {
    try {
      return new Set(JSON.parse(localStorage.getItem("koddi-open-projects") || "[]"));
    } catch {
      return new Set();
    }
  },
  write(id, open) {
    const items = openProjects.read();
    open ? items.add(String(id)) : items.delete(String(id));
    try {
      localStorage.setItem("koddi-open-projects", JSON.stringify([...items]));
    } catch {
      /* без хранилища список просто не запоминается */
    }
  },
};
// на каком проекте остановились: новый чат и запуск приложения начинаются с него
function rememberProject(project) {
  try {
    if (project) localStorage.setItem("koddi-last-project", String(project.id));
  } catch {
    /* хранилище недоступно */
  }
}
function lastProject() {
  try {
    const id = localStorage.getItem("koddi-last-project");
    return state.projects.find((p) => String(p.id) === id) || null;
  } catch {
    return null;
  }
}
function syncSidebarSelection() {
  document.querySelectorAll("#left .chat-row").forEach((row) => {
    row.classList.toggle("selected", row.dataset.chatId === String(state.chat?.id ?? ""));
  });
}
function renderSidebar(query = "") {
  const local =
    !window.koddiProductScope || window.koddiProductScope === "development";
  // список перерисовывается только если изменились сами данные, выбор чата меняет один класс
  const signature = JSON.stringify([
    query,
    local,
    state.projects.map((p) => [p.id, p.name]),
    state.chats.map((c) => [c.id, c.title, c.updated_at, c.project_id]),
  ]);
  if (renderSidebar.signature === signature && $("#chats").childElementCount) {
    syncSidebarSelection();
    return;
  }
  renderSidebar.signature = signature;
  $("#projects").replaceChildren();
  $("#chats").replaceChildren();
  const matching = state.chats.filter((chat) =>
    (chat.title || "").toLowerCase().includes(query.toLowerCase()),
  );
  const recent = (items) =>
    [...items].sort(
      (a, b) =>
        String(b.updated_at || "").localeCompare(String(a.updated_at || "")) ||
        Number(b.id) - Number(a.id),
    );
  const updated = new Map();
  for (const chat of state.chats)
    if (String(chat.updated_at || "") > (updated.get(chat.project_id) || ""))
      updated.set(chat.project_id, chat.updated_at);
  const projects = [...state.projects].sort((a, b) =>
    (updated.get(b.id) || "").localeCompare(updated.get(a.id) || ""),
  );
  for (const project of local ? projects : []) {
    const chats = recent(
      matching.filter((chat) => chat.project_id === project.id),
    );
    if (
      query &&
      !chats.length &&
      !project.name.toLowerCase().includes(query.toLowerCase())
    )
      continue;
    const group = document.createElement("details");
    group.className = "project-group";
    group.open =
      state.project?.id === project.id || openProjects.read().has(String(project.id));
    group.ontoggle = () => openProjects.write(project.id, group.open);
    const header = document.createElement("summary");
    header.innerHTML =
      icon("chevron") +
      icon("folder") +
      `<span>${escapeHtml(project.name)}</span>` +
      iconButton(`Новый чат в проекте ${project.name}`, "plus");
    $('span',header).title=project.name;
    $("button", header).onclick = (event) => {
      event.preventDefault();
      event.stopPropagation();
      newChat(project);
    };
    group.append(header);
    for (const chat of chats) group.append(chatSidebarRow(chat));
    if (!chats.length) {
      const empty = document.createElement("span");
      empty.className = "list-empty";
      empty.textContent = "Нет чатов";
      group.append(empty);
    }
    $("#projects").append(group);
  }
  if (!state.projects.length)
    $("#projects").innerHTML =
      '<span class="list-empty">Проектов пока нет</span>';
  const chats = local
    ? recent(matching.filter((chat) => !chat.project_id))
    : matching;
  for (const chat of chats) {
    $("#chats").append(chatSidebarRow(chat));
  }
  if (!chats.length)
    $("#chats").innerHTML =
      `<span class="list-empty">${query ? "Ничего не найдено" : "Чатов пока нет"}</span>`;
}
function chatSidebarRow(chat) {
  const row = document.createElement("div");
  row.className = "chat-row";
  row.dataset.chatId = String(chat.id);
  row.classList.toggle("selected", chat.id === state.chat?.id);
  row.innerHTML = `<button class="item" title="${escapeHtml(chat.title)}"><span>${escapeHtml(chat.title)}</span></button>${iconButton("Действия с чатом", "more")}`;
  row.firstElementChild.onclick = guarded(() => openChat(chat));
  row.lastElementChild.onclick = (event) => {
    event.stopPropagation();
    chatMenu(chat, event);
  };
  row.oncontextmenu = (event) => {
    event.preventDefault();
    chatMenu(chat, event);
  };
  return row;
}
async function refresh({ includeModels = true } = {}) {
  const [projects, chats, settings, models, modes] = await Promise.all([
    api("/api/projects"),
    api("/api/chats"),
    api("/api/settings"),
    includeModels ? api("/api/models/options") : Promise.resolve(null),
    api("/api/modes"),
  ]);
  state.projects = projects;
  state.chats = chats;
  state.settings = settings;
  state.modes = modes;
  [$("#projects"), $("#chats"), $("#environment")].forEach((node) => {
    node.classList.remove("skeleton", "loading");
    node.setAttribute("aria-busy", "false");
  });
  if (models) state.models = models;
  document.documentElement.dataset.theme =
    settings.theme === "light" ? "light" : "dark";
  if (typeof applyAppearance === "function") applyAppearance();
  const mode = $("#mode").value || "development";
  $("#mode").innerHTML = modes
    .map(
      (m) =>
        `<option value="${escapeHtml(m.id)}">${escapeHtml(m.name)}</option>`,
    )
    .join("");
  $("#mode").value = modes.some((m) => m.id === mode) ? mode : "development";
  renderModels();
  renderSidebar();
  updateEnvironment();
  document.body.classList.remove("booting");
}
function newChat(project = null) {
  switchView("chat");
  state.chat = null;
  state.project = typeof project === "object" ? project : null;
  rememberProject(state.project);
  state.followFeed = true;
  setRunning(false);
  $("#title").textContent = "";
  $("#mode").value = "development";
  $("#prompt").value = "";
  autoSize();
  const root = document.createElement("section");
  root.className = "empty welcome";
  root.innerHTML = `<div class="welcome-mascot" aria-hidden="true"><img class="welcome-mark" src="assets/mark.png" alt=""></div><h1>${state.project ? "Что сделаем в проекте?" : "Что создадим сегодня?"}</h1><div class="actions">${button(state.project ? state.project.name : "Открыть проект", "folder")}${button("Новый проект", "plus")}</div>`;
  const buttons = root.querySelectorAll("button");
  buttons[0].onclick = guarded(openFolder);
  buttons[1].onclick = guarded(createProject);
  $("#feed").append(root);
  renderModels();
  renderSidebar();
  updateEnvironment();
  updateSegment();
  $("#request-panel").replaceChildren();
  $("#task-plan").replaceChildren();
  renderChangeInspector([]);
}
async function openFolder() {
  if (window.koddiDesktop || navigator.userAgent.includes("QtWebEngine")) {
    window.location.href = "koddi://open-folder";
    return;
  }
  const path = await inputDialog("Открыть проект", [
    {
      name: "path",
      label: "Путь к папке",
      placeholder: "C:\\Projects\\MyProject",
      required: true,
    },
  ]);
  if (!path) return;
  const project = await post("/api/projects", path);
  await refresh({ includeModels: false });
  newChat(project);
}
async function createProject() {
  const value = await inputDialog(
    "Новый проект",
    [{ name: "name", label: "Название", required: true }],
    "Создать",
  );
  if (!value) return;
  const project = await post("/api/projects/create-empty", value);
  await refresh({ includeModels: false });
  newChat(project);
}
async function chooseWorkspace() {
  const fields = [
    {
      name: "project_id",
      label: "Проект",
      type: "select",
      options: [
        { value: "", label: "Без проекта" },
        ...state.projects.map((p) => ({ value: p.id, label: p.name })),
      ],
      value: state.project?.id || "",
    },
  ];
  const value = await inputDialog("Рабочая папка", fields);
  if (!value) return;
  const project =
    state.projects.find((p) => p.id === Number(value.project_id)) || null;
  if (state.chat)
    state.chat = await api(`/api/chats/${state.chat.id}`, {
      method: "PATCH",
      body: JSON.stringify({ project_id: project?.id || null }),
    });
  state.project = project;
  updateEnvironment();
  if (!state.chat) newChat(project);
}
async function openChat(chat) {
  const view = switchView("chat");
  state.chat = chat;
  state.project = state.projects.find((p) => p.id === chat.project_id) || null;
  rememberProject(state.project);
  state.followFeed = true;
  $("#title").textContent = chat.title;
  $("#mode").value = chat.mode === "build" ? "development" : chat.mode;
  renderModels();
  renderSidebar();
  updateEnvironment();
  updateSegment();
  skeleton();
  const loaded = await loadChatView(chat,view,[
    api(`/api/chats/${chat.id}/messages`, { signal: view.signal }),
    api(`/api/chats/${chat.id}/turn`, { signal: view.signal }),
    api(`/api/chats/${chat.id}/plan`, { signal: view.signal }),
    api(`/api/chats/${chat.id}/changes`, { signal: view.signal }),
  ]);
  if(!loaded)return;
  const [messages,turn,plan,changes]=loaded;
  if (view.version !== state.viewVersion) return;
  renderChatMessages(messages);
  renderPlan(plan.tracking_steps || plan.steps, plan);
  renderChangeInspector(changes);
  setRunning(turn.active);
  await loadCodingSession(chat.id, view.version);
  if (view.version !== state.viewVersion || chat.id !== state.chat?.id) return;
  state.streamBuffers.clear();
  if (turn.turn?.status === "failed")
    status(`Ошибка: ${turn.turn.summary || "ответ прерван"}`);
  else status(turn.active ? "Агент работает…" : "Готово");
  await showRequests();
  if (turn.active) attachStream(chat.id);
}
async function syncChat() {
  const chat = state.chat,
    version = state.viewVersion;
  if (!chat || state.page !== "chat") return;
  const [messages, turn, plan, changes] = await Promise.all([
    api(`/api/chats/${chat.id}/messages`),
    api(`/api/chats/${chat.id}/turn`),
    api(`/api/chats/${chat.id}/plan`),
    api(`/api/chats/${chat.id}/changes`),
  ]);
  if (version !== state.viewVersion || chat.id !== state.chat?.id) return;
  renderChatMessages(messages);
  renderPlan(plan.tracking_steps || plan.steps, plan);
  renderChangeInspector(changes);
  setRunning(turn.active);
  await loadCodingSession(chat.id, version);
  if (typeof refreshLocalQueue === 'function') await refreshLocalQueue();
}
function scheduleChatSync(delay = 180) {
  clearTimeout(state.syncTimer);
  state.syncTimer = setTimeout(guarded(syncChat), delay);
}
async function streamResponse(response, chatId, controller) {
  const reader = response.body.getReader(),
    decoder = new TextDecoder();
  let buffer = "";
  try {
    while (!controller.signal.aborted) {
      const packet = await reader.read();
      if (packet.done) break;
      buffer += decoder.decode(packet.value, { stream: true });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop();
      for (const block of blocks) {
        const dataLine = block
          .split(/\r?\n/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (!dataLine) continue;
        const data = JSON.parse(dataLine),
          kind = (block.match(/^event:\s*(.*)$/m) || [])[1];
        if (
          window.koddiProductScope &&
          window.koddiProductScope !== "development"
        )
          window.koddiCloudCursor =
            (block.match(/^id:\s*(.*)$/m) || [])[1] || window.koddiCloudCursor;
        if (state.chat?.id !== chatId || state.page !== "chat") continue;
        if (typeof handleLocalJournal === "function")
          handleLocalJournal(kind, data);
        if (typeof handleCloudProgress === "function")
          handleCloudProgress(kind, data);
        if (kind === "stream.reset") {
          await syncChat();
          state.streamBuffers.clear();
          if (data.live_message)
            state.streamBuffers.set(String(data.live_message.id), {
              content: data.live_message.content,
              node: null,
            });
          controller.abort();
          setTimeout(() => {
            if (state.chat?.id === chatId && state.running)
              attachStream(chatId, data.last_sequence || data.last_event_id);
          }, 100);
          continue;
        }
        if (kind === "message.delta") queueStreamDelta(chatId, data);
        if (kind === "message.reset") resetStreamMessage(data);
        if (kind === "agent.usage") {
          state.latestUsage = {usage:data.usage || {}, dokky:data.dokky || {}};
          if (state.codingSession) state.codingSession.last_usage = state.latestUsage;
          if (typeof applyBillingLimits === "function") applyBillingLimits(data.dokky?.billing);
          if (typeof scheduleCodingKeepalive === 'function') scheduleCodingKeepalive();
        }
        if (kind === "turn.started" && data.chat_title) {
          state.chat.title = data.chat_title;
          $("#title").textContent = data.chat_title;
        }
        if (kind === "agent.waiting")
          status(`Докки обрабатывает запрос · ${data.seconds || 1} с`);
        if (kind === "agent.step") status("Агент выполняет задачу…");
        if (kind === "search.started") status("Докки ищет информацию…");
        if (kind === "search.source")
          status(
            `Найден источник: ${(data.source || data).title || (data.source || data).url || "источник"}`,
          );
        if (kind === "activity.updated") {
          const activity = data.activity || {};
          if (activity.kind === "title" && activity.text) {
            state.chat.title = activity.text;
            $("#title").textContent = activity.text;
          } else if (activity.text) status(activity.text);
        }
        if (kind === "auth.revoked") window.koddiAuthRevoked?.();
        if (kind === "agent.status") status(data.text || "Агент работает…");
        if (kind === "agent.warning")
          notify(data.error || "Предупреждение агента", true);
        if (kind === "tool.waiting")
          status(
            `${actionText({ event: "tool.call", data })} · ${data.seconds} с`,
          );
        if (kind === "tool.progress") {
          if (typeof handleLocalJournal !== "function") pendingTool(data);
          status(
            data.name
              ? `Подготавливаю действие: ${(data.display_name || data.name).replaceAll("__", ".")}`
              : "Подготавливаю действие…",
          );
        }
        if (kind === "tool.call" || kind === "tool.result") {
          document.querySelectorAll(".pending-tool").forEach((node) => {
            if (node.dataset.callId === data.id) node.remove();
          });
          scheduleChatSync();
        }
        if (kind === "plan.updated") renderPlan(data.tracking_steps || data.steps, {...data, turn_id:data.turn_id ?? localJournal.turnId});
        if (kind === "agent.input_required") await showRequests();
        if (kind === "context.compacting" || kind === "context.compacted")
          status(data.text || "Сжимаю контекст…");
        if (kind === "turn.failed")
          notify(
            `Агент остановился: ${data.error?.message || data.error || "Неизвестная ошибка"}`,
            true,
          );
        if (
          ["turn.completed", "turn.failed", "turn.cancelled"].includes(kind)
        ) {
          document
            .querySelectorAll(".pending-tool")
            .forEach((node) => node.remove());
          state.streamBuffers.clear();
          await syncChat();
          setRunning(false);
          if (kind === "turn.completed") status("Готово");
          if (kind === "turn.cancelled") status("Выполнение остановлено");
          await refresh({ includeModels: false });
        }
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
async function attachStream(chatId, after = 0) {
  state.streamAbort?.abort();
  const controller = new AbortController();
  state.streamAbort = controller;
  try {
    const response = await fetch(
      `/api/chats/${chatId}/turn/events?after=${after}`,
      { signal: controller.signal },
    );
    if (!response.ok) return syncChat();
    await streamResponse(response, chatId, controller);
  } catch (error) {
    if (error.name !== "AbortError")
      notify(`Поток прервался: ${error.message}`, true);
  } finally {
    if (state.streamAbort === controller) {
      state.streamAbort = null;
      scheduleChatSync(0);
    }
  }
}
async function send() {
  if(state.chatLoadState!=='ready'||state.reasoningBusy)return;
  if (!attachmentsReady()) return;
  const content = $("#prompt").value.trim();
  if (!content) return;
  if (state.running) return enqueueLocalDraft();
  if (!$("#model").value) {
    notify("Войдите с помощью Докки");
    return page("llm");
  }
  let mode = $("#mode").value;
  if (["development", "debug"].includes(mode) && !state.project) {
    const choice = await inputDialog(
      "Выберите рабочую папку",
      [
        {
          name: "project_id",
          label: "Проект",
          type: "select",
          options: [
            { value: "", label: "Обычный чат без файлов" },
            ...state.projects.map((p) => ({ value: p.id, label: p.name })),
          ],
        },
      ],
      "Продолжить",
    );
    if (!choice) return;
    state.project =
      state.projects.find((p) => p.id === Number(choice.project_id)) || null;
    if (!state.project) {
      mode = "chat";
      $("#mode").value = mode;
    }
  }
  if (!state.chat)
    state.chat = await post("/api/chats", {
      title: "Новый чат",
      project_id: state.project?.id || null,
      mode,
      model: $("#model").value,
    });
  const chatId = state.chat.id;
  $("#prompt").value = "";
  autoSize();
  $(".empty")?.remove();
  state.followFeed = true;
  const attachmentIds = state.attachments.map(a => a.id);
  const optimistic = createMessage({
    id: "pending-user",
    role: "user",
    content,
    metadata: {},
  });
  optimistic.classList.add("is-new");
  $("#feed").append(optimistic);
  follow();
  state.attachments = [];
  renderAttachments();
  setRunning(true);
  updateEnvironment();
  updateSegment();
  const controller = new AbortController();
  state.streamAbort?.abort();
  state.streamAbort = controller;
  state.streamBuffers.clear();
  try {
    const response = await fetch(`/api/chats/${chatId}/messages`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        content,
        attachment_ids: attachmentIds,
        mode,
        model: $("#model").value,
        options: {},
      }),
      signal: controller.signal,
    });
    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.detail || "Не удалось отправить сообщение");
    }
    await streamResponse(response, chatId, controller);
  } catch (error) {
    if (error.name !== "AbortError") {
      notify(error.message, true);
      $("#prompt").value = content;
    }
  } finally {
    if (state.streamAbort === controller) {
      state.streamAbort = null;
      await syncChat();
    }
  }
}
async function stopTurn() {
  if (!state.chat || !state.running || localQueue.stopping) return;
  const chat = state.chat.id;
  localQueue.stopping = true;
  $('#send').disabled = true;
  $('#send').title = 'Останавливаю…';
  if (typeof handleLocalJournal === 'function') handleLocalJournal('agent.status', {text:'Останавливаю…'});
  try {
    await post(`/api/chats/${chat}/turn/cancel`);
    if (state.chat?.id !== chat) return;
    state.streamAbort?.abort();
    state.streamAbort = null;
    state.streamBuffers.clear();
    setRunning(false);
    await syncChat();
  } finally {
    localQueue.stopping = false;
    $('#send').disabled = false;
  }
}
function updateSegment() {
  document.querySelectorAll("[data-chat-mode]").forEach((node) => {
    const active =
      node.dataset.chatMode ===
      ($("#mode").value === "chat" ? "chat" : "development");
    node.classList.toggle("active", active);
    node.setAttribute("aria-pressed", String(active));
  });
}
async function showRequests() {
  const root = $("#request-panel");
  if (!root || !state.chat) return;
  const requests = await api("/api/agent/requests");
  root.replaceChildren();
  root.hidden = false;
  for (const request of requests.filter(
    (r) => r.chat_id === undefined || r.chat_id === state.chat.id,
  )) {
    const node = document.createElement("section");
    node.className = "approval-panel";
    const data = request.payload;
    node.innerHTML = `<b>${request.kind === "approval" ? "Требуется разрешение" : "Уточнение от агента"}</b><p>${escapeHtml(data.question || `${data.name}: ${JSON.stringify(data.arguments || {})}`)}</p>${request.kind === "approval" ? `<div class="actions">${button("Разрешить", "check", "primary")}${button("Отклонить", "close")}</div>` : '<form><input name="answer" required placeholder="Ваш ответ">' + button("Ответить", "send", "primary", 'type="submit"') + "</form>"}`;
    const respond = async (payload) => {
      await post(`/api/agent/requests/${request.id}/respond`, payload);
      node.remove();
      if (!root.children.length) root.hidden = true;
      status("Продолжаю выполнение…");
    };
    if (request.kind === "approval") {
      const buttons = node.querySelectorAll("button");
      buttons[0].onclick = guarded(() => respond({ approved: true }));
      buttons[1].onclick = guarded(() => respond({ approved: false }));
    } else {
      node.querySelector("button").type = "submit";
      node.querySelector("form").onsubmit = (event) => {
        event.preventDefault();
        guarded(respond)({ answer: node.querySelector("input").value });
      };
    }
    root.append(node);
  }
}
function renderPlan(steps = [], snapshot = {}) {
  const previous = state.planSnapshot;
  if (previous && previous.turn_id === snapshot.turn_id)
    snapshot = {...previous, ...snapshot, document:snapshot.document ?? previous.document};
  state.planSnapshot = snapshot;
  steps = Array.isArray(steps) ? steps : [];
  if (typeof renderPlanApproval === "function") renderPlanApproval(snapshot);
  const root = $("#task-plan");
  if (!root) return;
  root.innerHTML = steps.length
    ? `<ol class="plan-list">${steps.map((step) => `<li class="${escapeHtml(step.status)}"><i class="plan-dot" aria-hidden="true"></i><span>${escapeHtml(step.title)}</span></li>`).join("")}</ol>`
    : '<p class="muted">План появится, когда агент начнёт задачу.</p>';
  let live = $('#live-plan');
  if (!steps.length || window.koddiProductScope !== 'development') { live?.remove(); return; }
  if (!live) {
    live = document.createElement('section');live.id = 'live-plan';live.className = 'live-plan';
    live.setAttribute('aria-label', 'Ход выполнения плана');
    live.innerHTML = '<div class="live-plan-heading" role="status" aria-live="polite"></div><div class="live-plan-steps"></div>';
    $('#feed').append(live);
  }
  const completed = steps.filter(step => step.status === 'completed').length;
  $('.live-plan-heading', live).textContent = `План · ${completed} из ${steps.length}`;
  const signature = JSON.stringify(steps);
  if (live._steps !== signature) {
    $('.live-plan-steps', live).innerHTML = root.innerHTML;
    const labels = {pending:'Ожидает', running:'Выполняется', completed:'Готово', blocked:'Заблокирован'};
    $('.live-plan-steps', live).querySelectorAll('li').forEach((node, index) => {
      node.setAttribute('aria-label', `${labels[steps[index].status] || labels.pending}: ${steps[index].title}`);
      if (steps[index].status === 'running') node.setAttribute('aria-current', 'step');
    });
    live._steps = signature;
  }
  // план стоит сразу после журнала хода и не прыгает за новыми ответами
  const turnNodes = [...$('#feed').children].filter(node => {
    const item = state.messageSources.get(node.dataset.messageId);
    return String(node.dataset.turnId || item?.metadata?.turn_id || '') === String(snapshot.turn_id);
  });
  const anchor = $('#plan-approval') || turnNodes.find(node => node.classList.contains('local-work')) || turnNodes.at(-1);
  if (anchor && anchor !== live && anchor.nextElementSibling !== live) anchor.after(live);
  follow();
}
function diffCounts(diff) {
  return diffStats(parseUnifiedDiff(diff));
}
function renderChangeInspector(changes = []) {
  state.changes = Array.isArray(changes) ? changes : changes ? [changes] : [];
  $("#turn").hidden = state.inspector!=='diff'||!state.changes.length;
  $("#change-files").replaceChildren();
  const ids=new Set(state.changes.map(change=>change.id));
  for(const id of changeDiffCache.keys())if(!ids.has(id))changeDiffCache.delete(id);
  if (!state.changes.length) {
    $('#diff').replaceChildren();
    state.changeSelection=null;
    delete $('#rollback').dataset.id;
    $("#change-summary").textContent = "Изменений пока нет";
    return;
  }
  const entries=state.changes.flatMap(change=>{
    const files=changeDiffModel(change);
    if(!files.length)files.push({path:change.description,hunks:[],added:0,removed:0});
    return files.map(file=>({change,file,key:`${change.id}:${file.path}`,fileCount:files.length}));
  });
  const totals=diffStats(entries.map(entry=>entry.file));
  $("#change-summary").innerHTML =
    `<span>Изменения чата</span><span class="positive">+${totals.add}</span><span class="danger">−${totals.remove}</span>`;
  const select=entry=>{
    state.changeSelection=entry.key;
    $('#change-files').querySelectorAll('button').forEach(node=>{const active=node.dataset.changeKey===entry.key;node.classList.toggle('active',active);node.setAttribute('aria-pressed',String(active));});
    mountDiff($('#diff'),entry.file,entry.key);
    const rollback=$('#rollback');rollback.dataset.id=entry.change.id;rollback.dataset.fileCount=entry.fileCount;
    rollback.disabled=entry.change.status==='rolled_back';
    rollback.title=entry.fileCount>1?`Откатить весь набор (файлов: ${entry.fileCount})`:'Откатить изменение файла';
  };
  for (const entry of entries) {
    const {change,file}=entry;
    const node = document.createElement("button");
    node.className = "change-file";
    node.type='button';node.dataset.changeKey=entry.key;node.title=file.path;
    node.innerHTML =
      icon("file", 15) +
      `<span>${escapeHtml(file.path)}</span><small class="positive">+${file.added}</small><small class="danger">−${file.removed}</small>`;
    node.onclick = () => select(entry);
    $("#change-files").append(node);
  }
  select(entries.find(entry=>entry.key===state.changeSelection)||entries[0]);
}
async function selectInspector(key) {
  state.inspector = key;
  setPanel("right", false, false);
  $("#inspector-diff").hidden = key !== "diff";
  $("#inspector-files").hidden = key !== "files";
  document.querySelectorAll("[data-inspector]").forEach((node) => {
    node.classList.toggle("active", node.dataset.inspector === key);
    node.setAttribute("aria-selected", String(node.dataset.inspector === key));
    node.tabIndex = node.dataset.inspector === key ? 0 : -1;
  });
  $("#turn").hidden = key !== "diff" || !state.changes.length;
  $("#file-tree").hidden = key !== "files";
  $("#editor-view").hidden = key !== "files" || !state.editor;
  $("#task-plan").hidden = key !== "plan";
  if (key === "files") await loadFiles();
}
async function loadFiles() {
  const root = $("#file-tree");
  const projectId=state.project?.id,version=state.viewVersion,request=++state.fileListRequest;
  if (!state.project) {
    empty(root, "Проект не выбран");
    return;
  }
  root.innerHTML = '<div class="skeleton file-skeleton"></div>';
  let result;
  try {result=await api(`/api/projects/${projectId}/files`,{signal:state.viewAbort?.signal});}
  catch(error) {
    if(error.name==='AbortError'||state.project?.id!==projectId||state.viewVersion!==version||request!==state.fileListRequest)return;
    empty(root,'Не удалось загрузить файлы',error.message);
    root.insertAdjacentHTML('beforeend',button('Повторить','refresh'));
    $('button',root).onclick=guarded(loadFiles);
    return;
  }
  if(state.project?.id!==projectId||state.viewVersion!==version||request!==state.fileListRequest)return;
  const entries = result.items || result.entries || result.files || [];
  root.replaceChildren();
  for (const entry of entries) {
    const path = typeof entry === "string" ? entry : entry.path;
    if (!path || entry.is_dir || ["directory", "dir"].includes(entry.type))
      continue;
    const node = document.createElement("button");
    node.className = "file-row";
    node.innerHTML = icon("file", 15) + `<span>${escapeHtml(path)}</span>`;
    node.title = path;
    node.onclick = guarded(() => openFile(path));
    root.append(node);
  }
  if (!root.children.length) empty(root, "Папка пуста");
}
function clearWorkspaceEditor() {
  stashEditorDraft();
  ++state.editorRequest;
  state.editor=null;
  const root=$('#editor-view');
  if(root){root.replaceChildren();root.hidden=true;}
}
function stashEditorDraft() {
  const editor=state.editor,area=$('#editor-view textarea');
  if(editor&&area&&!area.readOnly) {
    const key = `${editor.projectId}:${editor.path}`;
    if(area.value!==editor.original) state.editorDrafts.set(key,{content:area.value,hash:editor.hash});
    else state.editorDrafts.delete(key);
  }
}
async function openFile(path) {
  if (!state.project) return;
  stashEditorDraft();
  const projectId=state.project.id,version=state.viewVersion,request=++state.editorRequest;
  let value;
  try {value = await api(
    `/api/projects/${projectId}/file?path=${encodeURIComponent(path)}`,{signal:state.viewAbort?.signal},
  );} catch(error) {
    if(error.name==='AbortError'||state.project?.id!==projectId||version!==state.viewVersion||request!==state.editorRequest)return;
    throw error;
  }
  if(state.project?.id!==projectId||version!==state.viewVersion||request!==state.editorRequest)return;
  const key=`${projectId}:${path}`,draft=state.editorDrafts.get(key);
  let content=value.content;
  if(draft&&!value.read_only) {
    const restore=draft.hash===value.hash||await confirmDialog('Восстановить несохранённый текст?','Файл на диске изменился после создания черновика. Если восстановить черновик и затем сохранить, новая версия файла будет заменена.','Восстановить');
    if(restore)content=draft.content;
  }
  if(state.project?.id!==projectId||version!==state.viewVersion||request!==state.editorRequest)return;
  stashEditorDraft();
  const editor={projectId,path,hash:value.hash,original:value.content};state.editor=editor;
  await selectInspector("files");
  if(state.project?.id!==projectId||version!==state.viewVersion||state.editor!==editor)return;
  const root = $("#editor-view");
  root.hidden = state.inspector!=='files';
  root.innerHTML = `<div class="editor-header"><span>${escapeHtml(path)}</span>${iconButton("Сохранить файл", "save")}</div><textarea class="file-editor" spellcheck="false" aria-label="Содержимое файла"></textarea>`;
  root.querySelector("textarea").value = content;
  root.querySelector("textarea").readOnly = Boolean(value.read_only);
  root.querySelector("button").disabled = Boolean(value.read_only);
  const area=root.querySelector('textarea'),save=root.querySelector('button');
  area.addEventListener('input', stashEditorDraft);
  if (/\.(md|markdown|mdown|mkd)$/i.test(path)) {
    const modes = document.createElement('div');modes.className = 'editor-modes';
    modes.setAttribute('role', 'group');modes.setAttribute('aria-label', 'Вид Markdown');
    modes.innerHTML = iconButton('Код', 'code', 'aria-pressed="true"') + iconButton('Предпросмотр', 'Eye', 'aria-pressed="false"');
    const preview = document.createElement('div');preview.className = 'file-preview message-body';preview.hidden = true;
    preview.tabIndex = 0;preview.setAttribute('aria-label', 'Предпросмотр Markdown');
    root.append(preview);$('.editor-header',root).insertBefore(modes,save);
    const buttons = modes.querySelectorAll('button');
    const showPreview = active => {
      stashEditorDraft();
      if (active) renderMarkdown(preview, area.value);
      area.hidden = active;preview.hidden = !active;
      buttons[0].setAttribute('aria-pressed', String(!active));buttons[1].setAttribute('aria-pressed', String(active));
    };
    buttons[0].onclick = () => showPreview(false);buttons[1].onclick = () => showPreview(true);
  }
  save.onclick = guarded(async () => {
    const current=()=>state.project?.id===projectId&&state.viewVersion===version&&state.editor===editor&&area.isConnected;
    if(!current()||save.disabled)return;
    save.disabled=true;
    const savedContent=area.value,savedHash=editor.hash;
    try {
    const result = await api(`/api/projects/${projectId}/file`, {
      method: "PUT",
      body: JSON.stringify({
        path,
        content: savedContent,
        hash: editor.hash,
      }),
    });
    if(current()){
      editor.hash=result.hash;editor.original=savedContent;
      if(area.value===savedContent)state.editorDrafts.delete(key);
      else state.editorDrafts.set(key,{content:area.value,hash:result.hash});
      notify("Файл сохранён");
    } else {
      const draft=state.editorDrafts.get(key);
      if(draft?.hash===savedHash) {
        if(draft.content===savedContent)state.editorDrafts.delete(key);
        else draft.hash=result.hash;
      }
    }
    } catch(error){if(current())throw error;}
    finally {if(current())save.disabled=Boolean(value.read_only);}
  });
}
async function upload(files) {
  const jobs = Array.from(files, file => ({
    file, key: crypto.randomUUID(), controller: null, error: null,
    path: window.koddiProductScope && window.koddiProductScope !== "development"
      ? "/api/dokky/cloud/files" : "/api/attachments",
  }));
  for (const job of jobs) state.uploads.set(job.key, job);
  renderAttachments();
  // файлы грузим по одному, чтобы большие вложения не раздували память
  for (const job of jobs) {
    if (state.uploads.get(job.key) === job) await runUpload(job);
  }
}
async function runUpload(job) {
  if (job.controller) return;
  const controller = new AbortController();
  job.controller = controller;
  job.error = null;
  renderAttachments();
  try {
    const form = new FormData();
    form.append("file", job.file);
    const saved = await api(job.path, {
      method: "POST", body: form, signal: controller.signal,
      headers: { "Idempotency-Key": job.key },
    });
    if (state.uploads.get(job.key) !== job || controller.signal.aborted) return;
    state.uploads.delete(job.key);
    state.attachments.push(saved);
  } catch (error) {
    if (state.uploads.get(job.key) !== job || controller.signal.aborted) return;
    job.error = error.message;
  } finally {
    job.controller = null;
    renderAttachments();
  }
}
function clearAttachments() {
  for (const job of state.uploads.values()) job.controller?.abort();
  state.uploads.clear();
  state.attachments = [];
  renderAttachments();
}
function attachmentsReady() {
  if (!state.uploads.size) return true;
  notify(Array.from(state.uploads.values()).some(job => job.error)
    ? "Повторите загрузку вложения или уберите его перед отправкой"
    : "Дождитесь загрузки вложений перед отправкой");
  return false;
}
function renderAttachments() {
  const root = $("#attachments");
  root.replaceChildren();
  for (const item of state.attachments) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.innerHTML =
      icon("file", 15) +
      `<span>${escapeHtml(item.original_name || item.name || "Вложение")}</span>` +
      iconButton("Убрать вложение", "close");
    chip.querySelector("button").onclick = () => {
      state.attachments = state.attachments.filter((a) => a.id !== item.id);
      renderAttachments();
    };
    root.append(chip);
  }
  for (const job of state.uploads.values()) {
    const chip = document.createElement("span");
    chip.className = `chip upload-chip${job.error ? " upload-error" : ""}`;
    chip.title = job.error || "Загрузка вложения";
    chip.setAttribute("role", "status");
    chip.innerHTML = icon("file", 15) +
      `<span>${escapeHtml(job.file.name)}</span><small>${job.error ? "Ошибка загрузки" : "Загрузка…"}</small>` +
      (job.error ? iconButton("Повторить загрузку", "refresh") : "") +
      iconButton("Отменить загрузку", "close");
    chip.querySelector('[aria-label="Повторить загрузку"]')?.addEventListener("click", () => runUpload(job));
    chip.querySelector('[aria-label="Отменить загрузку"]').onclick = () => {
      state.uploads.delete(job.key);
      job.controller?.abort();
      renderAttachments();
    };
    root.append(chip);
  }
  autoSize();
}
function autoSize() {
  const node = $("#prompt");
  node.style.height = "auto";
  node.style.height = `${Math.min(180, Math.max(26, node.scrollHeight))}px`;
  $("#send").classList.toggle("ready", Boolean(node.value.trim()));
  if (!node.value) node.scrollTop = 0;
  $("#composer").classList.toggle(
    "expanded",
    node.scrollHeight > 30 || state.attachments.length > 0 || state.uploads.size > 0,
  );
}
function inputDialog(title, fields, accept = "Сохранить") {
  const dialog = $("#modal");
  if (dialog.open) dialog.close();
  $("#modal-title").textContent = title;
  $("#modal-text").replaceChildren();
  const form = document.createElement("form");
  form.id = "dialog-form";
  form.noValidate = true;
  for (const field of fields) {
    const label = document.createElement("label");
    label.append(document.createTextNode(field.label));
    let input;
    if (field.type === "select") {
      input = document.createElement("select");
      for (const option of field.options) {
        const node = document.createElement("option");
        node.value = option.value;
        node.textContent = option.label;
        input.append(node);
      }
    } else if (field.type === "textarea")
      input = document.createElement("textarea");
    else {
      input = document.createElement("input");
      input.type = field.type || "text";
    }
    input.name = field.name;
    input.value = field.value ?? "";
    input.placeholder = field.placeholder || "";
    input.required = Boolean(field.required);
    label.append(input);
    form.append(label);
  }
  $("#modal-text").append(form);
  $("#modal-ok").textContent = accept;
  return new Promise((resolve) => {
    let done = false;
    const finish = (value) => {
      if (done) return;
      done = true;
      dialog.close();
      resolve(value);
    };
    $("#modal-ok").onclick = () => {
      $('.form-error',form)?.remove();
      if (form.checkValidity()) return finish(Object.fromEntries(new FormData(form)));
      const invalid=form.querySelector(':invalid');
      const error=document.createElement('p');error.className='form-error error-detail';error.role='alert';
      error.textContent=`Проверьте поле «${invalid?.closest('label')?.firstChild?.textContent||'Значение'}».`;
      form.append(error);invalid?.setAttribute('aria-invalid','true');
      (invalid?.nextElementSibling?.matches('.ui-select-trigger')?invalid.nextElementSibling:invalid)?.focus();
    };
    $("#modal-cancel").onclick = () => finish(null);
    dialog.oncancel = () => finish(null);
    form.onsubmit = (event) => {
      event.preventDefault();
      $("#modal-ok").click();
    };
    dialog.showModal();
    form.querySelector("input,textarea,select")?.focus();
  });
}
async function confirmDialog(title, text, accept = "Подтвердить") {
  const pending = inputDialog(title, [], accept);
  const detail = document.createElement("p");
  detail.textContent = text;
  $("#dialog-form").append(detail);
  return (await pending) !== null;
}
function chatApiPath(id) {
  return window.koddiProductScope && window.koddiProductScope !== "development"
    ? `/api/dokky/cloud/chats/${encodeURIComponent(id)}`
    : `/api/chats/${id}`;
}
function chatMenu(chat, event) {
  const menu = $("#chat-menu");
  const rect = event.currentTarget.getBoundingClientRect();
  SurfaceUI.open(menu,event.currentTarget,{x:event.clientX||rect.left,y:event.clientY||rect.bottom});
  menu.onkeydown=event=>SurfaceUI.menuKeys(event,menu);
  menu.querySelector('button').focus({preventScroll:true});
  menu.querySelector('[data-action="rename"]').onclick = guarded(async () => {
    SurfaceUI.close(menu);
    const value = await inputDialog("Переименовать чат", [
      { name: "title", label: "Название", value: chat.title, required: true },
    ]);
    if (!value) return;
    const updated = await api(chatApiPath(chat.id), {
      method: "PATCH",
      body: JSON.stringify({
        ...value,
        ...(window.koddiProductScope !== "development" && chat.version
          ? { expected_version: chat.version }
          : {}),
      }),
    });
    if (state.chat?.id === chat.id) {
      state.chat = updated;
      $("#title").textContent = updated.title;
    }
    await refresh({ includeModels: false });
  });
  menu.querySelector('[data-action="delete"]').onclick = guarded(async () => {
    SurfaceUI.close(menu);
    if (!(await confirmDialog("Удалить чат?", chat.title, "Удалить"))) return;
    await api(
      chatApiPath(chat.id) +
        (window.koddiProductScope && window.koddiProductScope !== "development"
          ? "?confirm=true"
          : ""),
      { method: "DELETE" },
    );
    if (state.chat?.id === chat.id) newChat();
    await refresh({ includeModels: false });
  });
}
async function editMessage(item) {
  if (state.running) return notify("Сначала остановите выполнение");
  const chatId=state.chat?.id,version=state.viewVersion,scope=window.koddiProductScope;
  const value = await inputDialog("Изменить сообщение", [
    {
      name: "content",
      label: "Сообщение",
      type: "textarea",
      value: item.content,
      required: true,
    },
  ]);
  if (!value) return;
  if(state.chat?.id!==chatId||state.viewVersion!==version||window.koddiProductScope!==scope)
    return notify("Чат изменился. Откройте нужное сообщение заново");
  await api(`${chatApiPath(chatId)}/messages/${item.id}`, {
    method:
      window.koddiProductScope && window.koddiProductScope !== "development"
        ? "PATCH"
        : "PUT",
    body: JSON.stringify({
      ...value,
      ...(window.koddiProductScope !== "development" && item.version
        ? { expected_version: item.version }
        : {}),
    }),
  });
  if(state.chat?.id===chatId&&state.viewVersion===version)await syncChat();
}

const names = {
  llm: "Аккаунт Докки",
  projects: "Проекты",
  mission: "Задачи",
  automations: "Автоматизации",
  skills: "Навыки",
  sources: "Источники",
  logs: "Журнал",
  review: "Изменения",
  settings: "Настройки",
};
function pageRoot(title, subtitle = "", commands = "") {
  const root = document.createElement("section");
  root.className = "page-content";
  root.innerHTML = `<div class="page-heading"><div><h1>${escapeHtml(title)}</h1>${subtitle ? `<p class="muted">${escapeHtml(subtitle)}</p>` : ""}</div><div class="actions">${commands}</div></div><div class="page-body"></div>`;
  $("#feed").append(root);
  return root;
}
async function page(key) {
  if (key === "llm" && typeof openSettings === "function") {
    productUI.section = "account";
    return openSettings();
  }
  if (key === "settings" && typeof openSettings === "function")
    return openSettings();
  const view = switchView(key);
  $("#title").textContent = names[key] || "";
  skeleton();
  try {
    const renders = {
      llm: llmPage,
      projects: projectPage,
      mission: tasksPage,
      logs: logsPage,
      review: reviewPage,
      settings: settingsPage,
      skills: skillsPage,
      automations: automationsPage,
      sources: sourcesPage,
    };
    await (renders[key] || projectPage)(view);
  } catch (error) {
    if (error.name !== "AbortError" && view.version === state.viewVersion) {
      clearFeed();
      const root = pageRoot(names[key]);
      $(".page-body", root).innerHTML =
        `<div class="error-state"><h3>Не удалось загрузить</h3><p>${escapeHtml(error.message)}</p>${button("Повторить", "refresh")}</div>`;
      $("button", root).onclick = guarded(() => page(key));
    }
  }
}
async function llmPage(view) {
  return dokkyAccountPage(view);
}
async function changeChatModel(model) {
  if (window.koddiProductScope && window.koddiProductScope !== "development") {
    if (state.chat)
      state.chat = await api(chatApiPath(state.chat.id), {
        method: "PATCH",
        body: JSON.stringify({ model, expected_version: state.chat.version }),
      });
    dokkyState.selected[dokkyState.scope] = model;
    renderModels();
    return;
  }
  await post("/api/models/select", { model });
  state.selectedModel = model;
  if (state.chat && state.page === "chat")
    state.chat = await api(chatApiPath(state.chat.id), {
      method: "PATCH",
      body: JSON.stringify({ model }),
    });
  renderModels();
  updateEnvironment();
  notify("Модель выбрана");
}
async function projectPage(view) {
  const projects = await api("/api/projects", { signal: view.signal });
  if (view.version !== state.viewVersion) return;
  clearFeed();
  const root = pageRoot(
    "Проекты",
    `${projects.length} ${projects.length === 1 ? "проект" : "проектов"}`,
    button("Открыть папку", "folder") +
      button("Новый проект", "plus", "primary"),
  );
  const commands = root.querySelectorAll(".page-heading button");
  commands[0].onclick = guarded(openFolder);
  commands[1].onclick = guarded(createProject);
  const body = $(".page-body", root);
  if (!projects.length) return empty(body, "Проектов пока нет");
  for (const project of projects) {
    const row = document.createElement("div");
    row.className = "project-row";
    row.innerHTML = `<span class="project-symbol">${icon("folder", 22)}</span><div><b>${escapeHtml(project.name)}</b><p class="muted">${escapeHtml(project.path)}</p></div><div class="actions">${button("Открыть", "folder")}${iconButton("Убрать из списка", "trash")}</div>`;
    const buttons = row.querySelectorAll("button");
    buttons[0].onclick = () => newChat(project);
    buttons[1].onclick = guarded(async () => {
      if (
        !(await confirmDialog(
          "Убрать проект?",
          "Файлы останутся на диске",
          "Убрать",
        ))
      )
        return;
      await api(`/api/projects/${project.id}`, { method: "DELETE" });
      await refresh({ includeModels: false });
      await page("projects");
    });
    body.append(row);
  }
}
const statusNames = {
  queued: "В очереди",
  running: "Выполняется",
  completed: "Завершено",
  failed: "Ошибка",
  cancelled: "Остановлено",
  pending: "Запланировано",
  blocked: "Заблокировано",
  approval_required: "Нужно разрешение",
};
async function tasksPage(view) {
  const tasks = await api("/api/tasks", { signal: view.signal });
  if (view.version !== state.viewVersion) return;
  clearFeed();
  const root = pageRoot("Задачи");
  const body = $(".page-body", root);
  if (!tasks.length) return empty(body, "Задач пока нет");
  body.innerHTML = `<div class="data-table"><div class="table-head"><span>Задача</span><span>Режим</span><span>Состояние</span></div>${tasks.map((task) => `<button class="table-row" data-task="${task.id}"><span>${escapeHtml(task.title)}</span><span>${escapeHtml(state.modes.find((m) => m.id === task.mode)?.name || task.mode)}</span><span class="badge ${task.status}">${escapeHtml(statusNames[task.status] || task.status)}</span></button>`).join("")}</div>`;
  body.querySelectorAll("[data-task]").forEach(
    (node) =>
      (node.onclick = guarded(async () => {
        const task = tasks.find((t) => t.id === Number(node.dataset.task));
        const chat = state.chats.find((c) => c.id === task.chat_id);
        if (chat) await openChat(chat);
        else notify("Для этой задачи нет отдельного чата");
      })),
  );
}
async function logsPage(view) {
  const rows = await api("/api/logs?limit=150", { signal: view.signal });
  if (view.version !== state.viewVersion) return;
  clearFeed();
  const root = pageRoot(
    "Журнал",
    `${rows.length} записей`,
    iconButton("Экспорт журнала", "download") +
      iconButton("Обновить", "refresh"),
  );
  root.querySelectorAll(".page-heading button")[0].onclick = () =>
    window.open("/api/logs/export", "_blank");
  root.querySelectorAll(".page-heading button")[1].onclick = guarded(() =>
    page("logs"),
  );
  const body = $(".page-body", root);
  body.innerHTML =
    '<label class="search-field">' +
    icon("search") +
    '<input type="search" placeholder="Поиск по журналу" aria-label="Поиск по журналу"></label><div class="log-list"></div>';
  const draw = (query) => {
    const list = $(".log-list", body);
    list.replaceChildren();
    for (const row of rows.filter((r) =>
      JSON.stringify(r).toLowerCase().includes(query.toLowerCase()),
    )) {
      const node = document.createElement("details");
      node.className = "log-row";
      node.innerHTML = `<summary><span class="badge ${row.level?.toLowerCase()}">${escapeHtml(row.level)}</span><span>${escapeHtml(row.event)}</span><time>${escapeHtml(row.created_at)}</time></summary><p>${escapeHtml(row.message)}</p><pre>${escapeHtml(row.data_json || row.metadata_json || "")}</pre>`;
      list.append(node);
    }
    if (!list.children.length) empty(list, "Записей не найдено");
  };
  body.querySelector("input").oninput = (event) => draw(event.target.value);
  draw("");
}
async function reviewPage(view) {
  const changes = await api(
    "/api/changes" + (state.project ? `?project_id=${state.project.id}` : ""),
    { signal: view.signal },
  );
  if (view.version !== state.viewVersion) return;
  clearFeed();
  const root = pageRoot("Изменения", state.project?.name || "Все проекты");
  const body = $(".page-body", root);
  if (!changes.length) return empty(body, "Изменений пока нет");
  for (const change of changes) {
    const node = document.createElement("details");
    node.className = "review-file";
    const counts = diffCounts(change.diff);
    node.innerHTML = `<summary>${icon("file")}<span>${escapeHtml(change.description)}</span><span class="positive">+${counts.add}</span><span class="danger">−${counts.remove}</span></summary><div class="code-diff"></div><div class="actions">${button(change.status === "rolled_back" ? "Откачено" : "Откатить", "undo", "secondary", change.status === "rolled_back" ? "disabled" : "")}</div>`;
    node.ontoggle=()=>{if(node.open)mountDiff($('.code-diff',node),changeDiffModel(change),String(change.id));};
    node.querySelector("button").onclick = guarded(async () => {
      if (
        !(await confirmDialog(
          "Откатить изменения?",
          change.description,
          "Откатить",
        ))
      )
        return;
      const result = await post(`/api/changes/${change.id}/rollback`);
      if (!result.rolled_back)
        throw new Error(result.reason || "Откат не выполнен");
      notify("Изменения отменены");
      await page("review");
    });
    body.append(node);
  }
}
async function settingsPage(view) {
  const settings = await api("/api/settings", { signal: view.signal });
  if (view.version !== state.viewVersion) return;
  clearFeed();
  const root = pageRoot("Настройки");
  const body = $(".page-body", root);
  body.classList.add("settings-form");
  body.innerHTML = `<h2>Интерфейс</h2><label>Тема<select id="theme"><option value="dark">Тёмная</option><option value="light">Светлая</option></select></label><h2>Доступ к проекту</h2><label>Разрешения<select id="permissions"><option value="ask">Вручную</option><option value="trusted">Автоматически</option></select></label><div class="section-heading"><h2>Режимы агента</h2>${iconButton("Создать режим", "plus")}</div><div id="mode-list"></div>`;
  $("#theme").value = settings.theme;
  $("#permissions").value = settings.permission_profile;
  const save = async (payload) => {
    state.settings = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    updateEnvironment();
  };
  $("#theme").onchange = guarded(async (event) => {
    await save({ theme: event.target.value });
    document.documentElement.dataset.theme = event.target.value;
  });
  $("#permissions").onchange = guarded((event) =>
    save({ permission_profile: event.target.value }),
  );
  $(".section-heading button", body).onclick = guarded(() => editMode());
  for (const mode of state.modes) {
    const row = document.createElement("div");
    row.className = "settings-row";
    row.innerHTML = `<div><b>${escapeHtml(mode.name)}</b><p class="muted">${mode.builtin ? "Встроенный режим" : `${mode.tools.length} инструментов`}</p></div>${!mode.builtin ? iconButton("Изменить режим", "edit") + iconButton("Удалить режим", "trash") : ""}`;
    if (!mode.builtin) {
      const buttons = row.querySelectorAll("button");
      buttons[0].onclick = guarded(() => editMode(mode));
      buttons[1].onclick = guarded(async () => {
        await api(`/api/modes/${mode.id}`, { method: "DELETE" });
        await refresh();
        await page("settings");
      });
    }
    $("#mode-list").append(row);
  }
}
async function editMode(mode = {}) {
  const values = await inputDialog(mode.id ? "Изменить режим" : "Новый режим", [
    {
      name: "id",
      label: "Идентификатор",
      value: mode.id || "",
      required: true,
    },
    { name: "name", label: "Название", value: mode.name || "", required: true },
    {
      name: "instructions",
      label: "Инструкции",
      type: "textarea",
      value: mode.instructions || "",
      required: true,
    },
    {
      name: "tools",
      label: "Инструменты через запятую",
      type: "textarea",
      value: (mode.tools || ["file.read", "search.text", "web.search"]).join(
        ", ",
      ),
    },
  ]);
  if (!values) return;
  await api(`/api/modes/${encodeURIComponent(values.id)}`, {
    method: "PUT",
    body: JSON.stringify({
      ...values,
      tools: values.tools
        .split(",")
        .map((v) => v.trim())
        .filter(Boolean),
    }),
  });
  await refresh();
  await page("settings");
}
async function skillsPage(view) {
  const skills = await api("/api/skills", { signal: view.signal });
  if (view.version !== state.viewVersion) return;
  clearFeed();
  const root = pageRoot("Навыки", "", button("Новый навык", "plus", "primary"));
  $(".page-heading button", root).onclick = guarded(() => editSkill());
  const body = $(".page-body", root);
  if (!skills.length) return empty(body, "Навыков пока нет");
  for (const skill of skills) {
    const row = document.createElement("div");
    row.className = "settings-row";
    row.innerHTML = `<div><b>${escapeHtml(skill.name)}</b><p class="muted">${escapeHtml(skill.description)}</p></div>${iconButton("Изменить навык", "edit")}${iconButton("Удалить навык", "trash")}${iconButton('Запустить в проекте','Play')}`;
    const buttons = row.querySelectorAll("button");
    buttons[0].onclick = guarded(() => editSkill(skill));
    buttons[1].onclick = guarded(async () => {
      await api(`/api/skills/${skill.id}`, { method: "DELETE" });
      await page("skills");
    });
    buttons[2].onclick=guarded(async()=>{
      const value=await inputDialog('Запустить навык',[
        {name:'project_id',label:'Проект',type:'select',options:state.projects.map(project=>({value:project.id,label:project.name}))},
        {name:'task',label:'Задание',type:'textarea',value:skill.description||skill.name,required:true},
      ],'Запустить');
      if(!value)return;
      const result=await post(`/api/skills/${skill.id}/execute`,{request_id:crypto.randomUUID(),project_id:Number(value.project_id),task:value.task});
      await refresh({includeModels:false});
      await openChat(result.chat);
    });
    body.append(row);
  }
}
async function editSkill(skill = {}) {
  const value = await inputDialog("Навык", [
    {
      name: "name",
      label: "Название",
      value: skill.name || "",
      required: true,
    },
    { name: "description", label: "Описание", value: skill.description || "" },
    {
      name: "checklist",
      label: "Шаги, по одному на строке",
      type: "textarea",
      value: (skill.workflow?.checklist || [])
        .map((step) => (typeof step === "string" ? step : step.title))
        .join("\n"),
    },
  ]);
  if (!value) return;
  await post("/api/skills", {
    name: value.name,
    description: value.description,
    workflow: {
      mode: "development",
      checklist: value.checklist.split("\n").filter(Boolean),
    },
  });
  await page("skills");
}
async function automationsPage(view) {
  const entries = await api("/api/automations", { signal: view.signal });
  if (view.version !== state.viewVersion) return;
  clearFeed();
  const root = pageRoot(
    "Автоматизации",
    "",
    button("Создать", "plus", "primary"),
  );
  const body = $(".page-body", root);
  $(".page-heading button", root).onclick = guarded(async () => {
    const value = await inputDialog("Автоматизация", [
      { name: "name", label: "Название", required: true },
      {
        name: "tool",
        label: "Инструмент",
        value: "diagnostics.collect",
        required: true,
      },
      {
        name: "arguments",
        label: "Аргументы JSON",
        type: "textarea",
        value: "{}",
      },
      {
        name: "workspace",
        label: "Папка проекта",
        value: state.project?.path || "",
      },
    ]);
    if (!value) return;
    await post("/api/automations", {
      name: value.name,
      schedule: "manual",
      action: {
        tool: value.tool,
        arguments: JSON.parse(value.arguments),
        workspace: value.workspace || null,
      },
    });
    await page("automations");
  });
  if (!entries.length) return empty(body, "Автоматизаций пока нет");
  for (const entry of entries) {
    const row = document.createElement("div");
    row.className = "settings-row";
    row.innerHTML = `<div><b>${escapeHtml(entry.name)}</b><p class="muted">${escapeHtml(entry.action.tool || "")} · ${escapeHtml(statusNames[entry.status] || entry.status)}</p></div>${button("Запустить", "terminal")}${iconButton("Удалить", "trash")}`;
    const buttons = row.querySelectorAll("button");
    buttons[0].onclick = guarded(async () => {
      buttons[0].disabled = true;
      try {
        const result = await post(`/api/automations/${entry.id}/run`);
        notify(
          result.ok
            ? "Автоматизация выполнена"
            : result.result?.reason ||
                result.result?.error ||
                "Автоматизация не выполнена",
          !result.ok,
        );
        await page("automations");
      } finally {
        buttons[0].disabled = false;
      }
    });
    buttons[1].onclick = guarded(async () => {
      await api(`/api/automations/${entry.id}`, { method: "DELETE" });
      await page("automations");
    });
    body.append(row);
  }
}
async function sourcesPage(view) {
  if (view.version !== state.viewVersion) return;
  clearFeed();
  const root = pageRoot("Источники");
  const body = $(".page-body", root);
  body.innerHTML = `<form class="web-search-form"><label class="search-field">${icon("search")}<input name="query" type="search" required placeholder="Поиск в интернете" aria-label="Поиск в интернете"></label>${button("Найти", "search", "primary", 'type="submit"')}</form><div class="search-results"></div>`;
  body.querySelector("button").type = "submit";
  body.querySelector("form").onsubmit = (event) => {
    event.preventDefault();
    guarded(async () => {
      const results = $(".search-results", body);
      results.innerHTML = '<div class="skeleton file-skeleton"></div>';
      const value = await api(
        `/api/web/search?query=${encodeURIComponent(body.querySelector("input").value)}&limit=8`,
      );
      if (view.version !== state.viewVersion) return;
      results.replaceChildren();
      for (const item of value.results || []) {
        const row = document.createElement("article");
        row.className = "source-row";
        row.innerHTML = `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title)} ${icon("link", 14)}</a><p>${escapeHtml(item.snippet)}</p><small>${escapeHtml(item.url)}</small>`;
        results.append(row);
      }
      if (!results.children.length) empty(results, "Источников не найдено");
    })();
  };
}
async function voice() {
  if (!navigator.mediaDevices?.getUserMedia)
    return notify("Голосовой ввод недоступен в этой среде", true);
  if (state.recording) {
    state.recording.recorder.stop();
    return;
  }
  const remoteSpeech =
    typeof dokkyState !== "undefined" && !dokkyState.account?.internal_test;
  if (remoteSpeech && !dokkyState.account?.authenticated) return dokkyLogin();
  const consent = remoteSpeech
    ? {
        consented: localStorage.getItem("dokky-microphone-consent") === "1",
        model_status: "installed",
      }
    : await api("/api/speech/status");
  if (!consent.consented) {
    if (
      !(await confirmDialog(
        "Голосовой ввод",
        "Микрофон используется для записи сообщения. Запись будет отправлена в Докки для распознавания.",
        "Разрешить",
      ))
    )
      return;
    if (remoteSpeech) localStorage.setItem("dokky-microphone-consent", "1");
    else await post("/api/speech/consent", { consented: true });
  }
  if (consent.model_status !== "installed")
    return notify(
      "Модуль распознавания речи не установлен. Запись не начата.",
      true,
    );
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const recorder = new MediaRecorder(stream);
  const chunks = [];
  state.recording = { recorder, stream };
  recorder.ondataavailable = (event) => chunks.push(event.data);
  recorder.onstop = guarded(async () => {
    stream.getTracks().forEach((track) => track.stop());
    state.recording = null;
    $("#voice").innerHTML = icon("mic");
    const blob = new Blob(chunks, { type: recorder.mimeType });
    const form = new FormData();
    form.append(remoteSpeech ? "audio" : "file", blob, "voice.webm");
    status("Распознаю речь…");
    let result;
    if (remoteSpeech)
      result = await api("/api/dokky/cloud/speech/transcriptions", {
        method: "POST",
        body: form,
      });
    else {
      const saved = await api("/api/attachments", {
        method: "POST",
        body: form,
      });
      result = await post("/api/speech/transcribe", {
        path: saved.stored_path,
      });
    }
    if (!result.text) throw new Error(result.error || "Речь не распознана");
    $("#prompt").value += ($("#prompt").value ? " " : "") + result.text;
    autoSize();
    status("Запись распознана");
  });
  recorder.start();
  $("#voice").innerHTML = icon("stop");
  status("Идёт запись…");
}

function bindUI() {
  lucide.createIcons();
  initChatInsets();
  document
    .querySelectorAll("[data-icon]")
    .forEach((node) =>
      node.insertAdjacentHTML("afterbegin", icon(node.dataset.icon)),
    );
  // «Ещё» и «Поиск» без data-page не должны открывать страницу
  document
    .querySelectorAll(".nav[data-page]")
    .forEach(
      (node) =>
        (node.onclick = guarded(() =>
          node.dataset.page === "chat" ? newChat(state.project) : page(node.dataset.page),
        )),
    );
  $("#attach").innerHTML = icon("attach");
  $("#voice").innerHTML = icon("mic");
  $("#attach").onclick = () => $("#files").click();
  $("#files").onchange = guarded((event) => upload(event.target.files));
  $("#voice").onclick = guarded(voice);
  $("#send").onclick = guarded(() => (state.running ? stopTurn() : send()));
  $("#prompt").oninput = autoSize;
  $("#prompt").onkeydown = (event) => {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.isComposing &&
      (state.settings.enter_sends !== false || event.ctrlKey || event.metaKey)
    ) {
      event.preventDefault();
      guarded(send)();
    }
  };
  $("#prompt").addEventListener("paste", (event) => {
    const files = [...event.clipboardData.files];
    if (files.length) {
      event.preventDefault();
      guarded(upload)(files);
    }
  });
  document.addEventListener("dragover", (event) => {
    if (event.dataTransfer.types.includes("Files")) {
      event.preventDefault();
      $("#composer").classList.add("file-drop");
    }
  });
  document.addEventListener("drop", (event) => {
    event.preventDefault();
    $("#composer").classList.remove("file-drop");
    if (event.dataTransfer.files.length)
      guarded(upload)(event.dataTransfer.files);
  });
  document.addEventListener("dragleave", (event) => {
    if (!event.relatedTarget) $("#composer").classList.remove("file-drop");
  });
  $("#model").onchange = guarded((event) =>
    changeChatModel(event.target.value),
  );
  $("#mode").onchange = guarded(async () => {
    updateSegment();
    if (state.chat)
      state.chat = await api(`/api/chats/${state.chat.id}`, {
        method: "PATCH",
        body: JSON.stringify({ mode: $("#mode").value }),
      });
  });
  $("#toggle-left").onclick = () =>
    setPanel("left", !$("#shell").classList.contains("left-collapsed"));
  $("#toggle-right").onclick = () =>
    setPanel("right", !$("#shell").classList.contains("right-collapsed"));
  $("#close-sidebar").onclick = () => setPanel("left", true);
  $("#sidebar-backdrop").onclick = () => setPanel("left", true);
  $("#close-inspector").onclick = () => setPanel("right", true);
  $("#workspace-selector").onclick = guarded(chooseWorkspace);
  document
    .querySelectorAll("[data-inspector]")
    .forEach(
      (node) =>
        (node.onclick = guarded(() => selectInspector(node.dataset.inspector))),
    );
  document.querySelectorAll("#segmented-mode [data-mode]").forEach((node) => {
    node.dataset.chatMode = node.dataset.mode;
    node.onclick = () => {
      $("#mode").value = node.dataset.mode;
      $("#mode").dispatchEvent(new Event("change"));
    };
  });
  $("#rollback").onclick = guarded(async () => {
    const id = $("#rollback").dataset.id;
    if (!id) return;
    if (
      !(await confirmDialog(
        "Откатить изменения?",
        Number($('#rollback').dataset.fileCount)>1?`Будет отменён весь набор изменений. Файлов: ${Number($('#rollback').dataset.fileCount)}. Это затронет не только открытый файл.`:"Изменение выбранного файла будет отменено",
        "Откатить",
      ))
    )
      return;
    const result = await post(`/api/changes/${id}/rollback`);
    if (!result.rolled_back) throw new Error(result.reason);
    await syncChat();
    notify("Изменения отменены");
  });
  const feed = $("#feed");
  feed.addEventListener(
    "wheel",
    (event) => {
      if (event.deltaY < 0) state.followFeed = false;
    },
    { passive: true },
  );
  // колесо над шапкой и полем ввода листает чат, как под прозрачным краем
  for (const edge of [$(".chat-header"), $("#composer")])
    edge.addEventListener(
      "wheel",
      (event) => {
        if (event.target.closest("textarea,select,.composer-actions")) return;
        if (event.deltaY < 0) state.followFeed = false;
        feed.scrollTop += event.deltaY;
      },
      { passive: true },
    );
  feed.addEventListener(
    "scroll",
    () => {
      if (isNearFeedBottom()) state.followFeed = true;
      updateJumpBottom();
    },
    { passive: true },
  );
  $("#jump-bottom").onclick = () => {
    state.followFeed = true;
    feed.scrollTo({ top: feed.scrollHeight, behavior: "smooth" });
  };
  $("#chat-search").onclick = guarded(async () => {
    const result = await inputDialog(
      "Поиск чатов",
      [{ name: "query", label: "Название чата", value: "" }],
      "Найти",
    );
    if (result) renderSidebar(result.query);
  });
  document.addEventListener("click", (event) => {
    if (
      !event.target.closest("#chat-menu") &&
      !event.target.closest(".chat-row .icon-button")
    )
      SurfaceUI.close($("#chat-menu"));
  });
  window.koddiProjectOpened = guarded(async (project) => {
    await refresh({ includeModels: false });
    newChat(project);
  });
  window.koddiProjectOpenFailed = (error) =>
    notify(`Не удалось открыть папку: ${error}`, true);
  setRunning(false);
}
async function start() {
  bindUI();
  newChat();
  document.body.classList.remove("booting");
  await refresh();
  setPanel(
    "left",
    state.settings.left_panel_collapsed || window.innerWidth < 720,
    false,
  );
  setPanel("right", state.settings.right_panel_collapsed ?? true, false);
  newChat(lastProject());
}
const startup = start().catch((error) => {
  document.body.classList.remove("booting");
  notify(`Не удалось подключиться к приложению: ${error.message}`, true);
});

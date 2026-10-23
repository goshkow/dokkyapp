/* облачные запросы идут через Python, токен до JavaScript не доходит */
const dokkyState = {
  scope: "chat",
  account: null,
  chats: [],
  run: null,
  next: null,
  loginTimer: null,
  selected: {},
  pending: null,
  createKey: null,
  preferences: null,
};
const localUI = {
  newChat,
  openChat,
  renderSidebar,
  renderModels,
  send,
  refresh,
  syncChat,
  attachStream,
  updateSegment,
};
const cloudPath = (path) => `/api/dokky/cloud/${path}`;
const cloudMessage = (m) => ({
  ...m,
  metadata: {
    status: m.status || "completed",
    events: [],
    cloud_activity: m.activity || [],
    cloud_citations: m.citations || [],
    cloud_artifacts: m.artifacts || [],
    cloud_attachments: m.attachments || [],
    cloud_version: m.version || 1,
  },
});

function renderCloudExtras(node, message) {
  renderCloudMessageProgress(node, message);
  renderCloudSourceExtras(node, message);
  renderCloudFiles(node, message);
  renderCloudActions(node, message);
}
function renderCloudSourceExtras(node, message) {
  node.querySelectorAll('.ai-source-chip,.cloud-sources').forEach(item=>item.remove());
  const mode=(dokkyState.preferences||dokkyState.account?.bootstrap?.preferences)?.sources||'both';
  if(mode==='both'||mode==='inline')attachCloudCitations($('.message-body',node),message);
  if(mode==='both'||mode==='end')renderCloudSources(node,message.citations);
  const sources=node.querySelector('.cloud-sources'),files=node.querySelector('.message-attachments:not(.user-attachments)');
  if(sources&&files)node.insertBefore(sources,files);
}
function setCloudPreferences(value) {
  dokkyState.preferences={...(dokkyState.preferences||dokkyState.account?.bootstrap?.preferences),...value};
  if(dokkyState.scope==='development')return;
  for(const [id,message] of state.messageSources){
    const node=state.messages.get(id);
    if(node?.isConnected)renderCloudSourceExtras(node,message);
  }
}
async function saveCloudPreferences(value) {
  const saved=await api(cloudPath('preferences'),{method:'PATCH',body:JSON.stringify(value)});
  setCloudPreferences(saved);
}

function cloudRenderModels() {
  const models = (dokkyState.account?.bootstrap?.models || []).filter((model) =>
    model.roles?.includes(dokkyState.scope),
  );
  const selected =
    state.chat?.model ||
    dokkyState.selected[dokkyState.scope] ||
    dokkyState.account?.bootstrap?.preferences?.default_model;
  const signature = JSON.stringify([models.map((model) => [model.id, model.name, model.available]), selected]);
  if ($("#model")._signature === signature) return;
  $("#model")._signature = signature;
  $("#model").replaceChildren();
  for (const model of models) {
    const option = document.createElement("option");
    option.value = model.id;
    option.textContent = model.name.replaceAll("Dokky", "Докки");
    option.disabled = !model.available;
    $("#model").append(option);
  }
  if (models.some((model) => model.id === selected && model.available))
    $("#model").value = selected;
  if (!models.length) $("#model").append(new Option("Войти в Докки", ""));
}

function dokkyChrome() {
  const local = dokkyState.scope === "development";
  window.koddiProductScope = dokkyState.scope;
  document.body.dataset.productScope = dokkyState.scope;
  // заголовок окна меняется только при смене режима, а не при каждой перерисовке
  if (window.koddiDesktop && dokkyChrome.announced !== dokkyState.scope) {
    dokkyChrome.announced = dokkyState.scope;
    window.location.href = `koddi://product?mode=${dokkyState.scope}`;
  }
  $(".sidebar-brand .brand-badge").hidden = !local;
  $('#cloud-library-button').hidden = local;
  $('#cloud-library-button').onclick = openCloudLibrary;
  $("#projects").closest("section").hidden = !local;
  for (const key of [
    "projects",
    "mission",
    "automations",
    "skills",
    "sources",
    "review",
    "logs",
  ])
    document
      .querySelectorAll(`[data-page="${key}"]`)
      .forEach((node) => (node.hidden = !local));
  for (const id of ["workspace-selector", "mode", "reasoning", "toggle-right"])
    if ($("#" + id)) $("#" + id).hidden = !local;
  document.querySelectorAll("[data-product-mode]").forEach((node) => {
    const active = node.dataset.productMode === dokkyState.scope;
    node.classList.toggle("active", active);
    node.setAttribute("aria-selected", String(active));
    node.tabIndex = active ? 0 : -1;
  });
  $("#segmented-mode").dataset.product = dokkyState.scope === "search" ? "search" : "chat";
  if (typeof syncSearchSurface === "function") syncSearchSurface(dokkyState.scope === "search");
  const placeholder = local
    ? (window.innerWidth<720?"Опишите задачу":"Опишите задачу для проекта")
    : dokkyState.scope === "search"
      ? "Что найти в интернете?"
      : "Спросить Докки";
  if ($("#prompt").placeholder !== placeholder) $("#prompt").placeholder = placeholder;
  $("#attach").hidden = dokkyState.scope === "search";
  renderModels();
  if (!local) setPanel("right", true, false);
  $("#connection-badge").dataset.state = dokkyState.account?.authenticated
    ? "ready"
    : "offline";
  setText(
    $(".connection-label"),
    dokkyState.account?.authenticated
      ? dokkyState.account.user?.display_name || "Докки подключён"
      : "Войти в Докки",
  );
  const avatar=$('.connection-dot');
  avatar.classList.add('account-avatar');
  // загруженная аватарка остаётся на месте, буква - только пока картинки нет
  if (!avatar.querySelector('img'))
    setText(avatar, Array.from(dokkyState.account?.user?.display_name||'Д')[0].toLocaleUpperCase());
  updateProductNavigation();
  updateAuthScreen();
}
function renderCloudSidebar(query = "") {
  const saved = state.chats;
  state.chats = dokkyState.chats;
  localUI.renderSidebar(query);
  state.chats = saved;
  $("#chats .load-more")?.remove();
  if (dokkyState.next) {
    const more = document.createElement("button");
    more.className = "item load-more";
    more.textContent = "Загрузить ещё";
    more.onclick = guarded(() => cloudRefresh(dokkyState.next));
    $("#chats").append(more);
  }
}
async function cloudRefresh(cursor = "") {
  dokkyState.account = await api("/api/dokky/session");
  if (!dokkyState.account.authenticated) {
    dokkyState.chats = [];
    renderCloudSidebar();
    dokkyChrome();
    return;
  }
  // поиск заменяет только область чата: список слева остаётся списком чатов
  const modeOf = (value) => (value === "search" ? "chat" : value);
  const listMode = modeOf(dokkyState.scope);
  const data = await api(
    cloudPath(
      `chats?mode=${listMode}&limit=50&cursor=${encodeURIComponent(cursor)}`,
    ),
  );
  if (listMode !== modeOf(dokkyState.scope)) return;
  dokkyState.chats = cursor ? [...dokkyState.chats, ...data.items] : data.items;
  dokkyState.next = data.next_cursor;
  renderCloudSidebar();
  dokkyChrome();
}
function cloudNewChat() {
  switchView("chat");
  state.chat = null;
  state.project = null;
  dokkyState.run = null;
  setRunning(false);
  dokkyState.pending = null;
  dokkyState.createKey = null;
  state.streamBuffers.clear();
  window.koddiCloudCursor = "0";
  // у нового чата и поиска названия нет: заголовок появится вместе с чатом
  $("#title").textContent = "";
  $("#feed").append(welcomeNode());
  state.attachments = [];
  renderAttachments();
  renderCloudSidebar();
  dokkyChrome();
}
async function switchDokkyScope(scope) {
  if (!dokkyState.account?.authenticated && !dokkyState.account?.internal_test)
    return updateAuthScreen();
  if (scope === dokkyState.scope) return;
  const switchVersion = dokkyState.switchVersion = (dokkyState.switchVersion || 0) + 1;
  // анимируется только лента и поле ввода: шапка и переключатели остаются на месте
  const stage = [$("#feed"), $("#composer")];
  for (const node of stage) for (const animation of node.getAnimations()) animation.cancel();
  if (document.documentElement.dataset.motion !== 'reduced')
    await Promise.all(stage.map((node) => node.animate([{opacity:1},{opacity:0}],{duration:100,easing:'ease-in'}).finished.catch(()=>{})));
  if (switchVersion !== dokkyState.switchVersion) return;
  closeCloudLibrary();
  state.streamAbort?.abort();
  state.streamAbort = null;
  const sidebarKept = dokkyState.scope !== "development" && scope !== "development";
  // открытый чат переживает заход в поиск и возвращается обратно
  if (dokkyState.scope === "chat" && scope === "search") dokkyState.parkedChat = state.chat;
  const parked = scope === "chat" ? dokkyState.parkedChat : null;
  if (scope !== "search") dokkyState.parkedChat = null;
  dokkyState.scope = scope;
  localStorage.setItem("koddi-product-scope", scope);
  clearFeed();
  state.chat = null;
  setRunning(false);
  dokkyChrome();
  if (scope === "development") {
    await localUI.refresh();
    if (switchVersion !== dokkyState.switchVersion) return;
    $("#mode").value = "development";
    localUI.newChat(lastProject());
  } else {
    cloudNewChat();
    if (!sidebarKept) await cloudRefresh();
    if (parked && scope === "chat" && switchVersion === dokkyState.switchVersion) await cloudOpen(parked);
  }
  dokkyChrome();
  if (document.documentElement.dataset.motion !== 'reduced')
    for (const node of stage)
      node.animate([{opacity:0},{opacity:1}],{duration:200,easing:'cubic-bezier(.22,1,.36,1)'});
}
async function cloudSync() {
  if (!state.chat) return;
  const id = state.chat.id,
    version = state.viewVersion;
  const [data, current] = await Promise.all([
    api(cloudPath(`chats/${encodeURIComponent(id)}/messages?limit=100`)),
    api(cloudPath(`chats/${encodeURIComponent(id)}`)),
  ]);
  if (state.chat?.id !== id || version !== state.viewVersion) return;
  state.chat = current;
  dokkyState.run = current.active_run?.id || null;
  setRunning(Boolean(dokkyState.run));
  const latest = data.items.map(cloudMessage),
    ids = new Set(latest.map((item) => item.id));
  if (!dokkyState.run && cloudProgress.live)
    handleCloudProgress("turn.completed", {
      message_id: [...latest]
        .reverse()
        .find((item) => item.role === "assistant")?.id,
    });
  const older = [...state.messageSources.values()].filter(
    (item) =>
      !ids.has(item.id) &&
      !String(item.id).startsWith("pending_") &&
      String(item.id).startsWith("msg_"),
  );
  const pending = dokkyState.run
    ? [...state.streamBuffers]
        .filter(([key]) => key === `pending_${dokkyState.run}`)
        .map(([key, value]) => ({
          id: key,
          role: "assistant",
          content: value.content,
          metadata: { status: "running" },
        }))
    : [];
  state.messageSources.clear();
  renderChatMessages([...older, ...latest, ...pending]);
  $("#title").textContent = current.title;
}
async function cloudOpen(chat) {
  // чат из списка открывается в режиме чата, даже если был включён поиск
  if (dokkyState.scope === "search" && chat.mode !== "search") {
    dokkyState.scope = "chat";
    localStorage.setItem("koddi-product-scope", "chat");
    dokkyChrome();
  }
  const view = switchView("chat");
  state.chat = chat;
  state.project = null;
  dokkyState.run = null;
  state.followFeed = true;
  $("#title").textContent = chat.title;
  skeleton();
  updateProductNavigation();
  const loaded = await loadChatView(chat,view,[
    api(cloudPath(`chats/${encodeURIComponent(chat.id)}/messages?limit=100`), {
      signal: view.signal,
    }),
    api(cloudPath(`chats/${encodeURIComponent(chat.id)}`), {
      signal: view.signal,
    }),
  ]);
  if(!loaded)return;
  const [history,current]=loaded;
  if (view.version !== state.viewVersion) return;
  state.chat = current;
  state.streamBuffers.clear();
  window.koddiCloudCursor = "0";
  renderChatMessages(history.items.map(cloudMessage));
  dokkyState.run = current.active_run?.id || null;
  setRunning(Boolean(dokkyState.run));
  dokkyChrome();
  if (dokkyState.run)
    beginCloudProgress(
      chat.id,
      dokkyState.run,
      Date.parse(
        [...history.items].reverse().find((item) => item.role === "user")
          ?.created_at,
      ) || null,
    );
  if (history.next_cursor) {
    let cursor = history.next_cursor;
    const more = document.createElement("button");
    more.className = "secondary";
    more.textContent = "Предыдущие сообщения";
    more.onclick = guarded(async () => {
      if(more.disabled)return;
      more.disabled=true;
      try {
      const data = await api(
        cloudPath(
          `chats/${encodeURIComponent(chat.id)}/messages?limit=100&cursor=${encodeURIComponent(cursor)}`,
        ), {signal:view.signal},
      );
      if (state.chat?.id !== chat.id||state.viewVersion!==view.version) return;
      state.followFeed = false;
      const feed = $("#feed"),
        height = feed.scrollHeight,
        top = feed.scrollTop;
      renderChatMessages([
        ...data.items.map(cloudMessage),
        ...[...state.messageSources.values()],
      ]);
      feed.scrollTop = top + feed.scrollHeight - height;
      cursor = data.next_cursor;
      if (!cursor) more.remove();
      } catch(error) {if(!view.signal.aborted&&state.viewVersion===view.version)throw error;}
      finally {if(more.isConnected)more.disabled=false;}
    });
    $("#feed").prepend(more);
  }
  if (dokkyState.run) attachStream(chat.id, "0");
}
async function cloudAttach(chatId, after = "0") {
  state.streamAbort?.abort();
  const controller = new AbortController();
  state.streamAbort = controller;
  if (!dokkyState.run) return;
  try {
    const response = await fetch(
      cloudPath(
        `runs/${encodeURIComponent(dokkyState.run)}/events?after=${encodeURIComponent(after || "0")}`,
      ),
      { signal: controller.signal },
    );
    if (!response.ok) {
      if (response.status === 401) {
        window.koddiAuthRevoked?.();
        return;
      }
      if (response.status >= 400 && response.status < 500) {
        setRunning(false);
        notify("Не удалось открыть ответ Докки. Обновите чат", true);
        return;
      }
      throw new Error("Не удалось подключиться к ответу Докки");
    }
    await streamResponse(response, chatId, controller);
    if (
      state.running &&
      !controller.signal.aborted &&
      state.chat?.id === chatId
    ) {
      status("Восстанавливаю соединение…");
      setTimeout(() => {
        if (state.chat?.id === chatId && !controller.signal.aborted)
          attachStream(chatId, window.koddiCloudCursor || "0");
      }, 1500);
    }
  } catch (error) {
    if (error.name !== "AbortError" && !controller.signal.aborted) {
      status("Соединение прервано. Переподключаюсь…");
      setTimeout(() => {
        if (state.chat?.id === chatId && !controller.signal.aborted)
          attachStream(chatId, window.koddiCloudCursor || "0");
      }, 3000);
    }
  }
}
async function cloudSend() {
  if(state.chatLoadState!=='ready')return;
  if (state.running && dokkyState.run) {
    await post(cloudPath(`runs/${encodeURIComponent(dokkyState.run)}/cancel`));
    return;
  }
  if (state.running) {
    if (dokkyState.submission) dokkyState.submission.cancelled = true;
    status("Останавливаю отправку…");
    return;
  }
  if (!dokkyState.account?.authenticated) return dokkyLogin();
  if (!attachmentsReady()) return;
  const content = $("#prompt").value.trim();
  if (!content) return;
  if (dokkyState.scope === "search" && state.attachments.length)
    throw new Error("В поиске вложения не поддерживаются. Переключитесь в чат");
  const body = {
    content,
    mode: dokkyState.scope,
    model: $("#model").value || undefined,
    attachment_ids: state.attachments.map((a) => a.id),
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  };
  const fingerprint = JSON.stringify(body);
  if (dokkyState.pending?.fingerprint !== fingerprint)
    dokkyState.pending = { fingerprint, key: crypto.randomUUID(), body };
  const pending = dokkyState.pending,
    { key } = pending;
  const context = {
    version: state.viewVersion,
    scope: dokkyState.scope,
    chat: state.chat,
    cancelled: false,
  };
  dokkyState.submission = context;
  const current = () =>
    context.version === state.viewVersion && context.scope === dokkyState.scope;
  $("#prompt").value = "";
  autoSize();
  $(".empty")?.remove();
  setRunning(true);
  status("Думаю…");
  state.followFeed = true;
  const optimistic = createMessage({
    id: `pending-submit-${key}`,
    role: "user",
    content,
    metadata: {},
  });
  optimistic.classList.add("is-new");
  $("#feed").append(optimistic);
  beginCloudProgress(state.chat?.id || null);
  try {
    if (!context.chat) {
      dokkyState.createKey ||= crypto.randomUUID();
      context.chat = await api(cloudPath("chats"), {
        method: "POST",
        headers: { "Idempotency-Key": dokkyState.createKey },
        body: JSON.stringify({ mode: context.scope, model: body.model }),
      });
    }
    const id = context.chat.id;
    if (current()) {
      state.chat = context.chat;
      if (cloudProgress.live) cloudProgress.live.chatId = id;
    }
    if (context.cancelled) return;
    updateProductNavigation();
    const result = await api(
      cloudPath(`chats/${encodeURIComponent(id)}/messages`),
      {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: JSON.stringify({
          ...pending.body,
          client_message_id: key,
        }),
      },
    );
    if (context.cancelled) {
      await post(cloudPath(`runs/${encodeURIComponent(result.run_id)}/cancel`));
      return;
    }
    if (!current()) return;
    dokkyState.pending = null;
    dokkyState.run = result.run_id;
    window.koddiCloudCursor = "0";
    if (cloudProgress.live) cloudProgress.live.runId = result.run_id;
    state.attachments = [];
    renderAttachments();
    await cloudSync();
    await cloudRefresh();
    await cloudAttach(id);
  } catch (error) {
    if (current()) {
      setRunning(false);
      clearCloudProgress();
      optimistic.remove();
      $("#prompt").value = content;
    }
    throw error;
  } finally {
    if (dokkyState.submission === context) dokkyState.submission = null;
    if (context.cancelled && current()) {
      setRunning(false);
      clearCloudProgress();
      if (state.chat) await cloudSync();
      status("Отправка остановлена");
    }
  }
}
async function dokkyLogin() {
  productUI.loginBusy = true;
  updateAuthScreen();
  let flow;
  try {
    flow = await post("/api/dokky/auth/start");
  } catch (error) {
    productUI.loginBusy = false;
    updateAuthScreen(error.message);
    throw error;
  }
  if (window.koddiDesktop)
    window.location.href = `koddi://open-url?url=${encodeURIComponent(flow.authorize_url)}`;
  else window.open(flow.authorize_url, "_blank", "noopener,noreferrer");
  status("Завершите вход в браузере…");
  clearTimeout(dokkyState.loginTimer);
  const deadline = Date.now() + 600000;
  const poll = async () => {
    try {
      const value = await api("/api/dokky/session");
      if (value.authenticated) {
        dokkyState.account = value;
        dokkyState.account.bootstrap = await post("/api/dokky/bootstrap");
        dokkyState.limits = null;
        dokkyState.limitsError = false;
        await localUI.refresh();
        productUI.loginBusy = false;
        if (dokkyState.scope === "development") localUI.newChat(lastProject());
        else {
          cloudNewChat();
          await cloudRefresh();
        }
        dokkyChrome();
        notify("Вход в Докки выполнен");
        return;
      }
      if (Date.now() > deadline)
        throw new Error("Время входа истекло. Повторите вход");
      dokkyState.loginTimer = setTimeout(poll, 1000);
    } catch (error) {
      productUI.loginBusy = false;
      updateAuthScreen(error.message);
    }
  };
  dokkyState.loginTimer = setTimeout(poll, 1000);
}
async function dokkyAccountPage(view) {
  productUI.section = "account";
  return openSettings();
}
async function initDokky() {
  initComposerActions();
  dokkyState.account = await api("/api/dokky/session");
  llmPage = async () => {
    productUI.section = "account";
    return openSettings();
  };
  names.llm = "Аккаунт Докки";
  const group = $("#segmented-mode");
  group.className = "ai-product-switch";
  group.innerHTML =
    '<span class="ai-product-indicator" aria-hidden="true"></span>' +
    [
      ["chat", "Чат"],
      ["search", "Поиск"],
    ]
      .map(
        ([id, label]) =>
          `<button id="product-${id}" data-product-mode="${id}" type="button" role="tab" aria-selected="false">${label}</button>`,
      )
      .join("");
  group
    .querySelectorAll("button")
    .forEach(
      (node) =>
        (node.onclick = guarded(() =>
          switchDokkyScope(node.dataset.productMode),
        )),
    );
  const stored = localStorage.getItem("koddi-product-scope");
  dokkyState.scope = ["chat", "search", "development"].includes(stored)
    ? stored
    : dokkyState.account.internal_test
      ? "development"
      : "chat";
  newChat = (project) =>
    dokkyState.scope === "development"
      ? localUI.newChat(project)
      : cloudNewChat();
  openChat = (chat) =>
    dokkyState.scope === "development"
      ? localUI.openChat(chat)
      : cloudOpen(chat);
  renderSidebar = (query) =>
    dokkyState.scope === "development"
      ? localUI.renderSidebar(query)
      : renderCloudSidebar(query);
  renderModels = () =>
    dokkyState.scope === "development"
      ? localUI.renderModels()
      : cloudRenderModels();
  refresh = (options) =>
    dokkyState.scope === "development"
      ? localUI.refresh(options)
      : cloudRefresh();
  syncChat = () =>
    dokkyState.scope === "development" ? localUI.syncChat() : cloudSync();
  attachStream = (id, after) =>
    dokkyState.scope === "development"
      ? localUI.attachStream(id, after)
      : cloudAttach(id, after);
  send = () =>
    dokkyState.scope === "development" ? localUI.send() : cloudSend();
  updateSegment = () => dokkyChrome();
  $("#send").onclick = guarded(() =>
    dokkyState.scope === "development" && state.running ? stopTurn() : send(),
  );
  $("#connection-badge").setAttribute('aria-haspopup','menu');
  $("#connection-badge").onclick = showAccountMenu;
  window.koddiAuthRevoked = () => {
    resetCloudSearch();
    dokkyState.parkedChat = null;
    switchView('chat');
    clearTimeout(localQueue.timer);
    Object.assign(localQueue,{signature:null,pending:null,project:undefined,wasRunning:false});
    ++localQueue.permissionsVersion;
    Object.assign(localHistory,{key:'',older:[],current:[],exhausted:false,busy:false});
    state.project=null;
    state.settings.permission_profile='ask';
    state.settings.private_network=false;
    $('#prompt').value=''; autoSize();
    closeCloudLibrary();
    cloudVariants.selected.clear();
    cloudVariants.pending.clear();
    clearCloudProgress(true);
    clearTimeout(dokkyState.loginTimer);
    productUI.loginBusy = false;
    dokkyState.account = { authenticated: false };
    dokkyState.preferences = null;
    dokkyState.limits = null;
    dokkyState.limitsError = false;
    dokkyState.chats = [];
    dokkyState.run = null;
    state.streamAbort?.abort();
    state.chat = null;
    clearFeed();
    setRunning(false);
    dokkyChrome();
    updateAuthScreen("Сессия завершена. Войдите снова");
  };
  const events = new EventSource("/api/events");
  events.addEventListener("dokky.auth_revoked", () =>
    window.koddiAuthRevoked(),
  );
  let refreshTimer;
  for (const kind of ["dokky.chat.updated", "dokky.chat.deleted"])
    events.addEventListener(kind, () => {
      clearTimeout(refreshTimer);
      refreshTimer = setTimeout(
        guarded(async () => {
          if (dokkyState.scope !== "development") await cloudRefresh();
        }),
        300,
      );
    });
  window.addEventListener("beforeunload", () => events.close(), { once: true });
  if (dokkyState.account.authenticated) {
    await post("/api/dokky/bootstrap");
    dokkyState.account = await api("/api/dokky/session");
    await localUI.refresh();
  }
  if (dokkyState.scope === "development") localUI.newChat(lastProject());
  else {
    cloudNewChat();
    await cloudRefresh();
  }
  dokkyChrome();
}
startup.then(initDokky).catch((error) => {
  productUI.loginBusy = false;
  updateAuthScreen(error.message);
});

/* навигация по продукту, вход и постоянные настройки приложения */
const productUI = {
  settings: null,
  section: "general",
  version: 0,
  loginBusy: false,
};
const motionQuery = matchMedia("(prefers-reduced-motion: reduce)");

// Остаток лимитов показывается без цены запроса: только проценты и время освобождения.
const limitsView = { cap: 35 };
const clampPercent = (value) => Math.max(0, Math.min(100, Math.round(Number(value))));
const limitNumber = (value) =>
  value == null || value === '' || !Number.isFinite(Number(value)) ? NaN : Number(value);
function normalizeLimits(subscription) {
  const developer = subscription.developer || {};
  const cap = limitNumber(developer.window_percent) > 0 ? limitNumber(developer.window_percent) : limitsView.cap;
  limitsView.cap = cap;
  const used = limitNumber(developer.window_used_percent);
  const remaining = limitNumber(subscription.remaining_percent);
  return {
    windowRemaining: Number.isFinite(used) ? clampPercent((1 - used / cap) * 100) : null,
    windowResetsAt: developer.window_resets_at || null,
    windowHours: Number(developer.window_hours) || 5,
    remaining: Number.isFinite(remaining) ? clampPercent(remaining) : null,
    expiresAt: subscription.expires_at || null,
  };
}
function applyBillingLimits(billing) {
  if (!billing || typeof billing !== 'object') return;
  const next = { ...(dokkyState.limits || {}) };
  if (billing.window_used_percent != null && Number.isFinite(Number(billing.window_used_percent))) {
    next.windowRemaining = clampPercent((1 - Number(billing.window_used_percent) / limitsView.cap) * 100);
    next.windowResetsAt = billing.window_resets_at ?? null;
  }
  if (billing.remaining_percent != null && Number.isFinite(Number(billing.remaining_percent)))
    next.remaining = clampPercent(billing.remaining_percent);
  dokkyState.limits = next;
  drawLimits();
}
function limitMoment(value) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '';
  return date.toDateString() === new Date().toDateString()
    ? date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
    : date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
}
function drawLimits() {
  const list = document.querySelector('#account-menu .limit-list');
  if (!list) return;
  const limits = dokkyState.limits || {};
  const rows = [
    [`${limits.windowHours || 5} ч`, limits.windowRemaining, limits.windowResetsAt],
    ['Подписка', limits.remaining, limits.expiresAt],
  ];
  list.replaceChildren(...rows.map(([name, percent, moment]) => {
    const row = document.createElement('div');
    row.className = 'limit-row';
    const known = percent != null;
    row.innerHTML = '<strong></strong><span class="limit-value"></span><span class="limit-time"></span><i class="limit-bar"><b></b></i>';
    row.children[0].textContent = name;
    row.children[1].textContent = known ? `${percent}%`
      : dokkyState.limitsRequest ? '…' : dokkyState.limitsError ? 'нет данных' : '—';
    row.children[2].textContent = known && moment ? limitMoment(moment) : '';
    row.querySelector('b').style.width = `${known ? percent : 0}%`;
    row.classList.toggle('low', known && percent <= 15);
    return row;
  }));
}
async function loadLimits() {
  if (dokkyState.limitsRequest) return dokkyState.limitsRequest;
  dokkyState.limitsRequest = api(cloudPath('subscription'))
    .then((value) => {
      dokkyState.limits = normalizeLimits(value);
      dokkyState.limitsError = false;
    })
    .catch(() => {
      dokkyState.limitsError = !dokkyState.limits;
    })
    .finally(() => {
      dokkyState.limitsRequest = null;
      drawLimits();
    });
  return dokkyState.limitsRequest;
}

function showAccountMenu() {
  const trigger=$('#account-menu-toggle')||$('#connection-badge');
  let menu=$('#account-menu');
  if(menu&&!menu.hidden){SurfaceUI.close(menu,true);return;}
  menu?.remove();menu=document.createElement('div');
  menu.id='account-menu';
  menu.className='surface-panel account-menu';menu.role='menu';menu.hidden=true;
  const name=document.createElement('strong');name.className='account-menu-name';
  name.textContent=dokkyState.account?.user?.display_name||'Аккаунт Докки';menu.append(name);
  const limits=document.createElement('section');limits.className='account-limits';
  limits.setAttribute('aria-label','Оставшийся лимит');
  limits.innerHTML=`<h3>${icon('Gauge',15)}<span>Оставшийся лимит</span></h3><div class="limit-list"></div>`;
  menu.append(limits);
  for(const [title,symbol,section] of [['Аккаунт','CircleUserRound','account'],['Настройки','settings','general'],['Использование','ChartNoAxesColumn','usage']]){
    const item=document.createElement('button');item.type='button';item.role='menuitem';
    item.innerHTML=icon(symbol)+`<span>${title}</span>`;
    item.onclick=guarded(async()=>{SurfaceUI.close(menu);productUI.section=section;await openSettings();});menu.append(item);
  }
  document.body.append(menu);drawLimits();SurfaceUI.open(menu,trigger);
  menu.onkeydown=event=>SurfaceUI.menuKeys(event,menu);menu.querySelector('button').focus({preventScroll:true});
  void loadLimits();
}

function initComposerActions() {
  $('#reasoning').onchange = guarded(changeReasoningMode);
  const permissions = document.createElement("select");
  permissions.id = "permission-selector";
  permissions.setAttribute("aria-label", "Разрешения действий");
  permissions.title = "Разрешения действий";
  for (const [id, title] of [
    ["ask", "Вручную"],
    ["trusted", "Автоматически"],
  ])
    permissions.append(new Option(title, id));
  permissions.onchange = async () => {
    const previous = state.settings.permission_profile || "ask",
      value = permissions.value;
    if (
      value === "trusted" &&
      !(await confirmDialog(
        "Включить автоматическое выполнение?",
        "Докки Code сможет изменять файлы и запускать команды без отдельных подтверждений только в выбранном проекте и текущем аккаунте.",
        "Включить",
      ))
    ) {
      permissions.value = previous;
      return;
    }
    permissions.disabled = true;
    try {
      await saveAppearance({ permission_profile: value });
    } catch (error) {
      permissions.value = previous;
      notify(error.message, true);
    } finally {
      permissions.disabled = false;
      updateProductNavigation();
    }
  };
  $(".agent-controls").prepend(permissions);
  const menu = document.createElement("div");
  menu.id = "composer-actions";
  menu.className = "composer-actions surface-panel";
  menu.hidden = true;
  menu.setAttribute("role", "menu");
  const actions = [
    ["Добавить файлы или фото", "attach", () => $("#files").click()],
    ["Открыть папку", "folder", openFolder],
    ["Создать проект", "plus", createProject],
    ["Сжать контекст", "ListTodo", compactCodingContext],
    [
      "Планирование",
      "ListTodo",
      () => {
        $("#mode").value = $("#mode").value === "plan" ? "development" : "plan";
        updateProductNavigation();
      },
    ],
  ];
  actions.forEach(([title, symbol, action], index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("role", title === 'Планирование' ? "menuitemcheckbox" : "menuitem");
    button.dataset.localAction = String(index > 0);
    button.innerHTML = icon(symbol) + `<span>${title}</span>`;
    button.onclick = guarded(async () => {
      SurfaceUI.close(menu);
      await action();
    });
    menu.append(button);
  });
  document.body.append(menu);
  const chip = document.createElement("button");
  chip.id = "planning-chip";
  chip.type = "button";
  chip.className = "planning-chip";
  chip.hidden = true;
  chip.innerHTML =
    icon("ListTodo") + "<span>Планирование</span>" + icon("close");
  chip.title = "Выключить планирование";
  chip.onclick = () => {
    $("#mode").value = "development";
    updateProductNavigation();
  };
  $("#composer").append(chip);
  $("#attach").innerHTML = icon("plus");
  $("#attach").title = "Добавить";
  $("#attach").setAttribute("aria-label", "Добавить");
  $("#attach").setAttribute("aria-haspopup", "menu");
  $("#attach").onclick = (event) => {
    event.stopPropagation();
    if (dokkyState.scope !== "development") return openCloudAttachMenu();
    menu
      .querySelectorAll("[data-local-action='true']")
      .forEach(
        (button) => (button.hidden = dokkyState.scope !== "development"),
      );
    menu.lastElementChild.setAttribute(
      "aria-checked",
      String($("#mode").value === "plan"),
    );
    if(menu.hidden) SurfaceUI.open(menu,$('#attach')); else SurfaceUI.close(menu);
    $("#attach").setAttribute("aria-expanded", String(!menu.hidden));
    if (!menu.hidden) menu.querySelector("button").focus();
  };
  document.addEventListener("click", (event) => {
    if (!menu.contains(event.target) && !event.target.closest("#attach")) {
      SurfaceUI.close(menu);
      $("#attach").setAttribute("aria-expanded", "false");
    }
  });
  menu.onkeydown = (event) => SurfaceUI.menuKeys(event, menu);
}

function renderPlanApproval(snapshot) {
  if (!state.chat || dokkyState.scope !== "development" || !snapshot.document) {
    $("#plan-approval")?.remove();return;
  }
  const chatId = state.chat.id;
  let root = $("#plan-approval");
  if(root && root.dataset.planKey !== `${chatId}:${snapshot.turn_id}`){root.remove();root=null;}
  if(!root){
    root=document.createElement('section');root.id='plan-approval';root.className='plan-document';
    root.dataset.planKey=`${chatId}:${snapshot.turn_id}`;
    root.innerHTML=`<details><summary aria-controls="plan-document-content" aria-expanded="false">${icon('chevron')}<span><strong>План реализации</strong><small>plan.md</small></span>${icon('file')}</summary></details><div id="plan-document-content" class="plan-document-body message-body" inert></div><footer></footer>`;
    $('details',root).addEventListener('toggle', () => {
      const open = $('details',root).open;
      $('summary',root).setAttribute('aria-expanded', String(open));
      $('.plan-document-body',root).inert = !open;
    });
    $('#feed').append(root);
  }
  const previous=[...$('#feed').children].filter(node=>{
    const item=state.messageSources.get(node.dataset.messageId);
    return String(node.dataset.turnId||item?.metadata?.turn_id||'')===String(snapshot.turn_id);
  }).at(-1);
  if(previous&&previous.nextElementSibling!==root)previous.after(root);
  if(root._document!==snapshot.document){
    renderMarkdown($('.plan-document-body',root),snapshot.document,false);
    root._document=snapshot.document;
    const heading=snapshot.document.match(/^#\s+(.+)$/m)?.[1];
    $('summary strong',root).textContent=heading||'План реализации';
  }
  const footer=$('footer',root);footer.replaceChildren();
  const download=document.createElement('button');download.type='button';download.className='secondary';
  download.innerHTML=icon('download')+'<span>Скачать план</span>';
  download.onclick=()=>{
    const url=URL.createObjectURL(new Blob([snapshot.document],{type:'text/markdown;charset=utf-8'}));
    const link=document.createElement('a');link.href=url;link.download='plan.md';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  footer.append(download);
  if(!snapshot.ready){
    const note=document.createElement('span');note.className='muted';
    note.textContent=snapshot.executed?'План принят к выполнению':state.running?'План составляется…':'План сохранён';
    footer.append(note);return;
  }
  const execute=document.createElement('button');execute.type='button';execute.className='primary';
  execute.innerHTML=icon('Play')+'<span>Реализуй план</span>';footer.append(execute);
  execute.onclick = guarded(async () => {
    if (state.chat?.id !== chatId || state.running || state.reasoningBusy) return;
    execute.disabled=true;
    if (!state.project) {
      await chooseWorkspace();
      if (!state.project){execute.disabled=false;return;}
    }
    const controller = new AbortController();
    state.streamAbort?.abort();
    state.streamAbort = controller;
    setRunning(true);
    status("Начинаю выполнение плана…");
    try {
      const response = await fetch(`/api/chats/${chatId}/plan/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ turn_id: snapshot.turn_id }),
        signal: controller.signal,
      });
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "Не удалось выполнить план");
      }
      $("#mode").value = "development";
      state.chat.mode = "development";
      updateProductNavigation();
      execute.remove();
      snapshot.executed=true;snapshot.ready=false;
      await streamResponse(response, chatId, controller);
    } finally {
      if (state.streamAbort === controller) {
        state.streamAbort = null;
        await syncChat();
      }
      if(execute.isConnected)execute.disabled=false;
    }
  });
}

const reasoningModes = ['light', 'pro', 'max'];
function currentReasoningMode() {
  // выбранный, но ещё не применённый режим показывается как текущий: он вступит в силу со следующим сообщением
  const value = state.codingSession?.pending_reasoning_mode || state.codingSession?.reasoning_mode || state.settings.coding_reasoning_mode;
  return reasoningModes.includes(value) ? value : 'light';
}
const codingCacheTimer = {timer:null, lastActivity:Date.now()};
for (const event of ['pointerdown', 'keydown']) document.addEventListener(event, e => {
  if (e.isTrusted) {codingCacheTimer.lastActivity = Date.now(); scheduleCodingKeepalive();}
}, {passive:true});
function codingKeepaliveDelay(cache, now, idle, enabled, busy) {
  const until = Date.parse(cache?.valid_until);
  if (!enabled || busy || idle > 90 * 60_000 || !(Number(cache?.prompt_tokens) >= 4000) ||
      !Number.isFinite(until) || until <= now) return null;
  return Math.max(0, until - now - 4 * 60_000);
}
function scheduleCodingKeepalive() {
  clearTimeout(codingCacheTimer.timer);
  const chatId = state.chat?.id, version = state.viewVersion;
  if (!chatId || dokkyState.scope !== 'development' || !dokkyState.account?.authenticated) return;
  const cache = state.codingSession?.last_usage?.dokky?.cache;
  const delay = codingKeepaliveDelay(cache, Date.now(), Date.now() - codingCacheTimer.lastActivity,
    state.settings.coding_keepalive === true, state.running || state.reasoningBusy);
  if (delay == null) return;
  codingCacheTimer.timer = setTimeout(async () => {
    if (state.chat?.id !== chatId || state.viewVersion !== version || state.running || state.reasoningBusy ||
        state.settings.coding_keepalive !== true || !dokkyState.account?.authenticated ||
        Date.now() - codingCacheTimer.lastActivity > 90 * 60_000) return;
    try {
      const result = await api(`/api/chats/${chatId}/coding-session/warm`, {method:'POST'});
      if (state.chat?.id !== chatId || state.viewVersion !== version) return;
      if (result.warmed) {
        state.codingSession.last_usage.dokky.cache = result.cache;
        applyBillingLimits(result.billing); scheduleCodingKeepalive();
      }
    } catch (_) { /* следующий рабочий ответ заново запланирует продление */ }
  }, delay);
}
function contextOperationStatus(text) {
  let row = $('#context-operation');
  if (!text) {row?.remove(); return;}
  if (!row) {row=document.createElement('div');row.id='context-operation';row.className='journal-stage';row.role='status';$('#feed').append(row);}
  row.textContent=text;
}
async function compactCodingContext() {
  const chatId=state.chat?.id, version=state.viewVersion;
  if (!chatId || state.running || state.reasoningBusy) return;
  const controller=new AbortController();
  state.reasoningBusy=true; updateProductNavigation();contextOperationStatus('Сжимаю контекст…');
  const cancel=document.createElement('button');cancel.type='button';cancel.textContent='Отменить';
  cancel.onclick=()=>controller.abort();$('#context-operation').append(cancel);
  try {
    const result=await api(`/api/chats/${chatId}/coding-session/compact`, {method:'POST',signal:controller.signal});
    if (state.chat?.id===chatId && state.viewVersion===version) {
      state.codingSession=result.session;contextOperationStatus(result.result.text);
    }
  } catch(error) {
    if (state.chat?.id===chatId && state.viewVersion===version) contextOperationStatus(error.name==='AbortError'?'Сжатие отменено. История сохранена.':error.message);
    if (error.name!=='AbortError') throw error;
  } finally {if (state.chat?.id===chatId && state.viewVersion===version) {state.reasoningBusy=false;updateProductNavigation();}}
}
async function loadCodingSession(chatId, version) {
  if (state.reasoningBusy) return;
  // тестовый провайдер без входа не создаёт серверные сессии
  if (dokkyState.account?.internal_test && !dokkyState.account.authenticated) return;
  const request = ++state.codingRequest;
  const usageBefore = state.latestUsage;
  try {
    const session = await api(`/api/chats/${chatId}/coding-session`, {signal:state.viewAbort?.signal});
    if (request !== state.codingRequest || version !== state.viewVersion || chatId !== state.chat?.id || state.reasoningBusy) return;
    if (state.codingSession && session.version < state.codingSession.version) return;
    state.codingSession = session;
    scheduleCodingKeepalive();
    if (session.last_usage && state.latestUsage === usageBefore) state.latestUsage = session.last_usage;
  } catch (error) {
    if (error.name !== 'AbortError' && version === state.viewVersion && chatId === state.chat?.id)
      $('#reasoning').dataset.tooltip = 'Не удалось загрузить режим. Откройте чат повторно.';
  } finally {
    if (version === state.viewVersion && chatId === state.chat?.id) updateProductNavigation();
  }
}
async function changeReasoningMode() {
  const select = $('#reasoning'), mode = select.value, previous = currentReasoningMode();
  select.value = previous;
  if (!reasoningModes.includes(mode) || mode === previous || state.running || state.reasoningBusy || dokkyState.scope !== 'development') return;
  const chatId = state.chat?.id, version = state.viewVersion;
  const current = () => version === state.viewVersion && chatId === state.chat?.id && dokkyState.scope === 'development';
  ++state.codingRequest;
  state.reasoningBusy = true;updateProductNavigation();
  try {
    if (!chatId) {
      const settings = await api('/api/settings', {method:'PUT', body:JSON.stringify({coding_reasoning_mode:mode})});
      state.settings.coding_reasoning_mode = settings.coding_reasoning_mode || mode;
      return;
    }
    const session = await api(`/api/chats/${chatId}/coding-session`);
    if (!current() || state.running) return;
    state.codingSession = session;
    // возврат к режиму, который стоит сейчас, отменяет отложенную смену сразу и без вопросов
    if (session.reasoning_mode === mode) {
      state.codingSession = await api(`/api/chats/${chatId}/coding-session`, {
        method:'PATCH', body:JSON.stringify({reasoning_mode:mode, acknowledge_cache_cost:false}),
      });
      return;
    }
    const hasHistory = Number(session.history?.length || 0) > 0 || state.messageSources.size > 0;
    if (hasHistory && !(await confirmDialog(
      'Сменить режим?',
      'Смена режима может увеличить стоимость следующего запроса, поскольку ИИ может потребоваться повторно обработать историю диалога. Сама история сохранится.',
      'Сменить режим',
    ))) return;
    if (!current() || state.running) return;
    // с историей режим только запоминается: ни плана, ни сжатия, ни запросов к серверу до следующего сообщения
    const updated = await api(`/api/chats/${chatId}/coding-session`, {
      method:'PATCH', body:JSON.stringify({reasoning_mode:mode, acknowledge_cache_cost:hasHistory}),
    });
    if (current()) {
      state.codingSession = updated;
      if (hasHistory) notify('Режим сменится со следующим сообщением');
    }
  } finally {
    if (current()) {state.reasoningBusy = false;updateProductNavigation();scheduleCodingKeepalive();}
  }
}
function applyAppearance() {
  const settings = state.settings;
  document.documentElement.dataset.motion =
    settings.ui_motion === "reduced" ||
    (settings.ui_motion !== "full" && motionQuery.matches)
      ? "reduced"
      : "full";
  document.documentElement.style.setProperty(
    "--chat-width",
    { compact: "680px", standard: "760px", wide: "1080px" }[
      settings.chat_width
    ] || "760px",
  );
  // один базовый размер: все остальные размеры текста считаются от него (см. --fs-N в app.css)
  document.documentElement.style.setProperty(
    "--base",
    `${Math.min(20, Math.max(8, Number(settings.text_size) || 14))}px`,
  );
  document.documentElement.style.setProperty(
    "--code-font",
    `"${settings.code_font || "Consolas"}", monospace`,
  );
}
motionQuery.addEventListener("change", applyAppearance);

function updateProductNavigation() {
  if (typeof dokkyState === "undefined") return;
  const development = dokkyState.scope === "development";
  // свойство пишется только при изменении: нет лишних записей в DOM и перерисовок
  const flag = (node, name, value) => {
    if (node && node[name] !== value) node[name] = value;
  };
  flag($("#mode"), "hidden", true);
  flag($("#reasoning"), "hidden", !development);
  flag($("#reasoning"), "value", currentReasoningMode());
  flag($("#reasoning"), "disabled", state.running || state.reasoningBusy || state.chatLoadState !== 'ready' || Boolean(state.chat && !state.codingSession));
  flag($("#model"), "hidden", development || $("#model").options.length <= 1);
  const planning = $("#mode").value === "plan";
  if ($("#permission-selector")) {
    flag($("#permission-selector"), "hidden", !development);
    flag($("#permission-selector"), "disabled", planning);
    flag($("#permission-selector"), "value", state.settings.permission_profile === 'trusted' ? 'trusted' : 'ask');
  }
  if ($("#composer-actions"))
    flag($("#composer-actions").lastElementChild, "disabled", state.running);
  flag($("#planning-chip"), "disabled", state.running);
  flag($("#planning-chip"), "hidden", !development || $("#mode").value !== "plan");
  const group = $("#segmented-mode");
  flag(group, "hidden", development);
  // переключатель живёт в одном узле: меняются только атрибуты, индикатор едет сам
  const toggle = $("#product-switch");
  const target = development ? "development" : "chat";
  if (toggle.dataset.scope !== target) toggle.dataset.scope = target;
  toggle.querySelectorAll("[data-sidebar-scope]").forEach((button) => {
    const on = button.dataset.sidebarScope === target;
    if (button.getAttribute("aria-selected") !== String(on))
      button.setAttribute("aria-selected", String(on));
    button.tabIndex = on ? 0 : -1;
  });
  if (!toggle._bound) {
    toggle._bound = true;
    toggle.addEventListener("click", (event) => {
      const button = event.target.closest("[data-sidebar-scope]");
      if (button) guarded(() => switchDokkyScope(button.dataset.sidebarScope))();
    });
    toggle.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      const next = toggle.querySelector('[aria-selected="false"]');
      if (!next) return;
      event.preventDefault();
      next.focus();
      next.click();
    });
  }
  SurfaceUI.refresh();
}

function updateAuthScreen(error = "") {
  const ready = Boolean(
    dokkyState.account?.authenticated || dokkyState.account?.internal_test,
  );
  const shell = $("#shell");
  const wasHidden = shell.inert;
  document.body.classList.remove("auth-pending");
  document.body.classList.toggle("auth-required", !ready);
  document.body.classList.toggle("auth-waiting", !ready && productUI.loginBusy);
  shell.inert = !ready;
  if (ready && wasHidden) requestAnimationFrame(autoSize);
  shell.setAttribute("aria-hidden", String(!ready));
  $("#auth-screen").hidden = ready;
  const login = $("#auth-login");
  login.disabled = productUI.loginBusy;
  login.textContent = productUI.loginBusy
    ? "Ожидание входа…"
    : "Войти с помощью Докки";
  $("#auth-status").textContent =
    error ||
    (productUI.loginBusy ? "Завершите вход в открывшемся браузере" : "");
  login.onclick = async () => {
    productUI.loginBusy = true;
    updateAuthScreen();
    try {
      await dokkyLogin();
    } catch (failure) {
      productUI.loginBusy = false;
      updateAuthScreen(failure.message);
    }
  };
  if (!ready && productUI.settings?.open) productUI.settings.close();
}

async function saveAppearance(values) {
  const project = state.project?.id || null;
  if (('permission_profile' in values || 'private_network' in values) && state.project) values = {...values,project_id:state.project.id};
  const settings = await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify(values),
  });
  state.settings = {...settings,permission_profile:project===(state.project?.id||null)?settings.permission_profile:'ask'};
  if(project!==(state.project?.id||null)) {
    localQueue.project=undefined;
    await refreshProjectPermissions();
  }
  document.documentElement.dataset.theme =
    state.settings.theme === "light" ? "light" : "dark";
  applyAppearance();
  updateEnvironment();
  dokkyChrome();
}

const settingsSections = [
  [
    "general",
    "Общие",
    "Settings",
    "тема анимации шрифт ширина отправка интерфейс",
  ],
  [
    "account",
    "Аккаунт",
    "CircleUserRound",
    "вход выход имя профиль подключение",
  ],
  [
    "conversation",
    "Общение",
    "MessagesSquare",
    "память источники ответы подробность",
  ],
  [
    "development",
    "Разработка",
    "Code2",
    "проект разрешения режимы инструменты",
  ],
  [
    "usage",
    "Использование",
    "ChartNoAxesColumn",
    "тариф лимиты подписка баланс",
  ],
];

function settingRow(root, title, detail, control) {
  const row = document.createElement("div");
  row.className = "preference-row";
  const label = document.createElement("label");
  label.innerHTML = `<strong>${escapeHtml(title)}</strong>${detail ? `<span>${escapeHtml(detail)}</span>` : ""}`;
  control.id = "preference-" + crypto.randomUUID();
  control.setAttribute("aria-label", title);
  label.htmlFor = control.id;
  row.append(label, control);
  root.append(row);
  return control;
}

function settingSelect(root, title, detail, options, value, save) {
  const select = document.createElement("select");
  for (const [id, name] of options) select.append(new Option(name, id));
  select.value = String(value);
  settingRow(root, title, detail, select);
  select.onchange = async () => {
    const old = String(value);
    select.disabled = true;
    try {
      await save(select.value);
      value = select.value;
      $("#settings-feedback").textContent = "Сохранено";
    } catch (error) {
      select.value = old;
      $("#settings-feedback").textContent = error.message;
    } finally {
      select.disabled = false;
    }
  };
}

async function showSettingsSection(section) {
  productUI.section = section;
  const version = ++productUI.version;
  const root = $("#settings-body");
  root.replaceChildren();
  $("#settings-heading").textContent = settingsSections.find(
    (item) => item[0] === section,
  )[1];
  $("#settings-feedback").textContent = "";
  document.querySelectorAll("[data-settings-section]").forEach((button) => {
    button.setAttribute(
      "aria-current",
      button.dataset.settingsSection === section ? "page" : "false",
    );
  });
  document
    .querySelector(`[data-settings-section="${section}"]`)
    ?.scrollIntoView({ block: "nearest", inline: "nearest" });
  if (section === "general") {
    const build = document.createElement('p'); build.className='muted'; root.append(build);
    api('/api/app/diagnostics').then(value=>{
      if(build.isConnected) build.textContent=`Сборка ${value.build_number || 'неизвестна'}`;
    }).catch(()=>{if(build.isConnected)build.textContent='Номер сборки недоступен';});
    const settings = state.settings;
    for (const [key, title, description, options, fallback] of [
      [
        "theme",
        "Тема",
        "",
        [
          ["dark", "Тёмная"],
          ["light", "Светлая"],
        ],
        "dark",
      ],
      [
        "ui_motion",
        "Анимации",
        "Учитывается системная настройка уменьшения движения.",
        [
          ["system", "Как в системе"],
          ["full", "Включены"],
          ["reduced", "Минимальные"],
        ],
        "system",
      ],
      [
        "chat_width",
        "Ширина чата",
        "",
        [
          ["compact", "Узкая"],
          ["standard", "Обычная"],
          ["wide", "Широкая"],
        ],
        "standard",
      ],
      [
        "text_size",
        "Размер текста",
        "",
        [8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20].map((size) => [size, `${size} px`]),
        14,
      ],
      [
        "code_font",
        "Шрифт кода",
        "",
        [
          ["Consolas", "Consolas"],
          ["Cascadia Code", "Cascadia Code"],
          ["monospace", "Системный моноширинный"],
        ],
        "Consolas",
      ],
    ])
      settingSelect(
        root,
        title,
        description,
        options,
        settings[key] ?? fallback,
        (value) =>
          saveAppearance({
            [key]: key === "text_size" ? Number(value) : value,
          }),
      );
    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.role = "switch";
    toggle.checked = settings.enter_sends !== false;
    settingRow(
      root,
      "Отправка по Enter",
      "Shift+Enter добавляет строку. При отключении отправка по Ctrl+Enter.",
      toggle,
    );
    toggle.onchange = async () => {
      toggle.disabled = true;
      try {
        await saveAppearance({ enter_sends: toggle.checked });
      } catch (error) {
        toggle.checked = !toggle.checked;
        $("#settings-feedback").textContent = error.message;
      } finally {
        toggle.disabled = false;
      }
    };
    const keepalive=document.createElement('input');keepalive.type='checkbox';keepalive.role='switch';
    keepalive.checked=settings.coding_keepalive===true;
    settingRow(root,'Продлевать кэш разработки','Платное продление только открытого диалога. Останавливается после 90 минут бездействия.',keepalive);
    keepalive.onchange=async()=>{
      keepalive.disabled=true;
      try {await saveAppearance({coding_keepalive:keepalive.checked});scheduleCodingKeepalive();}
      catch(error) {keepalive.checked=!keepalive.checked;$('#settings-feedback').textContent=error.message;}
      finally {keepalive.disabled=false;}
    };
    return;
  }
  if (section === "development") {
    if(state.project) {
      const recovery=document.createElement('section'); root.append(recovery);
      api(`/api/projects/${state.project.id}/write-recovery`).then(items=>{
        if(!recovery.isConnected||!items.length)return;
        const heading=document.createElement('h3');heading.textContent='Незавершённые записи файлов';recovery.append(heading);
        for(const item of items) {
          const row=document.createElement('div');row.className='preference-row';
          const label=document.createElement('span');
          label.textContent=`${item.files.map(file=>file.path).join(', ')} · ${item.state==='conflict'?'Есть конфликт':item.state==='applied'?'Изменения на диске':'Исходные файлы сохранены'}`;
          row.append(label);
          if(item.state!=='conflict'&&item.status==='prepared') {
            const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent=item.state==='applied'?'Сохранить результат':'Закрыть восстановление';
            button.onclick=guarded(async()=>{
              if(!await confirmDialog('Подтвердить состояние файлов?',label.textContent,'Подтвердить'))return;
              await post(`/api/projects/${item.project_id}/write-recovery/${item.id}`,{confirm:true,action:item.state==='applied'?'keep':'restore',expected_hashes:Object.fromEntries(item.files.map(file=>[file.path,file.current_hash]))});
              await showSettingsSection('development');
            });row.append(button);
          }
          recovery.append(row);
        }
      }).catch(error=>{if(recovery.isConnected)recovery.textContent=error.message;});
    }
    settingSelect(
      root,
      "Доступ к проекту",
      "Разрешения действуют только для выбранного проекта и текущего аккаунта.",
      [
        ["ask", "Вручную"],
        ["trusted", "Автоматически"],
      ],
      state.settings.permission_profile || "ask",
      (value) => saveAppearance({ permission_profile: value }),
    );
    if (state.project) settingSelect(
      root, 'Локальная сеть',
      'Разрешить веб-инструментам обращаться к localhost и устройствам вашей сети в этом проекте.',
      [['off','Запрещено'],['on','Разрешено']],
      state.settings.private_network ? 'on' : 'off',
      async value => {
        if(value==='on' && !await confirmDialog('Разрешить доступ к локальной сети?', 'Агент сможет обращаться к локальным сервисам. Не включайте для непроверенного проекта.', 'Разрешить')) return;
        await saveAppearance({private_network:value==='on'});
      },
    );
    const heading = document.createElement("h3");
    heading.textContent = "Режимы агента";
    root.append(heading);
    for (const mode of state.modes) {
      const line = document.createElement("div");
      line.className = "preference-row";
      const name = document.createElement("strong");
      name.textContent = mode.name;
      const count = document.createElement("span");
      count.className = "muted";
      count.textContent = mode.builtin
        ? "Встроенный"
        : `${mode.tools.length} инструментов`;
      line.append(name, count);
      root.append(line);
    }
    return;
  }
  if (section === "account") {
    if (!dokkyState.account?.authenticated) {
      root.innerHTML = button("Войти с помощью Докки", "user", "primary");
      $("button", root).onclick = guarded(dokkyLogin);
      return;
    }
    const title = document.createElement("h3");
    title.textContent =
      dokkyState.account?.user?.display_name || "Аккаунт Докки";
    root.append(title);
    const actions = document.createElement("div");
    actions.className = "actions";
    actions.innerHTML =
      button("Обновить аккаунт", "refresh") + button("Выйти", "LogOut");
    root.append(actions);
    actions.firstElementChild.onclick = guarded(async () => {
      dokkyState.account.bootstrap = await post("/api/dokky/bootstrap");
      dokkyState.account = await api("/api/dokky/session");
      dokkyChrome();
      await showSettingsSection("account");
    });
    actions.lastElementChild.onclick = guarded(async () => {
      if (
        !(await confirmDialog(
          "Выйти из Докки?",
          "Работающие задачи будут остановлены. Локальная история аккаунта и кэш будут удалены. Файлы проектов и серверные чаты Докки сохранятся.",
          "Выйти",
        ))
      )
        return;
      const result = await post("/api/dokky/logout");
      if (result.session_changed) return;
      closeSettings();
      window.koddiAuthRevoked();
    });
    return;
  }
  root.innerHTML =
    '<div class="settings-loading skeleton" aria-label="Загрузка настроек"></div>';
  try {
    const value = await api(
      cloudPath(section === "usage" ? "subscription" : "preferences"),
    );
    if (version !== productUI.version || !productUI.settings.open) return;
    root.replaceChildren();
    if (section === "conversation") {
      setCloudPreferences(value);
      settingSelect(
        root,
        "Подробность ответов",
        "",
        [
          ["brief", "Кратко"],
          ["standard", "Обычно"],
          ["full", "Подробно"],
        ],
        value.detail || "standard",
        (detail) => saveCloudPreferences({ detail }),
      );
      settingSelect(
        root,
        "Источники",
        "",
        [
          ["both", "В тексте и в конце"],
          ["inline", "В тексте"],
          ["end", "В конце"],
          ["none", "Скрыты"],
        ],
        value.sources || "both",
        (sources) => saveCloudPreferences({ sources }),
      );
      const memory = document.createElement("input");
      memory.type = "checkbox";
      memory.role = "switch";
      memory.checked = Boolean(value.memory_enabled);
      settingRow(
        root,
        "Память Докки",
        "Настройка серверного аккаунта.",
        memory,
      );
      memory.onchange = async () => {
        memory.disabled = true;
        try {
          await api(cloudPath("preferences"), {
            method: "PATCH",
            body: JSON.stringify({ memory_enabled: memory.checked }),
          });
        } catch (error) {
          memory.checked = !memory.checked;
          $("#settings-feedback").textContent = error.message;
        } finally {
          memory.disabled = false;
        }
      };
    } else {
      const labels = {
        free: "Бесплатный",
        plus: "Плюс",
        pro: "Про",
        ultra: "Ультра",
      };
      // денег в интерфейсе нет: только токены и остаток лимита в процентах
      const usage=state.latestUsage?.dokky?.usage || state.codingSession?.last_usage?.dokky?.usage;
      if (Number.isSafeInteger(usage?.cached_tokens) && usage.cached_tokens >= 0) {
        const cached=document.createElement('p');cached.className='muted';
        cached.textContent=`Использовано токенов из кэша: ${usage.cached_tokens.toLocaleString('ru-RU')}`;root.append(cached);
      }
      for (const [label, text] of [
        ["Тариф", labels[value.plan] || value.plan || "Не указан"],
        ["Остаток лимита", `${value.remaining_percent ?? 0}%`],
        [
          "Окно разработки",
          (() => {
            const left = normalizeLimits(value).windowRemaining;
            return left == null ? "нет данных" : `осталось ${left}%`;
          })(),
        ],
      ]) {
        const row = document.createElement("div");
        row.className = "preference-row";
        const name = document.createElement("strong"),
          detail = document.createElement("span");
        name.textContent = label;
        detail.textContent = text;
        row.append(name, detail);
        root.append(row);
      }
      if (value.developer?.reason) {
        const reason = document.createElement("p");
        reason.className = "muted";
        reason.textContent = value.developer.reason;
        root.append(reason);
      }
    }
  } catch (error) {
    if (version !== productUI.version) return;
    root.replaceChildren();
    const message = document.createElement("p");
    message.textContent = error.message;
    root.append(message);
    const retry = document.createElement("button");
    retry.className = "secondary";
    retry.textContent = "Повторить";
    retry.onclick = () => showSettingsSection(section);
    root.append(retry);
  }
}

function closeSettings() {
  if (!productUI.settings?.open) return;
  const modal = productUI.settings;
  modal.classList.add("is-closing");
  const duration =
    document.documentElement.dataset.motion === "reduced" ? 0 : 140;
  setTimeout(() => {
    modal.close();
    modal.classList.remove("is-closing");
    ++productUI.version;
  }, duration);
}

async function openSettings() {
  if (!dokkyState.account?.authenticated && !dokkyState.account?.internal_test)
    return updateAuthScreen();
  if (!productUI.settings) {
    const modal = document.createElement("dialog");
    modal.className = "settings-window";
    modal.setAttribute("aria-labelledby", "settings-heading");
    modal.innerHTML = `<div class="settings-layout"><aside class="settings-nav"><label class="settings-search">${icon("search")}<input type="search" aria-label="Найти настройку" placeholder="Поиск настроек"></label><nav aria-label="Разделы настроек"></nav></aside><section class="settings-main"><header><h2 id="settings-heading">Настройки</h2>${iconButton("Закрыть настройки", "close")}</header><div id="settings-body"></div><p id="settings-feedback" role="status" aria-live="polite"></p></section></div>`;
    for (const [id, title, symbol] of settingsSections) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.settingsSection = id;
      button.innerHTML = icon(symbol) + `<span>${title}</span>`;
      button.onclick = () => showSettingsSection(id);
      $("nav", modal).append(button);
    }
    $("header button", modal).onclick = closeSettings;
    modal.addEventListener("cancel", (event) => {
      event.preventDefault();
      closeSettings();
    });
    modal.addEventListener("click", (event) => {
      if (event.target === modal) closeSettings();
    });
    $("input[type=search]", modal).oninput = (event) => {
      const query = event.target.value.trim().toLowerCase();
      for (const [id, title, , keywords] of settingsSections)
        modal.querySelector(`[data-settings-section=${id}]`).hidden = Boolean(
          query && !`${title} ${keywords}`.toLowerCase().includes(query),
        );
    };
    document.body.append(modal);
    productUI.settings = modal;
  }
  if (!productUI.settings.open) productUI.settings.showModal();
  await showSettingsSection(productUI.section);
}

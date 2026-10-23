/* Оболочка приложения: история переходов, карточки правой панели, рост окна под панели,
   аватар аккаунта и предзагрузка настроек. Загружается последним и оборачивает глобальные функции. */
document.addEventListener('DOMContentLoaded', () => {
  const root = document.documentElement;
  const shell = () => document.getElementById('shell');
  const text = (value) => String(value ?? '');

  /* история переходов: назад и вперёд */
  const nav = { entries: [], index: -1, restoring: false };
  const sameEntry = (a, b) => a && b && a.scope === b.scope && a.page === b.page && a.chatId === b.chatId && a.projectId === b.projectId;
  function currentEntry() {
    return {
      scope: typeof dokkyState === 'object' ? dokkyState.scope : 'development',
      page: state.page,
      chatId: state.page === 'chat' ? (state.chat?.id ?? null) : null,
      projectId: state.page === 'chat' ? (state.project?.id ?? null) : null,
    };
  }
  function syncNavButtons() {
    const back = document.getElementById('nav-back'), forward = document.getElementById('nav-forward');
    if (back) back.disabled = nav.index <= 0;
    if (forward) forward.disabled = nav.index >= nav.entries.length - 1;
  }
  function recordNavigation() {
    if (nav.restoring) return;
    const entry = currentEntry();
    if (sameEntry(nav.entries[nav.index], entry)) {
      nav.entries[nav.index] = entry;
      return;
    }
    nav.entries.splice(nav.index + 1);
    nav.entries.push(entry);
    if (nav.entries.length > 80) nav.entries.shift();
    nav.index = nav.entries.length - 1;
    syncNavButtons();
  }
  async function openEntry(entry) {
    nav.restoring = true;
    try {
      if (entry.scope && dokkyState.scope !== entry.scope) await switchDokkyScope(entry.scope);
      if (entry.page !== 'chat') {
        await page(entry.page);
        return true;
      }
      if (entry.chatId == null) {
        newChat(state.projects.find((item) => item.id === entry.projectId) || null);
        return true;
      }
      const list = entry.scope === 'development' ? state.chats : dokkyState.chats;
      const chat = list.find((item) => item.id === entry.chatId);
      if (!chat) return false;
      await openChat(chat);
      return true;
    } finally {
      nav.restoring = false;
    }
  }
  async function step(direction) {
    while (true) {
      const target = nav.index + direction;
      if (target < 0 || target >= nav.entries.length) break;
      nav.index = target;
      syncNavButtons();
      if (await openEntry(nav.entries[target])) return;
      // чат удалили: убираем запись и идём дальше
      nav.entries.splice(target, 1);
      if (direction > 0) nav.index = target - 1;
      syncNavButtons();
    }
    syncNavButtons();
  }
  const baseSwitchView = switchView;
  switchView = function (key) {
    const result = baseSwitchView.apply(this, arguments);
    resetPanelCards();
    if (!nav.restoring) queueMicrotask(recordNavigation);
    return result;
  };

  /* карточки правой панели */
  const overviewOpen = { environment: true, context: true };
  const cardsRoot = () => document.getElementById('inspector-content');
  function applyFocus(key) {
    const container = cardsRoot();
    if (!container) return;
    container.dataset.focus = key || '';
    for (const card of container.querySelectorAll('.inspector-card')) {
      const name = card.dataset.card;
      const focusCard = card.classList.contains('focus-card');
      const open = focusCard ? name === key : overviewOpen[name] !== false;
      card.classList.toggle('open', open);
      card.querySelector('.card-head')?.setAttribute('aria-expanded', String(open));
      const body = card.querySelector('.card-body');
      if (body) body.inert = !open;
      card.inert = !focusCard && Boolean(key);
    }
  }
  selectInspector = async function (key) {
    state.inspector = key;
    setPanel('right', false, false);
    document.getElementById('turn').hidden = false;
    document.getElementById('file-tree').hidden = false;
    document.getElementById('editor-view').hidden = !state.editor;
    applyFocus(key);
    if (key === 'files') await loadFiles();
  };
  function collapseFocus() {
    state.inspector = 'diff';
    applyFocus('');
  }
  function bindCards() {
    const container = cardsRoot();
    container.addEventListener('click', (event) => {
      const head = event.target.closest('.card-head');
      if (!head) return;
      event.stopPropagation();
      const card = head.closest('.inspector-card');
      if (card.classList.contains('focus-card')) {
        if (card.classList.contains('open')) collapseFocus();
        else void guarded(() => selectInspector(card.dataset.card))();
        return;
      }
      overviewOpen[card.dataset.card] = !card.classList.contains('open');
      applyFocus(container.dataset.focus);
    }, true);
    applyFocus('');
  }

  const baseUpdateEnvironment = updateEnvironment;
  updateEnvironment = function () {
    const result = baseUpdateEnvironment.apply(this, arguments);
    updateContextCard();
    return result;
  };

  const baseRenderChanges = renderChangeInspector;
  renderChangeInspector = function (changes) {
    const result = baseRenderChanges.apply(this, arguments);
    const turn = document.getElementById('turn');
    turn.hidden = false;
    const empty = !state.changes.length;
    turn.querySelector('.actions').hidden = empty;
    document.getElementById('diff').hidden = empty;
    const meta = document.getElementById('changes-count');
    if (empty) {
      meta.textContent = '';
      return result;
    }
    try {
      const files = state.changes.flatMap((change) => changeDiffModel(change));
      const totals = diffStats(files);
      meta.innerHTML = `${files.length || state.changes.length} · <span class="positive">+${totals.add}</span> <span class="danger">−${totals.remove}</span>`;
    } catch {
      meta.textContent = String(state.changes.length);
    }
    return result;
  };

  const baseRenderPlan = renderPlan;
  renderPlan = function (steps) {
    const result = baseRenderPlan.apply(this, arguments);
    const list = Array.isArray(steps) ? steps : [];
    const done = list.filter((item) => item.status === 'completed').length;
    document.getElementById('plan-count').textContent = list.length ? `${done}/${list.length}` : '';
    return result;
  };
  function resetPanelCards() {
    const count = document.getElementById('plan-count');
    if (count) count.textContent = '';
    updateContextCard();
  }

  const finite = (...values) => values.find((value) => Number.isFinite(Number(value)) && Number(value) > 0);
  const numberText = (value) => Number(value).toLocaleString('ru-RU');
  function updateContextCard() {
    const meter = document.querySelector('.context-meter');
    if (!meter) return;
    const usage = (state.latestUsage || state.codingSession?.last_usage)?.usage || {};
    const model = (state.models || []).find((item) => item.tag === (state.chat?.model || state.selectedModel));
    const used = finite(usage.context_tokens, usage.prompt_tokens, usage.input_tokens);
    // окно у каждого режима своё: Light, Pro и Max сжимаются на разных порогах
    const mode = state.codingSession?.pending_reasoning_mode || state.codingSession?.reasoning_mode || state.settings.coding_reasoning_mode;
    const modeWindow = model?.reasoning_modes?.find((item) => item.id === mode)?.compact_at_tokens;
    const limit = finite(modeWindow, usage.context_limit, model?.context_tokens, model?.context_window);
    const percentNode = document.getElementById('context-percent');
    const detail = document.getElementById('context-detail');
    if (!used) {
      percentNode.textContent = '—';
      detail.textContent = 'Данных об окне контекста пока нет.';
      meter.firstElementChild.style.width = '0%';
      meter.setAttribute('aria-valuenow', '0');
      delete meter.dataset.level;
      return;
    }
    if (!limit) {
      percentNode.textContent = '—';
      detail.textContent = `Использовано ${numberText(used)} токенов.`;
      meter.firstElementChild.style.width = '0%';
      return;
    }
    const percent = Math.min(100, Math.round((Number(used) / Number(limit)) * 100));
    percentNode.textContent = `${percent}%`;
    detail.textContent = `Использовано ${numberText(used)} из ${numberText(limit)} токенов. При заполнении Докки сожмёт историю сам.`;
    meter.firstElementChild.style.width = `${percent}%`;
    meter.setAttribute('aria-valuenow', String(percent));
    meter.dataset.level = percent >= 90 ? 'high' : percent >= 75 ? 'warn' : '';
  }
  setInterval(() => {
    if (!shell()?.classList.contains('right-collapsed')) updateContextCard();
  }, 2000);

  /* панели расширяют окно, а не сжимают чат */
  // окно растёт только когда колонке чата реально не хватает места, плавно и в такт с панелью;
  // при закрытии панели окно не уменьшается
  let ready = false;
  const baseSetPanel = setPanel;
  const adjust = (side, delta, ms) => {
    const bridge = window.koddiWindowBridge;
    if (!bridge?.adjustWidth) return;
    try { bridge.adjustWidth(side, Math.round(delta), Math.round(ms), () => {}); } catch { /* окно без моста */ }
  };
  const cssWidth = (name, suffix) => {
    const value = parseFloat(getComputedStyle(shell()).getPropertyValue(`--${name === 'left' ? 'lw' : 'rw'}${suffix}`));
    return Number.isFinite(value) ? value : 0;
  };
  const minMain = () => Math.min((parseFloat(getComputedStyle(root).getPropertyValue('--chat-width')) || 760) + 56, 760);
  const duration = () => (root.dataset.motion === 'reduced' ? 0 : 380);
  setPanel = function (name, hidden, persist = true) {
    if (ready && !hidden && (name === 'left' || name === 'right') && shell().classList.contains(`${name}-collapsed`)) {
      const panel = cssWidth(name, '-open');
      const other = cssWidth(name === 'left' ? 'right' : 'left', '');
      const missing = minMain() - (window.innerWidth - panel - other);
      if (panel && missing > 0) adjust('right', Math.min(panel, Math.ceil(missing)), duration());
    }
    return baseSetPanel(name, hidden, persist);
  };

  // при изменении размера окна ширины панелей пересчитываются сразу: без анимации и без рассинхрона с содержимым
  let resizeTimer = 0;
  window.addEventListener('resize', () => {
    shell()?.classList.add('shell-snap');
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => shell()?.classList.remove('shell-snap'), 160);
  });

  /* кольцо фокуса только при работе с клавиатуры */
  document.addEventListener('keydown', (event) => {
    if (['Tab', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) root.dataset.input = 'keyboard';
  }, true);
  document.addEventListener('pointerdown', () => { delete root.dataset.input; }, true);

  /* колесо над полосой перетаскивания листает чат, как над прозрачным краем */
  document.addEventListener('wheel', (event) => {
    if (!event.target.closest?.('.desktop-drag-strip')) return;
    const feed = document.getElementById('feed');
    if (event.deltaY < 0) state.followFeed = false;
    feed.scrollTop += event.deltaY;
  }, { passive: true, capture: true });

  /* сайдбар: время чатов, аккаунт, шестерёнка */
  const relative = (value) => {
    const moment = Date.parse(value);
    if (!Number.isFinite(moment)) return '';
    const minutes = Math.max(0, Math.round((Date.now() - moment) / 60000));
    if (minutes < 1) return 'сейчас';
    if (minutes < 60) return `${minutes} мин`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `${hours} ч`;
    const days = Math.round(hours / 24);
    if (days < 7) return `${days} д`;
    if (days < 30) return `${Math.round(days / 7)} нед`;
    return `${Math.round(days / 30)} мес`;
  };
  const baseChatRow = chatSidebarRow;
  chatSidebarRow = function (chat) {
    const row = baseChatRow.apply(this, arguments);
    const label = relative(chat.updated_at || chat.created_at);
    if (label) {
      const stamp = document.createElement('time');
      stamp.className = 'chat-time';
      stamp.textContent = label;
      row.insertBefore(stamp, row.lastElementChild);
    }
    return row;
  };

  const avatar = { key: '', url: '', tried: '' };
  function showAvatar() {
    const dot = document.querySelector('#connection-badge .connection-dot');
    if (!dot || !avatar.url) return;
    if (dot.firstElementChild?.tagName === 'IMG') return;
    dot.textContent = '';
    const image = new Image();
    image.alt = '';
    image.src = avatar.url;
    image.draggable = false;
    dot.append(image);
  }
  async function loadAvatar() {
    const account = dokkyState.account;
    if (!account?.authenticated) return;
    const key = text(account.user?.id ?? account.user?.display_name);
    if (avatar.tried === key) return showAvatar();
    avatar.tried = key;
    avatar.url = '';
    try {
      const response = await fetch(cloudPath('me/avatar'));
      if (!response.ok) return;
      const blob = await response.blob();
      if (!blob.type.startsWith('image/') || blob.size > 1_500_000) return;
      const url = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
      if (avatar.tried !== key) return;
      avatar.url = url;
      showAvatar();
    } catch {
      /* без аватарки остаётся буква имени */
    }
  }
  function openAccountSettings() {
    productUI.section = 'account';
    return openSettings();
  }

  /* предзагрузка настроек */
  const warm = new Map();
  const warmTtl = new Map([
    ['/api/dokky/cloud/preferences', 300_000],
    ['/api/dokky/cloud/subscription', 20_000],
    ['/api/app/diagnostics', 600_000],
  ]);
  const baseApi = api;
  api = function (path, options = {}) {
    const method = text(options.method || 'GET').toUpperCase();
    const ttl = warmTtl.get(path);
    if (method === 'GET' && ttl) {
      const hit = warm.get(path);
      if (hit && Date.now() - hit.at < ttl) return Promise.resolve(structuredClone(hit.value));
      return baseApi.apply(this, arguments).then((value) => {
        warm.set(path, { value: structuredClone(value), at: Date.now() });
        return value;
      });
    }
    if (method !== 'GET' && warmTtl.has(path)) warm.delete(path);
    return baseApi.apply(this, arguments);
  };
  let prewarmed = false;
  function prewarmSettings() {
    if (prewarmed || !dokkyState.account?.authenticated || !productUI) return;
    prewarmed = true;
    const run = async () => {
      try {
        void api('/api/app/diagnostics').catch(() => {});
        void api(cloudPath('preferences')).catch(() => {});
        void loadLimits();
        if (!productUI.settings) {
          const section = productUI.section;
          productUI.section = 'general';
          void openSettings();
          productUI.settings?.close();
          productUI.section = section;
        }
      } catch {
        prewarmed = false;
      }
    };
    if ('requestIdleCallback' in window) requestIdleCallback(run, { timeout: 4000 });
    else setTimeout(run, 1500);
  }
  window.koddiPrewarmSettings = prewarmSettings;

  const baseChrome = dokkyChrome;
  dokkyChrome = function () {
    const result = baseChrome.apply(this, arguments);
    showAvatar();
    void loadAvatar();
    prewarmSettings();
    return result;
  };

  /* поиск по чатам: поле выезжает из строки «Поиск», как на сайте */
  function bindSearch() {
    const row = document.getElementById('chat-search-row');
    const input = document.getElementById('chat-search-input');
    const open = document.getElementById('chat-search');
    const close = document.getElementById('chat-search-close');
    const toggle = (value) => {
      row.classList.toggle('open', value);
      open.setAttribute('aria-expanded', String(value));
      input.tabIndex = close.tabIndex = value ? 0 : -1;
    };
    const finish = () => {
      input.value = '';
      toggle(false);
      renderSidebar('');
      open.blur();
    };
    open.onclick = () => {
      toggle(true);
      input.focus({ preventScroll: true });
    };
    close.onclick = finish;
    input.addEventListener('input', () => renderSidebar(input.value.trim()));
    input.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      finish();
    });
    input.addEventListener('blur', () => {
      if (input.value.trim()) return;
      setTimeout(() => { if (!row.contains(document.activeElement)) toggle(false); }, 120);
    });
  }

  function bindShell() {
    document.getElementById('nav-back').onclick = guarded(() => step(-1));
    document.getElementById('nav-forward').onclick = guarded(() => step(1));
    const row = document.querySelector('.account-row');
    row.addEventListener('click', (event) => {
      const badge = event.target.closest('#connection-badge');
      const toggle = event.target.closest('#account-menu-toggle');
      const gear = event.target.closest('#sidebar-settings');
      if (!badge && !toggle && !gear) return;
      event.stopPropagation();
      if (badge) void guarded(openAccountSettings)();
      else if (toggle) showAccountMenu();
      else {
        productUI.section = 'general';
        void guarded(openSettings)();
      }
    }, true);
    bindCards();
    bindSearch();
    syncNavButtons();
  }
  bindShell();
  updateContextCard();
  startup.finally(() => setTimeout(() => {
    ready = true;
    recordNavigation();
  }, 700));
  window.koddiShell = { goBack: () => step(-1), goForward: () => step(1), history: nav };
  root.dataset.shell = 'cards';
});

/* Поверхность поиска Докки: разметка и состояния повторяют mini_app/assets/ai-search.js,
   данные приходят из API v1 (чат режима search): статус, источники, поток ответа. */
const cloudSearch = {
  surface: null,
  busy: false,
  token: 0,
  chat: null,
  run: null,
  query: "",
  answer: "",
  sources: new Map(),
  cursor: "0",
  history: [],
};
const csEl = {};
const csMake = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

function initCloudSearch() {
  if (cloudSearch.surface) return;
  const surface = csMake("section", "ai-search-surface");
  surface.id = "ai-search-surface";
  surface.hidden = true;
  surface.setAttribute("role", "tabpanel");
  surface.setAttribute("aria-labelledby", "product-search");
  surface.innerHTML = `
    <div class="ai-search-start">
      <div class="ai-search-intro">
        <img class="ai-search-logo" src="assets/mark.png" alt="Докки">
        <h1>Что хотите найти?</h1>
        <p>Проверим источники и найдём ответ.</p>
      </div>
      <form class="composer ai-search-form" role="search">
        <div class="ai-search-line">
          <textarea id="ai-search-query" rows="1" maxlength="400" placeholder="Что вас интересует?" aria-label="Поисковый запрос" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="ai-search-suggestions"></textarea>
          <button class="send-button" type="submit" aria-label="Найти" disabled></button>
        </div>
        <div class="ai-suggestions-wrap" inert><div class="ai-suggestions" id="ai-search-suggestions" role="listbox" aria-label="Недавние запросы"></div></div>
      </form>
    </div>
    <div class="ai-search-output" hidden>
      <div class="ai-response-column">
        <div class="ai-response-pane">
          <div class="ai-response-heading"><img class="ai-response-logo" src="assets/mark.png" alt=""><span>Докки</span></div>
          <div class="ai-search-status" role="status" aria-live="polite" hidden><span class="ai-search-progress" aria-hidden="true"></span><span class="ai-search-status-copy"></span><span class="ai-reading-sources" aria-hidden="true"></span></div>
          <article class="ai-search-answer"><div class="message-body"></div></article>
          <section class="ai-search-source-cards" hidden></section>
        </div>
        <button class="ai-search-followup" type="button" hidden>Продолжить диалог</button>
      </div>
      <div class="ai-results-pane">
        <div class="ai-results-heading" hidden><h2>Источники и результаты</h2></div>
        <div class="ai-search-results"></div>
      </div>
    </div>`;
  document.querySelector("main").append(surface);
  Object.assign(csEl, {
    form: $("form", surface),
    input: $("textarea", surface),
    submit: $(".send-button", surface),
    output: $(".ai-search-output", surface),
    status: $(".ai-search-status", surface),
    statusCopy: $(".ai-search-status-copy", surface),
    reading: $(".ai-reading-sources", surface),
    answer: $(".ai-search-answer", surface),
    answerBody: $(".ai-search-answer .message-body", surface),
    cards: $(".ai-search-source-cards", surface),
    results: $(".ai-search-results", surface),
    heading: $(".ai-results-heading", surface),
    followup: $(".ai-search-followup", surface),
    suggestions: $(".ai-suggestions", surface),
    wrap: $(".ai-suggestions-wrap", surface),
  });
  cloudSearch.surface = surface;
  setSearchSubmit("search");
  const sync = () => {
    csEl.input.style.height = "auto";
    csEl.input.style.height = `${Math.min(150, csEl.input.scrollHeight)}px`;
    csEl.submit.disabled = !cloudSearch.busy && !csEl.input.value.trim();
    csEl.submit.classList.toggle("ready", cloudSearch.busy || Boolean(csEl.input.value.trim()));
    drawSuggestions();
  };
  csEl.input.addEventListener("input", sync);
  csEl.input.addEventListener("focus", () => {
    void loadSearchHistory().then(drawSuggestions);
    drawSuggestions();
  });
  csEl.input.addEventListener("blur", () => setTimeout(closeSuggestions, 120));
  csEl.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      csEl.form.requestSubmit();
    }
    if (event.key === "Escape") closeSuggestions();
  });
  csEl.form.addEventListener("submit", (event) => {
    event.preventDefault();
    closeSuggestions();
    if (cloudSearch.busy) return void stopSearch();
    void guarded(() => runSearch(csEl.input.value))();
  });
  csEl.followup.onclick = () => {
    const query = cloudSearch.query;
    guarded(async () => {
      await switchDokkyScope("chat");
      $("#prompt").value = `Продолжим по поиску «${query}»: `;
      autoSize();
      $("#prompt").focus();
    })();
  };
}

function setSearchSubmit(kind) {
  const stop = kind === "stop";
  csEl.submit.innerHTML = `${icon(stop ? "stop" : "search")}<span class="ai-search-submit-label">${stop ? "Стоп" : "Найти"}</span>`;
  csEl.submit.setAttribute("aria-label", stop ? "Остановить поиск" : "Найти");
  csEl.submit.disabled = !stop && !csEl.input.value.trim();
  csEl.submit.classList.toggle("busy", stop);
}

function syncSearchSurface(active) {
  initCloudSearch();
  const main = document.querySelector("main");
  main.classList.toggle("ai-search-active", active);
  cloudSearch.surface.hidden = !active;
  cloudSearch.surface.inert = !active;
  for (const node of [$("#feed"), $("#composer")]) node.inert = active;
}

function searchStatus(text, sources = false) {
  csEl.status.hidden = !text;
  csEl.statusCopy.textContent = text || "";
  if (!sources) csEl.reading.replaceChildren();
}

function searchSourceInfo(raw) {
  const source = raw?.source || raw || {};
  const url = String(source.url || "");
  if (!/^https?:\/\//i.test(url)) return null;
  return { id: source.id || url, url, title: String(source.title || ""), snippet: String(source.snippet || "") };
}

function addSearchResult(source) {
  if (cloudSearch.sources.has(source.url)) return;
  cloudSearch.sources.set(source.url, source);
  csEl.heading.hidden = false;
  const card = csMake("article", "ai-search-result");
  card.style.animationDelay = `${Math.min(cloudSearch.sources.size - 1, 9) * 35}ms`;
  const copy = csMake("div", "ai-search-result-copy");
  const meta = csMake("div", "ai-search-result-meta");
  meta.append(sourceIcon(source), csMake("span", "", sourceDomain(source)));
  const title = sourceLink(source, source.title || sourceDomain(source), "ai-search-result-title");
  let path = "";
  try {
    path = decodeURIComponent(new URL(source.url).pathname)
      .split("/")
      .filter((part) => part && !/^[a-f0-9]{16,}$/i.test(part))
      .slice(0, 3)
      .map((part) => (part.length > 60 ? part.slice(0, 57) + "…" : part))
      .join(" › ");
  } catch {}
  copy.append(meta, title || csMake("span", "ai-search-result-title", source.title), csMake("span", "ai-search-result-url", path));
  if (source.snippet) copy.append(csMake("p", "", source.snippet.replace(/\s+/g, " ").trim()));
  card.append(copy);
  csEl.results.append(card);
  card.addEventListener("animationend", () => (card.style.animation = "none"), { once: true });
}

function addReadingSource(source) {
  const icons = csEl.reading;
  const node = sourceIcon(source);
  icons.append(node);
  while (icons.children.length > 4) icons.firstElementChild.remove();
}

function resetSearchOutput() {
  cloudSearch.answer = "";
  cloudSearch.sources = new Map();
  cancelCloudStream(csEl.answerBody);
  csEl.answerBody.replaceChildren();
  csEl.results.replaceChildren();
  csEl.cards.replaceChildren();
  csEl.cards.hidden = true;
  csEl.heading.hidden = true;
  csEl.followup.hidden = true;
  csEl.reading.replaceChildren();
  csEl.output.hidden = false;
  cloudSearch.surface.classList.remove("has-ai-answer", "no-summary");
  cloudSearch.surface.classList.add("has-query");
  $(".ai-search-intro", cloudSearch.surface).hidden = true;
  cloudSearch.surface.scrollTop = 0;
  for (const error of cloudSearch.surface.querySelectorAll(".ai-search-error")) error.remove();
}

function searchError(message) {
  searchStatus("");
  const node = csMake("p", "ai-search-error", message);
  csEl.answer.before(node);
}

async function readSearchEvents(chatId, runId, token) {
  for (let attempt = 0; attempt < 40 && token === cloudSearch.token; attempt++) {
    const controller = (cloudSearch.controller = new AbortController());
    let terminal = false;
    try {
      const response = await fetch(
        cloudPath(`runs/${encodeURIComponent(runId)}/events?after=${encodeURIComponent(cloudSearch.cursor)}`),
        { signal: controller.signal },
      );
      if (response.status === 401) return void window.koddiAuthRevoked?.();
      if (!response.ok) throw new Error("Не удалось получить ответ поиска");
      const reader = response.body.getReader(), decoder = new TextDecoder();
      let buffer = "";
      while (token === cloudSearch.token) {
        const packet = await reader.read();
        if (packet.done) break;
        buffer += decoder.decode(packet.value, { stream: true });
        const blocks = buffer.split(/\r?\n\r?\n/);
        buffer = blocks.pop();
        for (const block of blocks) {
          const dataLine = block.split(/\r?\n/).filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
          if (!dataLine) continue;
          cloudSearch.cursor = (block.match(/^id:\s*(.*)$/m) || [])[1] || cloudSearch.cursor;
          const kind = (block.match(/^event:\s*(.*)$/m) || [])[1], data = JSON.parse(dataLine);
          if (handleSearchEvent(kind, data)) terminal = true;
        }
        if (terminal) break;
      }
      await reader.cancel().catch(() => {});
    } catch (error) {
      if (error.name === "AbortError") return;
      if (attempt > 3) throw error;
    }
    if (terminal) return;
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
}

function handleSearchEvent(kind, data) {
  if (kind === "search.started") searchStatus("Ищу информацию…");
  else if (kind === "search.source") {
    const source = searchSourceInfo(data);
    if (source) {
      addSearchResult(source);
      addReadingSource(source);
      searchStatus(`Изучаю источник: ${sourceDomain(source)}`, true);
    }
  } else if (kind === "activity.updated") {
    const activity = data.activity || {};
    if (activity.text && !["title", "commentary"].includes(activity.kind)) searchStatus(activity.text, true);
  } else if (kind === "message.delta" && typeof data.content === "string") {
    if (data.replace) cloudSearch.answer = data.content;
    else if (Number.isInteger(data.offset)) cloudSearch.answer = cloudSearch.answer.slice(0, data.offset) + data.content;
    else cloudSearch.answer += data.content;
    if (!cloudSearch.surface.classList.contains("has-ai-answer")) {
      cloudSearch.surface.classList.add("has-ai-answer");
      searchStatus("");
    }
    renderCloudStreamMarkdown(csEl.answerBody, cloudSearch.answer);
  } else if (kind === "auth.revoked") window.koddiAuthRevoked?.();
  else if (kind === "turn.failed") {
    searchError(data.error?.message || data.error || "Поиск завершился с ошибкой");
    return true;
  } else if (["turn.completed", "turn.cancelled"].includes(kind)) return true;
  return false;
}

// итоговый ответ сервера: разметка, ссылки на источники и список источников
async function finishSearch(chatId, token) {
  const data = await api(cloudPath(`chats/${encodeURIComponent(chatId)}/messages?limit=10`));
  if (token !== cloudSearch.token) return;
  const message = [...data.items].reverse().find((item) => item.role === "assistant" && item.content);
  searchStatus("");
  if (!message) return;
  showSearchMessage(message);
}

function showSearchMessage(message) {
  cancelCloudStream(csEl.answerBody);
  cloudSearch.answer = message.content;
  cloudSearch.surface.classList.add("has-ai-answer");
  const draft = document.createElement("div");
  renderMarkdown(draft, message.content, true, { message });
  if (csEl.answerBody.firstChild) reconcileStreamNodes(csEl.answerBody, draft);
  else csEl.answerBody.replaceChildren(...draft.childNodes);
  attachCloudCitations(csEl.answerBody, message);
  const citations = (message.citations || []).map(searchSourceInfo).filter(Boolean);
  for (const source of citations) addSearchResult(source);
  csEl.cards.replaceChildren();
  renderCloudSources(csEl.cards, message.citations);
  csEl.cards.hidden = !csEl.cards.children.length;
  csEl.followup.hidden = false;
}

async function runSearch(rawQuery) {
  const query = String(rawQuery || "").trim();
  if (!query || cloudSearch.busy) return;
  if (!dokkyState.account?.authenticated) return dokkyLogin();
  const token = ++cloudSearch.token;
  Object.assign(cloudSearch, { busy: true, query, cursor: "0", run: null, chat: null });
  csEl.input.value = query;
  resetSearchOutput();
  setSearchSubmit("stop");
  searchStatus("Ищу информацию…");
  try {
    const chat = await api(cloudPath("chats"), {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ mode: "search", title: query.slice(0, 80), model: $("#model").value || undefined }),
    });
    cloudSearch.chat = chat;
    if (token !== cloudSearch.token) return;
    const result = await api(cloudPath(`chats/${encodeURIComponent(chat.id)}/messages`), {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({
        content: query,
        mode: "search",
        model: $("#model").value || undefined,
        attachment_ids: [],
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        client_message_id: crypto.randomUUID(),
      }),
    });
    cloudSearch.run = result.run_id;
    await readSearchEvents(chat.id, result.run_id, token);
    if (token === cloudSearch.token) await finishSearch(chat.id, token);
    cloudSearch.history = [];
  } catch (error) {
    if (token === cloudSearch.token && error.name !== "AbortError") searchError(error.message);
  } finally {
    if (token === cloudSearch.token) {
      cloudSearch.busy = false;
      setSearchSubmit("search");
      searchStatus("");
      void cloudRefresh?.();
    }
  }
}

async function stopSearch() {
  const run = cloudSearch.run;
  cloudSearch.controller?.abort();
  if (run) await post(cloudPath(`runs/${encodeURIComponent(run)}/cancel`)).catch(() => {});
  cloudSearch.token++;
  cloudSearch.busy = false;
  setSearchSubmit("search");
  searchStatus("");
  if (cloudSearch.chat) void finishSearch(cloudSearch.chat.id, cloudSearch.token).catch(() => {});
}

async function loadSearchHistory() {
  if (cloudSearch.history.length || !dokkyState.account?.authenticated) return;
  try {
    const data = await api(cloudPath("chats?mode=search&limit=12"));
    cloudSearch.history = data.items || [];
  } catch {}
}

function closeSuggestions() {
  csEl.wrap.inert = true;
  csEl.form.classList.remove("suggestions-open");
  csEl.input.setAttribute("aria-expanded", "false");
}

function drawSuggestions() {
  if (document.activeElement !== csEl.input) return;
  const text = csEl.input.value.trim().toLocaleLowerCase("ru-RU");
  const items = cloudSearch.history
    .filter((chat) => chat.title && (!text || chat.title.toLocaleLowerCase("ru-RU").includes(text)))
    .slice(0, 6);
  csEl.suggestions.replaceChildren(
    ...items.map((chat) => {
      const row = csMake("div", "ai-suggestion-row");
      row.setAttribute("role", "option");
      const pick = csMake("button", "ai-suggestion-pick");
      pick.type = "button";
      pick.tabIndex = -1;
      pick.innerHTML = icon("Clock");
      pick.append(csMake("span", "", chat.title));
      pick.addEventListener("pointerdown", (event) => event.preventDefault());
      pick.onclick = guarded(() => openStoredSearch(chat));
      row.append(pick);
      return row;
    }),
  );
  const open = items.length > 0;
  csEl.wrap.inert = !open;
  csEl.form.classList.toggle("suggestions-open", open);
  csEl.input.setAttribute("aria-expanded", String(open));
  if (open) csEl.form.style.setProperty("--ai-popup-height", `${csEl.suggestions.scrollHeight + 8}px`);
}

// сохранённый поиск открывается без нового запроса
async function openStoredSearch(chat) {
  closeSuggestions();
  const token = ++cloudSearch.token;
  Object.assign(cloudSearch, { busy: false, query: chat.title, chat });
  csEl.input.value = chat.title;
  resetSearchOutput();
  setSearchSubmit("search");
  searchStatus("Открываю результаты…");
  const data = await api(cloudPath(`chats/${encodeURIComponent(chat.id)}/messages?limit=10`));
  if (token !== cloudSearch.token) return;
  searchStatus("");
  const user = data.items.find((item) => item.role === "user");
  if (user?.content) {
    csEl.input.value = user.content;
    cloudSearch.query = user.content;
  }
  const message = [...data.items].reverse().find((item) => item.role === "assistant" && item.content);
  if (message) showSearchMessage(message);
  else searchError("В этом поиске нет сохранённого ответа");
}

function resetCloudSearch() {
  if (!cloudSearch.surface) return;
  cloudSearch.token++;
  cloudSearch.controller?.abort();
  Object.assign(cloudSearch, { busy: false, chat: null, run: null, query: "", history: [] });
  csEl.input.value = "";
  csEl.output.hidden = true;
  cloudSearch.surface.classList.remove("has-query", "has-ai-answer");
  $(".ai-search-intro", cloudSearch.surface).hidden = false;
  csEl.answerBody.replaceChildren();
  csEl.results.replaceChildren();
  searchStatus("");
  setSearchSubmit("search");
}

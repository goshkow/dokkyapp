/* из фронтенда Докки, mini_app/assets/work-progress.js */
(() => {
  "use strict";
  const controllers = new Set(),
    epochs = new Map();
  let panelSequence = 0;
  const storageKey = (user) => `dokky-work-progress-v1:${user}`;
  const reduced = () =>
    document.documentElement.dataset.motion === "reduced" ||
    matchMedia("(prefers-reduced-motion: reduce)").matches;
  function changeStatus(status, text) {
    const label = status?.querySelector("span:last-child");
    if (!label || label.textContent === text) return;
    for (const animation of label.getAnimations()) animation.cancel();
    status
      .querySelectorAll(".status-outgoing")
      .forEach((node) => node.remove());
    if (reduced() || !status.isConnected) {
      label.textContent = text;
      return;
    }
    const old = document.createElement("div");
    old.className = "status-outgoing";
    old.textContent = label.textContent;
    Object.assign(old.style, {
      position: "absolute",
      left: label.offsetLeft + "px",
      top: label.offsetTop + "px",
      pointerEvents: "none",
    });
    old.setAttribute("aria-hidden", "true");
    status.style.position = "relative";
    status.insertBefore(old, label);
    label.textContent = text;
    old
      .animate(
        [
          { opacity: 1, transform: "translateY(0)" },
          { opacity: 0, transform: "translateY(-8px)" },
        ],
        { duration: 240, fill: "forwards", easing: "ease-in" },
      )
      .finished.catch(() => {})
      .then(() => old.remove());
    label.animate(
      [
        { opacity: 0, transform: "translateY(8px)" },
        { opacity: 1, transform: "translateY(0)" },
      ],
      { duration: 340, easing: "cubic-bezier(.22,1,.36,1)" },
    );
  }
  const past = {
    understand: "Разобрал запрос",
    plan: "Составил план",
    routing: "Выбрал способ обработки",
    inspect_attachments: "Изучал вложения",
    attachments: "Изучал вложения",
    local_rag: "Проверял сохранённую информацию",
    web_search: "Искал в интернете",
    relevance: "Сопоставлял источники",
    page_reading: "Изучал страницы",
    fact_check: "Проверял факты",
    additional_search: "Искал дополнительные источники",
    general_search: "Расширял поиск",
    deep_search: "Углублял поиск",
    deep_reading: "Изучал дополнительные страницы",
    synthesis: "Готовил ответ",
    citation_verify: "Проверял ссылки",
    export: "Работал над файлом",
    document_assembly: "Собирал документ",
    memory: "Учитывал контекст",
    image: "Создавал изображение",
    video_resolve: "Нашёл видео",
    video_wait: "Проверял сохранённый анализ",
    video_captions: "Получал субтитры",
    video_analysis: "Изучал видео",
    video_transcription: "Распознавал речь",
    video_visuals: "Анализировал визуальные моменты",
    video_digest: "Составлял конспект",
    video_index: "Формировал индекс",
    browser: "Работал с браузером",
  };
  const duration = (milliseconds) => {
    const seconds = Math.max(1, Math.round(milliseconds / 1000));
    return seconds < 60
      ? `${seconds} сек.`
      : `${Math.floor(seconds / 60)} мин. ${seconds % 60} сек.`;
  };
  function read(user) {
    try {
      const value = JSON.parse(
        sessionStorage.getItem(storageKey(user)) || "null",
      );
      return value &&
        Date.now() - value.savedAt < 24 * 60 * 60 * 1000 &&
        Array.isArray(value.entries)
        ? value
        : null;
    } catch {
      return null;
    }
  }
  function remove(root) {
    if (!root.isConnected || reduced()) {
      root.remove();
      return;
    }
    const height = root.getBoundingClientRect().height;
    root.style.overflow = "hidden";
    root
      .animate(
        [
          { height: `${height}px`, opacity: 1 },
          {
            height: "0px",
            marginBottom: "0px",
            opacity: 0,
          },
        ],
        {
          duration: 260,
          easing: "cubic-bezier(.22,1,.36,1)",
          fill: "forwards",
        },
      )
      .finished.catch(() => {})
      .then(() => root.remove());
  }
  function clear(user) {
    epochs.set(String(user), (epochs.get(String(user)) || 0) + 1);
    try {
      sessionStorage.removeItem(storageKey(user));
    } catch {}
    for (const controller of controllers)
      if (controller.user === String(user) && controller.complete) {
        controller.root.dataset.cleared = "true";
        remove(controller.root);
        controllers.delete(controller);
      }
  }
  function create(
    body,
    {
      user,
      status,
      render,
      chevron,
      scope = "chat",
      cached = null,
      answerNow = null,
      privateChat = false,
      startedAt = null,
    } = {},
  ) {
    const root = document.createElement("section");
    root.className = "work-progress";
    root.hidden = true;
    const live = document.createElement("div");
    live.className = "work-progress-live";
    const comments = document.createElement("div");
    comments.className = "work-progress-comments";
    live.append(comments);
    const faster = document.createElement("button");
    faster.type = "button";
    faster.className = "work-progress-faster";
    faster.textContent = "Ответить быстрее";
    faster.hidden = true;
    faster.onclick = async () => {
      if (!answerNow || faster.disabled) return;
      faster.disabled = true;
      try {
        const accepted = await answerNow();
        if (accepted) faster.textContent = "Готовлю ответ…";
        else faster.disabled = false;
      } catch {
        faster.disabled = false;
      }
    };
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "work-progress-toggle";
    toggle.setAttribute("aria-expanded", "false");
    toggle.hidden = true;
    const label = document.createElement("span"),
      arrow = document.createElement("span");
    arrow.className = "work-progress-chevron";
    arrow.innerHTML = chevron || "";
    toggle.append(label, arrow);
    const panel = document.createElement("div");
    panel.className = "work-progress-panel";
    panel.inert = true;
    const history = document.createElement("div");
    history.className = "work-progress-history";
    panel.append(history);
    const panelId = `work-progress-${++panelSequence}`;
    panel.id = panelId;
    toggle.setAttribute("aria-controls", panelId);
    root.append(live, toggle, panel);
    body.prepend(root);
    const controller = { user: String(user), root, complete: false };
    controllers.add(controller);
    const epoch = epochs.get(String(user)) || 0;
    const entries = cached?.entries?.slice(-160) || [];
    const started = cached?.started || startedAt || Date.now();
    let enabled = Boolean(cached),
      answerStarted = false,
      messageId = cached?.messageId || 0,
      elapsed = cached?.elapsed ?? null,
      phaseStart = Date.now(),
      phase = [];
    const persist = () => {
      if (privateChat) return;
      if (
        !controller.complete ||
        !enabled ||
        !messageId ||
        (epochs.get(String(user)) || 0) !== epoch
      )
        return;
      try {
        sessionStorage.setItem(
          storageKey(user),
          JSON.stringify({
            scope,
            messageId,
            entries,
            started,
            elapsed,
            savedAt: Date.now(),
          }),
        );
      } catch {}
    };
    const buildHistory = () => {
      history.replaceChildren();
      for (const entry of entries) {
        const row = document.createElement("div");
        row.className = `work-progress-entry ${entry.type === "commentary" ? "is-commentary" : ""}`;
        if (entry.type === "commentary") render(row, entry.text);
        else {
          row.textContent = past[entry.node] || entry.text;
          row.title = entry.text;
        }
        history.append(row);
      }
    };
    toggle.addEventListener("click", () => {
      const open = toggle.getAttribute("aria-expanded") !== "true";
      if (open) buildHistory();
      toggle.setAttribute("aria-expanded", String(open));
      panel.inert = !open;
      root.classList.toggle("is-expanded", open);
    });
    const record = (type, node, text) => {
      if (!text || controller.complete) return;
      const previous = entries.at(-1);
      if (
        previous?.type === type &&
        previous?.node === node &&
        previous?.text === text
      )
        return;
      entries.push({
        type,
        node,
        text: String(text).slice(0, 2000),
        at: Date.now(),
      });
      if (entries.length > 160) entries.shift();
    };
    const enable = () => {
      enabled = true;
      root.hidden = false;
      if (status && status.parentNode !== live) live.append(status);
      live.append(faster);
    };
    const startAnswer = () => {
      if (!enabled || answerStarted) return;
      answerStarted = true;
      elapsed = Date.now() - started;
      label.textContent = `Работал ${duration(elapsed)}`;
      toggle.hidden = false;
      status?.remove();
      faster.hidden = true;
      const height = live.getBoundingClientRect().height;
      live.inert = true;
      if (reduced() || !root.isConnected) {
        live.hidden = true;
        return;
      }
      live.style.overflow = "hidden";
      live
        .animate(
          [
            {
              height: `${height}px`,
              opacity: 1,
              transform: "scale(1)",
            },
            {
              height: "0px",
              opacity: 0,
              transform: "scale(.98)",
            },
          ],
          {
            duration: 360,
            easing: "cubic-bezier(.22,1,.36,1)",
            fill: "forwards",
          },
        )
        .finished.catch(() => {})
        .then(() => {
          live.hidden = true;
        });
      toggle.animate(
        [
          { opacity: 0, transform: "translateY(-5px)" },
          { opacity: 1, transform: "translateY(0)" },
        ],
        { duration: 300, delay: 100, easing: "ease-out", fill: "both" },
      );
    };
    const api = {
      root,
      enable,
      startAnswer,
      status(node, text) {
        if (
          answerNow &&
          [
            "deep_search",
            "deep_reading",
            "browser",
            "browser_preview",
          ].includes(node) &&
          !answerStarted
        ) {
          enable();
          faster.hidden = false;
          live.append(faster);
        }
        if (node === "browser_preview") {
          let data;
          try {
            data = JSON.parse(text);
          } catch {
            return;
          }
          if (data.clear) {
            live.querySelector(".browser-preview")?.remove();
            return;
          }
          if (
            !/^https?:\/\//.test(data.url || "") ||
            !/^data:image\/jpeg;base64,[A-Za-z0-9+/=]+$/.test(data.image || "")
          )
            return;
          enable();
          let preview = live.querySelector(".browser-preview");
          if (!preview) {
            preview = document.createElement("figure");
            preview.className = "browser-preview";
            const image = document.createElement("img");
            image.alt = "Страница в браузере Докки";
            const caption = document.createElement("figcaption");
            preview.append(image, caption);
            live.append(preview);
          }
          preview.querySelector("img").src = data.image;
          preview.querySelector("figcaption").textContent =
            data.title || data.url || "Браузер";
          live.append(faster);
          return;
        }
        record("status", node, text);
        if (
          !phase.length ||
          phase.at(-1).node !== node ||
          phase.at(-1).text !== text
        )
          phase.push({ node, text });
        if (phase.length > 160) phase.shift();
        changeStatus(status, text);
      },
      commentary(text) {
        enable();
        record("commentary", "commentary", text);
        const before = status?.getBoundingClientRect().top;
        if (comments.children.length && phase.length) {
          const group = document.createElement("details");
          group.className = "work-progress-phase";
          const summary = document.createElement("summary");
          summary.className = "work-progress-toggle";
          const phaseLabel = document.createElement("span");
          phaseLabel.textContent = `Работал ${duration(Date.now() - phaseStart)}`;
          summary.append(phaseLabel, arrow.cloneNode(true));
          const list = document.createElement("div");
          for (const entry of phase) {
            const row = document.createElement("div");
            row.textContent = past[entry.node] || entry.text;
            list.append(row);
          }
          group.append(summary, list);
          comments.append(group);
        }
        phase = [];
        phaseStart = Date.now();
        const paragraph = document.createElement("div");
        paragraph.className = "work-progress-commentary";
        render(paragraph, text);
        comments.append(paragraph);
        while (comments.children.length > 40)
          comments.firstElementChild.remove();
        if (status?.isConnected && before != null && !reduced()) {
          const delta = before - status.getBoundingClientRect().top;
          if (delta)
            status.animate(
              [
                { transform: `translateY(${delta}px)` },
                { transform: "translateY(0)" },
              ],
              { duration: 300, easing: "cubic-bezier(.22,1,.36,1)" },
            );
        }
      },
      snapshot() {
        return {
          expanded: toggle.getAttribute("aria-expanded") === "true",
          entries: entries.map((entry) => ({ ...entry })),
          started,
          elapsed,
          messageId,
          scope,
          savedAt: Date.now(),
        };
      },
      dispose() {
        controllers.delete(controller);
        for (const animation of root.getAnimations({ subtree: true }))
          animation.cancel();
        root.remove();
      },
      bind(id) {
        messageId = String(id || messageId);
        persist();
      },
      finish() {
        if (controller.complete) return;
        if (enabled) {
          startAnswer();
          root.classList.add("is-complete");
          elapsed = Date.now() - started;
          label.textContent = `Работал ${duration(elapsed)}`;
        } else root.remove();
        controller.complete = true;
        persist();
        if (privateChat) controllers.delete(controller);
      },
    };
    if (cached) {
      root.hidden = false;
      live.hidden = true;
      toggle.hidden = false;
      label.textContent = Number.isFinite(elapsed)
        ? `Работал ${duration(elapsed)}`
        : "Время работы не сохранено";
      root.classList.add("is-complete");
      controller.complete = true;
      answerStarted = true;
      if (cached.expanded) {
        buildHistory();
        toggle.setAttribute("aria-expanded", "true");
        panel.inert = false;
        root.classList.add("is-expanded");
      }
    }
    return api;
  }
  function restore(body, user, messageId, options = {}) {
    const cached = read(user);
    if (
      cached &&
      cached.scope === (options.scope || "chat") &&
      String(cached.messageId) === String(messageId) &&
      !body.querySelector(":scope > .work-progress")
    )
      return create(body, { ...options, user, cached });
    return null;
  }
  window.DokkyWorkProgress = { create, restore, clear, duration };
})();

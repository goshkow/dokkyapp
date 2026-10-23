/* Приветствие и меню «+» для чата как на сайте Докки (mini_app/assets/app.js). */
const welcomeLines = [
  "Привет, {name}! Чем займёмся?",
  "Рад тебя видеть, {name}. Что обсудим?",
  "С чего начнём сегодня, {name}?",
  "Что для тебя сделать, {name}?",
  "Привет, {name}! Я на связи.",
  "Какая задача у нас сегодня, {name}?",
  "Чем могу помочь, {name}?",
  "Что хочешь узнать, {name}?",
  "Есть идея, {name}? Давай разберёмся.",
  "Готов помочь. С чего начнём, {name}?",
  "Что сегодня интересно, {name}?",
  "Давай начнём, {name}. Что нужно?",
  "Привет, {name}! Над чем поработаем?",
  "Какой вопрос разберём, {name}?",
  "Я здесь, {name}. Чем займёмся?",
  "Что у тебя на уме, {name}?",
  "Слушаю тебя, {name}.",
  "Какой план на сегодня, {name}?",
  "Чем займёмся вместе, {name}?",
  "Расскажи, что нужно, {name}.",
];
function welcomeNode() {
  const root = document.createElement("section");
  root.className = "empty welcome typing";
  root.innerHTML =
    '<div class="welcome-mascot" aria-hidden="true"><img class="welcome-mark" src="assets/mark.png" alt=""></div><h1></h1>';
  const title = $("h1", root);
  const name =
    String(dokkyState.account?.user?.display_name || "").trim().split(/\s+/)[0] || "друг";
  const line = welcomeLines[Math.floor(Math.random() * welcomeLines.length)].replace("{name}", name);
  const chars = Array.from(line);
  if (
    document.documentElement.dataset.motion === "reduced" ||
    matchMedia("(prefers-reduced-motion: reduce)").matches
  ) {
    title.textContent = line;
    root.classList.remove("typing");
    return root;
  }
  let index = 0;
  const next = () => {
    if (!root.isConnected && index) return;
    title.textContent = chars.slice(0, ++index).join("");
    if (index < chars.length) setTimeout(next, 26);
    else root.classList.remove("typing");
  };
  setTimeout(next, 30);
  return root;
}

const modeDescriptions = {
  "dokky-default": ["Авто", "Докки выберет подходящий режим", "Sparkles"],
  "dokky-fast": ["Быстрый", "Быстрый ответ без раздумий", "Zap"],
  "dokky-enhanced": ["Усиленный", "Глубже, умнее и с источниками", "Brain"],
};

// меню «+»: прикрепить файл и выбрать режим, как на сайте
function openCloudAttachMenu() {
  let menu = $("#cloud-attach-menu");
  if (menu && !menu.hidden) return SurfaceUI.close(menu);
  menu?.remove();
  menu = document.createElement("div");
  menu.id = "cloud-attach-menu";
  menu.className = "composer-actions surface-panel mode-menu";
  menu.setAttribute("role", "menu");
  menu.hidden = true;
  const select = $("#model");
  const current = () => modeDescriptions[select.value]?.[0] || select.selectedOptions[0]?.textContent || "Авто";
  // меню всегда раскрывается над полем ввода, как на сайте
  const above = () => {
    const anchor = $("#composer .composer-field").getBoundingClientRect();
    menu.style.left = `${Math.max(8, anchor.left)}px`;
    menu.style.top = `${Math.max(8, anchor.top - menu.offsetHeight - 10)}px`;
  };
  const page = (content) => {
    menu.replaceChildren(...content);
    above();
    menu.querySelector("button")?.focus({ preventScroll: true });
  };
  const choice = (label, symbol, action, hint) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mode-choice";
    button.setAttribute("role", "menuitem");
    button.innerHTML = icon(symbol) + `<span>${escapeHtml(label)}</span>`;
    if (hint) button.insertAdjacentHTML("beforeend", `<span class="mode-label">${escapeHtml(hint)}</span>`);
    button.onclick = guarded(action);
    return button;
  };
  const modes = () => {
    const back = choice("Режим Докки", "ChevronLeft", () => page(root()));
    back.className = "mode-back";
    const options = [...select.options]
      .filter((option) => option.value)
      .map((option) => {
        const [name, text, symbol] = modeDescriptions[option.value] || [option.textContent, "", "Sparkles"];
        const button = document.createElement("button");
        button.type = "button";
        button.className = "mode-option";
        button.setAttribute("role", "menuitemradio");
        button.setAttribute("aria-checked", String(option.value === select.value));
        button.disabled = option.disabled;
        button.innerHTML = `${icon(symbol)}<span><b>${escapeHtml(name)}</b><small>${escapeHtml(text)}</small></span>${icon("check")}`;
        button.onclick = () => {
          SurfaceUI.close(menu);
          if (select.value === option.value) return;
          select.value = option.value;
          select.dispatchEvent(new Event("change", { bubbles: true }));
        };
        return button;
      });
    return [back, ...options];
  };
  const root = () => [
    choice("Прикрепить файл", "attach", () => {
      SurfaceUI.close(menu);
      $("#files").click();
    }),
    choice("Режим", "settings", () => page(modes()), current()),
  ];
  menu.append(...root());
  document.body.append(menu);
  SurfaceUI.open(menu, $("#attach"));
  above();
  menu.onkeydown = (event) => SurfaceUI.menuKeys(event, menu);
  menu.querySelector("button")?.focus({ preventScroll: true });
}

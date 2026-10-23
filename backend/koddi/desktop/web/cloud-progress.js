/* события API v1 для компонента хода работы из Докки */
const cloudProgress = {
  live: null,
  messageControllers: new Map(),
  snapshots: new Map(),
};
const cloudProgressUser = () =>
  dokkyState.account?.user?.id || "current-account";
const cloudDurationKey=()=>`koddi-work-durations:${cloudProgressUser()}`;
function cloudDurations() {
  try {const value=JSON.parse(localStorage.getItem(cloudDurationKey())||'{}');return value&&typeof value==='object'&&!Array.isArray(value)?value:{};}
  catch {return {};}
}
function saveCloudDuration(id,elapsed) {
  if(!Number.isFinite(elapsed)||elapsed<0)return;
  const entries=Object.entries(cloudDurations()).filter(([key])=>key!==String(id));
  entries.push([String(id),elapsed]);
  try {localStorage.setItem(cloudDurationKey(),JSON.stringify(Object.fromEntries(entries.slice(-300))));}catch {}
}
const progressOptions = () => ({
  user: cloudProgressUser(),
  scope: dokkyState.scope,
  render: (node, text) => renderMarkdown(node, text),
  chevron: icon("arrowDown"),
});

function disposeCloudMessage(node) {
  const controller = cloudProgress.messageControllers.get(node);
  if (controller) {
    const snapshot = controller.snapshot();
    if (snapshot.messageId)
      cloudProgress.snapshots.set(String(snapshot.messageId), snapshot);
    controller.dispose();
  }
  while (cloudProgress.snapshots.size > 30)
    cloudProgress.snapshots.delete(cloudProgress.snapshots.keys().next().value);
  cloudProgress.messageControllers.delete(node);
}
function clearCloudProgress(forget = false) {
  cloudProgress.live?.controller.dispose();
  cloudProgress.live = null;
  for (const controller of cloudProgress.messageControllers.values())
    controller.dispose();
  cloudProgress.messageControllers.clear();
  if (forget) {
    try {localStorage.removeItem(cloudDurationKey());}catch {}
    DokkyWorkProgress.clear(cloudProgressUser());
    cloudProgress.snapshots.clear();
  }
}
function beginCloudProgress(chatId, runId = null, startedAt = null) {
  cloudProgress.live?.controller.dispose();
  const host = document.createElement("section");
  host.className = "cloud-work turn-message assistant";
  const status = document.createElement("div");
  status.className = "status-line";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  status.innerHTML =
    '<span class="spinner" aria-hidden="true"></span><span>Думаю…</span>';
  $("#feed").append(host);
  const controller = DokkyWorkProgress.create(host, {
    ...progressOptions(),
    status,
    startedAt,
  });
  controller.enable();
  controller.status("thinking", "Думаю…");
  cloudProgress.live = { chatId, runId, host, controller };
  return cloudProgress.live;
}
function placeCloudProgress() {
  const live = cloudProgress.live;
  if (!live || live.chatId !== state.chat?.id || state.page !== "chat") return;
  const pending = live.runId && state.messages.get(`pending_${live.runId}`);
  if (pending?.isConnected) $("#feed").insertBefore(live.host, pending);
  else $("#feed").append(live.host);
}
function handleCloudProgress(event, data) {
  if (!window.koddiProductScope || window.koddiProductScope === "development")
    return;
  let live = cloudProgress.live;
  if (!live && state.running)
    live = beginCloudProgress(state.chat.id, data.run_id || dokkyState.run);
  if (!live || live.chatId !== state.chat?.id) return;
  if (data.run_id) live.runId = data.run_id;
  if (event === "activity.updated") {
    const activity = data.activity || {};
    if (activity.kind === "commentary")
      live.controller.commentary(activity.text || "");
    else if (activity.kind !== "title" && activity.text)
      live.controller.status(activity.kind || "status", activity.text);
  } else if (event === "search.started")
    live.controller.status("web_search", "Ищу информацию…");
  else if (event === "search.source")
    live.controller.status(
      "web_search",
      `Изучаю источник: ${(data.source || data).title || (data.source || data).url || "страницу"}`,
    );
  else if (event === "message.delta" && data.content)
    live.controller.startAnswer();
  else if (
    ["turn.completed", "turn.failed", "turn.cancelled"].includes(event)
  ) {
    live.controller.finish();
    if (data.message_id) {
      live.controller.bind(data.message_id);
      cloudProgress.snapshots.set(data.message_id, live.controller.snapshot());
      saveCloudDuration(data.message_id,live.controller.snapshot().elapsed);
    }
    while (cloudProgress.snapshots.size > 30)
      cloudProgress.snapshots.delete(
        cloudProgress.snapshots.keys().next().value,
      );
    live.controller.dispose();
    live.host.remove();
    cloudProgress.live = null;
  }
  placeCloudProgress();
}
function renderCloudMessageProgress(node, message) {
  if (message.role !== "assistant" || String(message.id).startsWith("pending_"))
    return;
  const options = progressOptions();
  let cached = cloudProgress.snapshots.get(message.id);
  if ((message.activity || []).length)
    cached = {
      ...cached,
      elapsed: cached?.elapsed ?? cloudDurations()[String(message.id)] ?? null,
      messageId: message.id,
      entries: message.activity
        .filter((activity) => activity.text && activity.kind !== "title")
        .map((activity) => ({
          type: activity.kind === "commentary" ? "commentary" : "status",
          node: activity.kind,
          text: activity.text,
        })),
    };
  let controller;
  if (cached?.entries?.length)
    controller = DokkyWorkProgress.create(node, { ...options, cached });
  else
    controller = DokkyWorkProgress.restore(
      node,
      options.user,
      message.id,
      options,
    );
  if (controller) cloudProgress.messageControllers.set(node, controller);
}

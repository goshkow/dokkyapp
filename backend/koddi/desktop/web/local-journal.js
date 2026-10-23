// журнал содержит только публичные сообщения и действия, не историю провайдера
const localJournal = { turnId: null, status: "Думаю…" };
const JOURNAL_PAGE_SIZE = 100;

function journalPublicMessage(message) {
  const metadata = message.metadata || {};
  return (!message.role || message.role === "assistant") &&
    (!metadata.channel || ["commentary", "final"].includes(metadata.channel)) &&
    !["analysis", "reasoning", "thinking"].includes(metadata.kind);
}

function projectLocalJournal(messages) {
  if (window.koddiProductScope && window.koddiProductScope !== "development")
    return messages;
  const result = [];
  let pending = [], user = null;
  const flush = () => {
    if (!pending.length) return;
    const turns = pending.map(message => message.metadata?.turn).filter(Boolean);
    const turn = turns.length ? { ...turns.at(-1), timeline: [] } : null;
    if (turn) {
      const timeline = new Map();
      for (const value of turns)
        for (const event of value.timeline || [])
          timeline.set(event.id ?? `${event.sequence}:${event.kind}`, event);
      turn.timeline = [...timeline.values()];
    }
    const work = [], answers = [];
    let hasWork = false;
    // текст идущего шага ещё не знает свой канал: пока у хода есть работа, он остаётся
    // в журнале и не переезжает из ленты в журнал, когда шаг завершится комментарием
    const provisional = message => message.content && message.metadata?.status === "running" && !message.metadata?.channel
      ? { ...message, metadata: { ...message.metadata, channel: "commentary", provisional: true } } : message;
    for (const message of pending) {
      if (!journalPublicMessage(message)) continue;
      const metadata = message.metadata || {};
      if (metadata.channel === "final") {
        answers.push(message);
        work.push(message);
      } else if (metadata.channel === "commentary" || metadata.kind === "activity" || metadata.events?.length) {
        work.push(provisional(message));
        hasWork = true;
      } else if (message.content && hasWork && provisional(message) !== message)
        work.push(provisional(message));
      else if (message.content) answers.push(message);
    }
    if (work.some(message => message.metadata?.channel !== "final" || message.metadata?.events?.length) || turn?.timeline?.length)
      result.push({
        id: `journal-${turn?.id || pending[0].metadata?.turn_id || user?.id || pending[0].id}`,
        role: "assistant", content: "",
        metadata: {
          kind: "journal", work_items: work, turn,
          // ответ уже печатается: общий статус «Думаю…» больше не нужен
          live_text: answers.some(message => message.metadata?.status === "running" && message.content),
          status: turn?.status || (work.some(message => message.metadata?.status === "running") ? "running" : "completed"),
        },
      });
    result.push(...answers);
    pending = [];
  };
  for (const message of messages) {
    if (message.role === "user" || message.metadata?.kind === "journal") {
      flush();
      if (message.role === "user") user = message;
      result.push(message);
    } else {
      const previous = pending.at(-1)?.metadata;
      const oldTurn = previous?.turn_id ?? previous?.turn?.id;
      const newTurn = message.metadata?.turn_id ?? message.metadata?.turn?.id;
      if (oldTurn != null && newTurn != null && oldTurn !== newTurn) flush();
      pending.push(message);
    }
  }
  flush();
  return result;
}

function journalTime(value) {
  if (typeof value !== "string" || !value) return NaN;
  const iso = value.trim().replace(" ", "T");
  return Date.parse(/[zZ]$|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
}

function journalMessages(metadata) {
  const messages = new Map();
  for (const message of [...(metadata.work_items || []), ...(metadata.messages || [])]) {
    if (journalPublicMessage(message)) messages.set(message.id ?? message, message);
  }
  return [...messages.values()];
}

function journalEntryOrder(a, b, updated = false) {
  const aSequence = updated && a.type === "action" ? a.updated_sequence : a.sequence;
  const bSequence = updated && b.type === "action" ? b.updated_sequence : b.sequence;
  if (aSequence != null && bSequence != null && aSequence !== bSequence) return aSequence - bSequence;
  const delta = journalTime(updated ? a.updated_at || a.created_at : a.created_at) -
    journalTime(updated ? b.updated_at || b.created_at : b.created_at);
  return Number.isFinite(delta) ? delta : 0;
}

function orderedJournalEntries(metadata) {
  const entries = new Map();
  let order = 0;
  const add = entry => {
    const previous = entries.get(entry.key);
    if (previous) {
      // результат обновляет строку вызова, но не переносит её через commentary
      const first = journalEntryOrder(entry, previous) < 0 ? entry : previous;
      const latest = journalEntryOrder(entry, previous, true) < 0 ? previous : entry;
      const value = previous.type === "action" && previous.event.event === "tool.result" && entry.event.event === "tool.call" ? previous : latest;
      entries.set(entry.key, { ...value, sequence: first.sequence ?? latest.sequence,
        created_at: first.created_at || latest.created_at, updated_at: latest.updated_at,
        updated_sequence: latest.updated_sequence, order: previous.order });
    } else entries.set(entry.key, { ...entry, order: order++ });
  };
  const sequence = value => value != null && Number.isFinite(Number(value)) ? Number(value) : null;
  const addEvent = (event, fallback, key) => {
    const payload = event.data || event.payload || {};
    if (payload.channel && !["commentary", "final"].includes(payload.channel)) return;
    const kind = event.event || event.kind;
    const common = { sequence: sequence(event.sequence ?? payload.sequence ?? fallback.sequence),
      created_at: event.created_at || payload.created_at || fallback.created_at };
    if (["tool.call", "tool.result"].includes(kind)) {
      add({ ...common, key: payload.id != null ? `action:${payload.id}` : `action:${key}`,
        type: "action", updated_at: common.created_at,
        updated_sequence: sequence(event.sequence ?? payload.sequence), event: { event: kind, data: payload } });
    } else if (["context.compacting", "context.compacted"].includes(kind)) {
      add({...common, key:`context:${event.id ?? key}`, type:'compaction', text:payload.text || 'Сжимаю контекст…'});
    } else if (["agent.status", "agent.step", "agent.input_required"].includes(kind) || (!kind && typeof payload.text === "string")) {
      const text = payload.text || (kind === "agent.step" ? "Думаю…" : kind === "agent.input_required" ? "Ожидаю подтверждение" : kind === "context.compacted" ? "Контекст сохранён" : "");
      if (text) add({ ...common, key: `status:${event.id ?? key}`, type: "status", text });
    }
  };
  for (const message of journalMessages(metadata)) {
    const meta = message.metadata || {};
    const common = { sequence: sequence(meta.sequence), created_at: meta.started_at || message.created_at || meta.created_at };
    if (message.content?.trim() && ["commentary", "final"].includes(meta.channel))
      add({ ...common, key: `message:${message.id}:text`, type: meta.channel, text: message.content,
        completed_at: meta.completed_at, running: meta.status === "running",
        complete: meta.status === "completed" });
    for (const [index, event] of (meta.events || []).entries()) {
      const time = event.created_at || event.data?.created_at || event.payload?.created_at;
      addEvent(event, { ...common, sequence: index === 0 || !time ? common.sequence : null }, `${message.id}:${index}`);
    }
  }
  for (const [index, event] of (metadata.turn?.timeline || []).entries())
    addEvent(event, {}, `timeline:${index}`);
  const values = [...entries.values()];
  // старые записи без sequence располагаются по времени, а не получают ложный ноль
  const known = values.filter(entry => entry.sequence != null).sort((a, b) => a.sequence - b.sequence);
  for (const entry of values) {
    entry.position = entry.sequence;
    if (entry.position != null) continue;
    const time = journalTime(entry.created_at);
    const next = known.find(value => journalTime(value.created_at) > time);
    const previous = known.findLast(value => journalTime(value.created_at) <= time);
    entry.position = previous && next ? (previous.sequence + next.sequence) / 2 :
      previous ? previous.sequence + 0.5 : next ? next.sequence - 0.5 : null;
  }
  return values.sort((a, b) => {
    if (a.position != null && b.position != null && a.position !== b.position) return a.position - b.position;
    const delta = journalTime(a.created_at) - journalTime(b.created_at);
    return (Number.isFinite(delta) && delta) || a.order - b.order;
  });
}

function journalDuration(turn) {
  let paused = 0, since = null;
  for (const event of turn?.timeline || []) {
    if (event.kind === 'context.compacting' && since == null) since = journalTime(event.created_at);
    if (event.kind === 'context.compacted' && since != null) {paused += Math.max(0, journalTime(event.created_at)-since);since=null;}
  }
  if (since != null) paused += Math.max(0, journalTime(turn?.completed_at)-since);
  const elapsed = journalTime(turn?.completed_at) - journalTime(turn?.created_at) - paused;
  return Number.isFinite(elapsed) && elapsed >= 0 ? DokkyWorkProgress.duration(elapsed) : null;
}

const ruPlural = (n, one, few, many) => {
  const tail = n % 100, last = n % 10;
  return tail >= 11 && tail <= 14 ? many : last === 1 ? one : last >= 2 && last <= 4 ? few : many;
};
const journalGroups = {
  "file.read": "read", "tool.output_read": "read",
  "directory.list": "explore", "directory.tree": "explore", "search.text": "explore", "project.instructions": "explore",
  "file.create": "edit", "file.write_patch": "edit", "file.edit": "edit", "file.apply_patch": "edit",
  "shell.run": "command", "test.run": "command", "verification.run": "command",
  "web.search": "web", "web.fetch": "web", "plan.update": "meta",
  "task.complete": "meta", "task.ask_user": "meta", "task.delegate": "meta",
};

// «Прочитал 3 файла, изменил 2 файла, выполнил 5 команд (1 с ошибкой)»
function journalSummary(entries) {
  const actions = new Map();
  for (const entry of entries) if (entry.type === "action") actions.set(entry.key, entry.event);
  const count = { read: new Set(), explore: 0, created: new Set(), edited: new Set(), command: 0, failed: 0, web: 0, plan: 0, other: 0 };
  let added = 0, removed = 0;
  for (const event of actions.values()) {
    if (event.event !== "tool.result") continue;
    const data = event.data || {}, args = data.arguments || data.args || {}, result = data.result || {};
    const path = args.path || args.source || result.path || data.id;
    const group = journalGroups[data.name] || "other";
    if (group === "read") count.read.add(path);
    else if (group === "explore") count.explore++;
    else if (group === "edit") {
      (data.name === "file.create" ? count.created : count.edited).add(path);
      added += Number(result.added_lines ?? result.lines_added) || 0;
      removed += Number(result.removed_lines ?? result.lines_removed) || 0;
    } else if (group === "command") count.command++;
    else if (group === "web") count.web++;
    else if (group === "meta") count.plan++;
    else count.other++;
    if (result.ok === false) count.failed++;
  }
  const parts = [];
  const files = n => `${n} ${ruPlural(n, "файл", "файла", "файлов")}`;
  if (count.read.size) parts.push(`прочитал ${files(count.read.size)}`);
  if (count.explore) parts.push("изучил проект");
  if (count.created.size) parts.push(`создал ${files(count.created.size)}`);
  if (count.edited.size) parts.push(`изменил ${files(count.edited.size)}`);
  if (count.command) parts.push(`выполнил ${count.command} ${ruPlural(count.command, "команду", "команды", "команд")}`);
  if (count.web) parts.push("искал в интернете");
  if (count.other) parts.push(`выполнил ${count.other} ${ruPlural(count.other, "действие", "действия", "действий")}`);
  if (!parts.length && count.plan) parts.push("обновил план");
  // длинный перечень не помещается в строку: три главные части и многоточие
  let text = parts.slice(0, 3).join(", ") + (parts.length > 3 ? "…" : "");
  if (count.failed) text += ` (${count.failed} с ошибкой)`;
  text = text ? text[0].toLocaleUpperCase("ru-RU") + text.slice(1) : "Работал";
  return { text, added, removed, edits: count.created.size + count.edited.size };
}
function journalLiveLabel(event) {
  const name = event.data?.name;
  if (event.event === "tool.call") {
    if (["shell.run", "test.run", "verification.run"].includes(name)) return "Выполняется команда";
    if (name === "web.search") return "Ищу информацию в интернете";
  }
  return actionText(event);
}

function journalSegments(entries, metadata) {
  const blocks = [];
  let segment = null;
  const close = end => { if (segment) { segment.end = end; segment = null; } };
  for (const entry of entries) {
    if (entry.type === "final") { close(entry.created_at); continue; }
    if (entry.type === 'compaction') {close(entry.created_at);blocks.push(entry);continue;}
    if (entry.type === "commentary") {
      close(entry.created_at);
      blocks.push(entry);
    } else {
      if (!segment) {
        segment = { key: `segment:${entry.key}`, type: "segment", entries: [], created_at: entry.created_at };
        if (!blocks.length && metadata.turn?.created_at) segment.created_at = metadata.turn.created_at;
        blocks.push(segment);
      }
      segment.entries.push(entry);
    }
  }
  if (segment) {
    segment.running = metadata.status === "running" && !metadata.live_text;
    if (!segment.running) segment.end = metadata.turn?.completed_at;
  }
  // общее «Думаю…» без конкретных действий не получает отдельную строку после завершения этапа
  return blocks.filter(block => block.type !== "segment" || block.running ||
    block.entries.some(entry => entry.type !== "status" || !entry.text.startsWith("Думаю")));
}

function journalActionSignature(event) {
  const result = event.data?.result;
  return [event.event, actionText(event), result?.ok, result?.error, result?.reason, event.data?.arguments?.path, state.project?.id];
}

function localJournalSignature(item) {
  const metadata = item.metadata;
  return JSON.stringify([metadata.status, metadata.live_text, metadata.turn?.id, metadata.turn?.created_at,
    metadata.turn?.completed_at, metadata.turn?.summary,
    orderedJournalEntries(metadata).map(entry => [entry.key, entry.sequence, entry.created_at, entry.completed_at,
      entry.updated_at, entry.updated_sequence,
      entry.running, entry.complete, entry.type === "action" ? journalActionSignature(entry.event) : entry.text])]);
}

function reconcileJournalRows(parent, entries, cache, live, reorder = true) {
  const wanted = new Set(entries.map(entry => entry.key));
  for (const [key, value] of cache) if (!wanted.has(key)) {
    // узел мог уйти в ленту как готовый ответ: тогда он больше не принадлежит журналу
    if (value.node.parentNode === parent && !value.node._adoptedAway) { cancelCloudStream(value.node); value.node.remove(); }
    cache.delete(key);
  }
  let cursor = parent.firstElementChild;
  for (const entry of entries) {
    const event = entry.event, result = event?.data?.result;
    const signature = JSON.stringify([entry.type, entry.text, live && entry.running,
      entry.type === "action" ? journalActionSignature(event) : null]);
    let cached = cache.get(entry.key);
    if (!cached) {
      cached = { node: (entry.type === "commentary" && takeStreamBody(entry.key)) || document.createElement("div") };
      cached.node._journalEntryKey = entry.key; cache.set(entry.key, cached);
    }
    const row = cached.node;
    if (cached.signature !== signature) {
      if (entry.type === "status" || entry.type === 'compaction') {
        row.className = "journal-stage"; row.textContent = entry.text;
      } else if (entry.type === "commentary") {
        row.className = "journal-commentary message-body";
        if (live && entry.running && row.isConnected && !matchMedia("(prefers-reduced-motion: reduce)").matches)
          renderCloudStreamMarkdown(row, entry.text);
        else {
          cancelCloudStream(row);
          if (row.firstChild) {
            const draft = document.createElement("div");
            renderMarkdown(draft, entry.text, false);
            reconcileStreamNodes(row, draft);
          } else renderMarkdown(row, entry.text, false);
        }
      } else {
        row.className = "activity-line";
        row.removeAttribute("role"); row.removeAttribute("tabindex"); row.onclick = null; row.onkeydown = null;
        row.innerHTML = `<span class="action-state">${icon(event.event === "tool.call" ? "Clock" : result?.ok === false ? "close" : "check", 14)}</span><span>${escapeHtml(actionText(event))}</span>`;
        if (result?.error || result?.reason) {
          const error = document.createElement("p"); error.className = "error-detail";
          error.textContent = result.error || result.reason; row.append(error);
        }
        if (event.data?.arguments?.path && state.project) {
          row.role = "button"; row.tabIndex = 0;
          row.onclick = guarded(() => openFile(event.data.arguments.path));
          row.onkeydown = key => { if (key.key === "Enter" || key.key === " ") { key.preventDefault(); row.click(); } };
        }
      }
      cached.signature = signature;
    }
    if (reorder) {
      if (row !== cursor) parent.insertBefore(row, cursor);
      cursor = row.nextElementSibling;
    } else if (row.parentNode !== parent) parent.append(row);
  }
}

function journalDetails(className) {
  const details = document.createElement("details");
  details.className = `activity turn-journal ${className}`;
  details.innerHTML = `<summary><i class="journal-dots" aria-hidden="true"><b></b><b></b><b></b><b></b></i><span class="journal-summary-copy"></span><span class="journal-badge" hidden></span><span class="journal-duration"></span>${icon("chevron", 14)}</summary><div class="activity-body"><div class="activity-content journal-history"></div></div>`;
  return details;
}

function journalPager(parent, count, view, draw) {
  view.draw = draw;
  if (!view.pager) {
    view.pager = document.createElement("div"); view.pager.className = "journal-pagination";
    for (const [delta, title, symbol] of [[-1, "Предыдущие действия", "ChevronLeft"], [1, "Следующие действия", "ChevronRight"]]) {
      const button = document.createElement("button"); button.type = "button";
      button.title = title; button.setAttribute("aria-label", title); button.innerHTML = icon(symbol, 16);
      button.onclick = () => {
        view.offset += delta * JOURNAL_PAGE_SIZE;
        view.followTail = delta > 0 && view.offset >= Math.max(0, view.count - JOURNAL_PAGE_SIZE);
        view.draw();
      };
      view.pager.append(button);
    }
  }
  const end = Math.max(0, count - JOURNAL_PAGE_SIZE);
  if (view.offset == null || (view.followTail && state.followFeed !== false && view.count !== count)) view.offset = end;
  view.offset = Math.max(0, Math.min(view.offset, end));
  view.followTail ??= true;
  view.count = count;
  view.pager.children[0].disabled = view.offset === 0;
  view.pager.children[1].disabled = view.offset + JOURNAL_PAGE_SIZE >= count;
  if (count > JOURNAL_PAGE_SIZE) {
    if (view.pager.parentNode !== parent) parent.append(view.pager);
  } else view.pager.remove();
}

function updateJournalElapsed(details) {
  if (details.dataset.journalActive !== "true") return;
  const view = details._segmentView;
  const elapsed = Date.now() - view.startedAt;
  if (!Number.isFinite(elapsed) || elapsed < 0) return;
  view.elapsed = Math.max(view.elapsed || 0, elapsed);
  const duration = $(".journal-duration", details);
  const text = DokkyWorkProgress.duration(view.elapsed);
  if (duration.textContent !== text) duration.textContent = text;
}

function renderJournalSegment(details, block) {
  const view = details._segmentView ||= { rows: new Map() };
  const last = block.entries.reduce((latest, entry) => journalEntryOrder(entry, latest, true) >= 0 ? entry : latest);
  const summary = journalSummary(block.entries);
  const actionCount = block.entries.filter(entry => entry.type === "action").length;
  $(".journal-summary-copy", details).textContent = block.running
    ? (last.type === "action" ? journalLiveLabel(last.event) : last.text)
    : actionCount > 1 ? summary.text
    : last.type === "action" ? actionText(last.event)
    : last.text.startsWith("Думаю") ? "Думал" : "Работал";
  const badge = $(".journal-badge", details);
  badge.hidden = block.running || !summary.edits || (!summary.added && !summary.removed);
  if (!badge.hidden) badge.innerHTML = `<b class="positive">+${summary.added}</b><b class="danger">−${summary.removed}</b>`;
  const duration = journalDuration({ created_at: block.created_at, completed_at: block.end });
  $(".journal-duration", details).textContent = duration || "";
  details.classList.toggle("is-running", !!block.running);
  details.dataset.journalActive = String(!!block.running);
  const startedAt = journalTime(block.created_at);
  if (view.startedAt !== startedAt) view.elapsed = 0;
  view.startedAt = startedAt;
  if (block.running) updateJournalElapsed(details);
  const history = $(".journal-history", details);
  // служебные «Думаю…» и «Подготавливаю действие…» в списке только мешают
  const rows = block.entries.filter(entry => entry.type !== "status" || !/^(Думаю|Подготавливаю)/.test(entry.text));
  const draw = () => {
    if (!details.open) return;
    journalPager($(".activity-body", details), rows.length, view, draw);
    reconcileJournalRows(history, rows.slice(view.offset, view.offset + JOURNAL_PAGE_SIZE), view.rows, false);
  };
  details.ontoggle = draw;
  draw();
}

function renderLocalJournal(node, item, previousOpen) {
  const metadata = item.metadata, entries = orderedJournalEntries(metadata);
  const running = metadata.status === "running";
  const completed = metadata.status === "completed" && entries.some(entry => entry.type === "final" && entry.complete);
  const view = node._journalView ||= { blocks: new Map(), comments: new Map(), saved: new Map() };
  node.dataset.journalRunning = String(running);
  node.dataset.turnId = String(metadata.turn?.id || "");
  node.classList.add("local-work");
  const timeline = view.timeline ||= document.createElement("div");
  timeline.className = "journal-timeline";
  const track = view.track ||= document.createElement("div");
  track.className = "journal-window";
  if (timeline.parentNode !== track) track.prepend(timeline);
  const blocks = journalSegments(entries, metadata);
  const draw = () => {
    journalPager(track, blocks.length, view, draw);
    const visible = blocks.slice(view.offset, view.offset + JOURNAL_PAGE_SIZE);
    const wanted = new Set(visible.map(block => block.key));
    for (const [key, details] of view.blocks) if (!wanted.has(key)) {
      view.saved.set(key, { open: details.open, offset: details._segmentView?.offset });
      for (const cached of details._segmentView?.rows.values() || []) cancelCloudStream(cached.node);
      details.remove(); view.blocks.delete(key);
    }
    reconcileJournalRows(timeline, visible.filter(block => ['commentary','compaction'].includes(block.type)), view.comments, running, false);
    let cursor = timeline.firstElementChild;
    for (const block of visible) {
      let row;
      if (['commentary','compaction'].includes(block.type)) row = view.comments.get(block.key).node;
      else {
        row = view.blocks.get(block.key);
        if (!row) {
          row = journalDetails("journal-segment");
          const saved = view.saved.get(block.key);
          if (saved) {
            row.open = saved.open;
            row._segmentView = { rows: new Map(), offset: saved.offset, followTail: false };
          }
          view.blocks.set(block.key, row);
        }
        renderJournalSegment(row, block);
      }
      if (row !== cursor) timeline.insertBefore(row, cursor);
      cursor = row.nextElementSibling;
    }
  };
  draw();
  if (completed) {
    const group = view.group ||= journalDetails("journal-completed");
    if (!view.completed) group.open = false;
    const duration = journalDuration(metadata.turn);
    $(".journal-summary-copy", group).textContent = duration ? `Работал ${duration}` : "Работа завершена · время не сохранено";
    const history = $(".journal-history", group);
    if (track.parentNode !== history) history.append(track);
    if (group.parentNode !== node) node.prepend(group);
  } else {
    if (track.parentNode !== node) node.prepend(track);
    view.group?.remove();
  }
  view.completed = completed;
  if (["cancelled", "failed"].includes(metadata.status)) {
    const outcome = view.outcome ||= document.createElement("p");
    outcome.className = metadata.status === "failed" ? "error-detail journal-error" : "journal-stage journal-outcome";
    const duration = journalDuration(metadata.turn);
    outcome.textContent = (metadata.status === "failed" ? "Работа завершилась с ошибкой" : "Работа остановлена") +
      (duration ? ` · ${duration}` : "");
    if (outcome.parentNode !== node) node.append(outcome);
  } else view.outcome?.remove();
  const reason = metadata.status === "failed" && typeof metadata.turn?.summary === "string"
    ? metadata.turn.summary.trim() : "";
  if (reason) {
    const error = view.reason ||= document.createElement("p");
    error.className = "error-detail journal-error-reason";
    if (error.textContent !== reason) error.textContent = reason;
    if (error.parentNode !== node) node.append(error);
  } else view.reason?.remove();
  // пустой журнал не занимает место и не сдвигает ответ, когда появляется текст
  node.hidden = !(timeline.children.length || completed || view.outcome?.isConnected || view.reason?.isConnected);
}

function localWaiting(active) {
  if (window.koddiProductScope && window.koddiProductScope !== "development") return;
  if (!active) { $("#local-waiting")?.remove(); return; }
  const children = [...$("#feed").children];
  const lastUser = children.findLastIndex(node => node.classList.contains("user"));
  if (children.slice(lastUser + 1).some(node => node.classList.contains("assistant"))) return;
  if (!$("#local-waiting")) {
    const node = document.createElement("section"); node.id = "local-waiting"; node.className = "work-progress";
    node.innerHTML = '<div class="status-line" role="status" aria-live="polite"><i class="journal-dots" aria-hidden="true"><b></b><b></b><b></b><b></b></i><span class="local-status">Думаю…</span></div>';
    $("#feed").append(node); follow();
  }
}

function handleLocalJournal(event, data) {
  if (window.koddiProductScope && window.koddiProductScope !== "development") return;
  if (data.channel && !["commentary", "final"].includes(data.channel)) return;
  if (data.turn_id) localJournal.turnId = data.turn_id;
  if (["agent.waiting", "tool.waiting"].includes(event)) {
    const nodes = [...document.querySelectorAll('.local-work[data-journal-running="true"]')];
    const targets = data.turn_id != null
      ? nodes.filter(node => node.dataset.turnId === String(data.turn_id)) : nodes.slice(-1);
    for (const node of targets) {
      const current = $('.journal-segment[data-journal-active="true"]', node);
      if (current) updateJournalElapsed(current);
    }
    return;
  }
  if (event === "turn.started") {
    localJournal.status = "Думаю…"; localWaiting(true);
  } else if (event === "message.delta") $("#local-waiting")?.remove();
  else if (event === "message.updated") scheduleChatSync(0);
  else if (event === "agent.status") {
    localJournal.status = data.text || "Агент работает…";
    scheduleChatSync();
  } else if (["agent.step", "agent.input_required", "context.compacting", "context.compacted"].includes(event)) {
    localJournal.status = data.text || (event === "agent.step" ? "Думаю…" : event === "agent.input_required" ? "Ожидаю подтверждение" : "Контекст сохранён");
    scheduleChatSync();
  }
  else if (event === "tool.progress") localJournal.status = data.name
    ? `Получаю параметры: ${(data.display_name || data.name).replaceAll("__", ".")} · ${Math.round((data.bytes_received || 0) / 1024)} КБ`
    : "Получаю параметры действия…";
  else if (event === "tool.call" || event === "tool.result") localJournal.status = actionText({ event, data });
  else if (["turn.completed", "turn.failed", "turn.cancelled"].includes(event)) {
    for (const node of document.querySelectorAll('.local-work[data-journal-running="true"]'))
      if (node.dataset.turnId === String(localJournal.turnId)) {
        node.dataset.journalRunning = "false";
        for (const current of node.querySelectorAll('[data-journal-active="true"]')) {
          updateJournalElapsed(current);
          current.dataset.journalActive = "false";
          current.classList.remove("is-running");
        }
      }
    localWaiting(false); scheduleChatSync(0); return;
  }
  $("#local-waiting .local-status")?.replaceChildren(document.createTextNode(localJournal.status));
  for (const node of document.querySelectorAll('.local-work[data-journal-running="true"]'))
    if (node.dataset.turnId === String(localJournal.turnId)) {
      const title = $('.journal-segment[data-journal-active="true"] .journal-summary-copy', node);
      if (title) title.textContent = localJournal.status;
    }
}

// живой текст переходит между лентой и журналом тем же узлом, без перерисовки и скачка
function takeStreamBody(key) {
  const id = /^message:(.+):text$/.exec(key)?.[1];
  const body = id && state.orphanBodies?.get(id);
  if (!body) return null;
  state.orphanBodies.delete(id);
  return body;
}
function journalRowFor(id) {
  for (const node of document.querySelectorAll(".local-work")) {
    const row = node._journalView?.comments.get(`message:${id}:text`)?.node;
    if (row?.isConnected) return row;
  }
  return null;
}
function journalRunning() {
  return document.querySelector('.local-work[data-journal-running="true"]');
}
setInterval(() => {
  for (const details of document.querySelectorAll('.journal-segment[data-journal-active="true"]'))
    updateJournalElapsed(details);
}, 1000);

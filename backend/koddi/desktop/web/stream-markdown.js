/* сверка разметки и поэтапный показ слов, из mini_app/assets/app.js Докки */
function reconcileStreamNodes(oldParent, newParent, enter = () => {}) {
  const equal = (old, fresh) => {
    if (
      old.nodeType === 1 &&
      fresh.nodeType === 1 &&
      old.matches(".table-scroll") &&
      fresh.matches(".table-scroll")
    )
      return (
        old.childNodes.length === fresh.childNodes.length &&
        [...old.childNodes].every((node, index) =>
          node.isEqualNode(fresh.childNodes[index]),
        )
      );
    return old.isEqualNode(fresh);
  };
  const wanted = [...newParent.childNodes];
  wanted.forEach((fresh, index) => {
    let old = oldParent.childNodes[index];
    // готовый блок сохраняет узел даже при вставке нового блока перед ним
    if (old && !equal(old, fresh)) {
      const exact = [...oldParent.childNodes]
        .slice(index + 1)
        .find((node) => equal(node, fresh));
      if (exact) {
        oldParent.insertBefore(exact, old);
        old = exact;
      }
    }
    if (!old) {
      oldParent.append(fresh);
      enter(fresh);
      return;
    }
    if (equal(old, fresh)) return;
    if (wanted.slice(index + 1).some((node) => equal(old, node))) {
      oldParent.insertBefore(fresh, old);
      enter(fresh);
      return;
    }
    if (
      old.nodeType !== fresh.nodeType ||
      (old.nodeType === 1 && old.nodeName !== fresh.nodeName)
    ) {
      old.replaceWith(fresh);
      enter(fresh);
      return;
    }
    if (old.nodeType === 3) {
      if (old.nodeValue !== fresh.nodeValue) old.nodeValue = fresh.nodeValue;
      return;
    }
    if (old.matches(".rich-video-card")) {
      if (
        old.dataset.videoId === fresh.dataset.videoId &&
        old.getAttribute("href") === fresh.getAttribute("href")
      )
        return;
      old.replaceWith(fresh);
      enter(fresh);
      return;
    }
    const sameRichImage =
      old.hasAttribute("data-rich-image") &&
      old.getAttribute("data-rich-image") ===
        fresh.getAttribute("data-rich-image");
    for (const attr of [...old.attributes])
      if (
        !fresh.hasAttribute(attr.name) &&
        !(old.matches(".table-scroll") && attr.name === "style") &&
        !(
          sameRichImage &&
          ["src", "data-media-path", "data-source-url"].includes(attr.name)
        )
      )
        old.removeAttribute(attr.name);
    for (const attr of [...fresh.attributes])
      if (old.getAttribute(attr.name) !== attr.value)
        old.setAttribute(attr.name, attr.value);
    reconcileStreamNodes(old, fresh, enter);
  });
  while (oldParent.childNodes.length > wanted.length)
    oldParent.lastChild.remove();
}

const streamJobs = new WeakMap();
const streamMotion = () =>
  document.documentElement.dataset.motion !== "reduced" &&
  !matchMedia("(prefers-reduced-motion: reduce)").matches;
const STREAM_WORD_SKIP = "table,a,pre,code,.code-head,.katex,svg,textarea";
const STREAM_WORD_FRAMES = [
  { opacity: 0, filter: "blur(3px)", transform: "translateY(4px)", offset: 0, easing: "cubic-bezier(.2,.6,.3,1)" },
  { opacity: 0.65, filter: "blur(1.5px)", transform: "translateY(.5px)", offset: 0.38, easing: "cubic-bezier(.2,.6,.3,1)" },
  { opacity: 1, filter: "blur(0px)", transform: "translateY(-.65px)", offset: 0.7, easing: "cubic-bezier(.4,0,.35,1)" },
  { opacity: 1, filter: "blur(0px)", transform: "translateY(0)", offset: 1 },
];

// каждое слово отдельным узлом: новые проявляются, старые не перерисовываются
function wrapStreamWords(root, fromOffset) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const texts = [];
  while (walker.nextNode()) texts.push(walker.currentNode);
  let offset = 0;
  for (const text of texts) {
    const start = offset;
    offset += text.length;
    if (text.parentElement.closest(STREAM_WORD_SKIP)) continue;
    const fragment = document.createDocumentFragment();
    let position = start;
    for (const part of text.nodeValue.match(/\s+|\S+/gu) || []) {
      if (/^\s+$/u.test(part)) fragment.append(document.createTextNode(part));
      else {
        const word = document.createElement("span");
        word.className = "stream-word";
        word.textContent = part;
        word._streamNew = position >= fromOffset;
        fragment.append(word);
      }
      position += part.length;
    }
    text.replaceWith(fragment);
  }
}

function paintStream(target, text, job) {
  const draft = document.createElement("div");
  renderMarkdown(draft, text, false, { streaming: true });
  const previous = job.text ?? target.textContent;
  const next = draft.textContent;
  let common = 0;
  while (
    common < previous.length &&
    common < next.length &&
    previous[common] === next[common]
  )
    common++;
  job.text = next;
  const motion = streamMotion();
  wrapStreamWords(draft, motion ? common : Infinity);
  const positions = [
    ...target.querySelectorAll("pre,table,.table-scroll"),
  ].map((node) => ({ node, left: node.scrollLeft }));
  reconcileStreamNodes(target, draft, (node) => {
    if (!motion || node.nodeType !== 1) return;
    const words = [
      ...(node.matches(".stream-word") ? [node] : []),
      ...node.querySelectorAll(".stream-word"),
    ];
    for (const word of words) {
      if (!word._streamNew) continue;
      word
        .animate(STREAM_WORD_FRAMES, { duration: 520, fill: "both" })
        .finished.then((animation) => animation.cancel())
        .catch(() => {});
    }
  });
  for (const { node, left } of positions)
    if (node.isConnected && Math.abs(node.scrollLeft - left) > 0.5)
      node.scrollLeft = left;
}

function pumpStream(target) {
  const job = streamJobs.get(target);
  if (!job) return;
  job.raf = 0;
  if (!target.isConnected) return;
  const now = performance.now();
  if (now < job.notBefore) {
    job.raf = requestAnimationFrame(() => pumpStream(target));
    return;
  }
  const remaining = job.full.length - job.shown;
  if (remaining > 0) {
    const frames = Math.max(1, Math.min(6, Math.round((now - job.last) / 16)));
    let shown = Math.min(
      job.full.length,
      job.shown + frames * Math.min(72, Math.max(12, Math.ceil(remaining / 18))),
    );
    const edge = job.full.charCodeAt(shown - 1);
    if (edge >= 0xd800 && edge <= 0xdbff && shown < job.full.length) shown++;
    job.shown = shown;
    job.last = now;
  }
  const visible = job.full.slice(0, job.shown);
  // замена текста (replace, поправка смещения) тоже должна дойти до экрана
  if (visible !== job.painted) {
    job.painted = visible;
    paintStream(target, visible, job);
    // разбор разметки занимает не больше половины времени кадров
    job.notBefore = performance.now() + (performance.now() - now);
    if (typeof follow === "function") follow();
  }
  if (job.shown < job.full.length)
    job.raf = requestAnimationFrame(() => pumpStream(target));
  else job.drained?.();
}

function renderCloudStreamMarkdown(target, source) {
  let job = streamJobs.get(target);
  if (!job) {
    job = { full: "", shown: 0, last: performance.now(), notBefore: 0, raf: 0, text: null };
    // уже показанный текст не сворачивается и не печатается заново
    if (target.firstChild) {
      job.shown = target._shownLength ?? Infinity;
      job.text = target.textContent;
    }
    streamJobs.set(target, job);
  }
  job.full = String(source || "");
  if (job.shown === Infinity) job.shown = job.full.length;
  if (job.shown > job.full.length) job.shown = job.full.length;
  if (!job.raf) job.raf = requestAnimationFrame(() => pumpStream(target));
}

// ждёт, пока показанный текст догонит принятый, и только потом завершает ответ
function settleCloudStream(target, settle) {
  const job = streamJobs.get(target);
  if (!job || job.shown >= job.full.length || !target.isConnected) {
    if (job?.raf) cancelAnimationFrame(job.raf);
    streamJobs.delete(target);
    settle();
    return;
  }
  clearTimeout(job.deadline);
  const run = () => {
    clearTimeout(job.deadline);
    job.drained = null;
    if (job.raf) cancelAnimationFrame(job.raf);
    streamJobs.delete(target);
    settle();
  };
  job.drained = run;
  job.deadline = setTimeout(run, 1800);
}

function cancelCloudSettle(target) {
  const job = streamJobs.get(target);
  if (!job?.drained) return;
  clearTimeout(job.deadline);
  job.drained = null;
}

function cancelCloudStream(target) {
  const job = streamJobs.get(target);
  if (!job) return;
  if (job.raf) cancelAnimationFrame(job.raf);
  clearTimeout(job.deadline);
  streamJobs.delete(target);
}

async function waitStreamAnimations(target, limit = 700) {
  const animations = target.getAnimations({ subtree: true });
  if (!animations.length) return;
  await Promise.race([
    Promise.allSettled(animations.map((animation) => animation.finished)),
    new Promise((resolve) => setTimeout(resolve, limit)),
  ]);
}

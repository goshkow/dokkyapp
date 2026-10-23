/* сетка библиотеки, вкладки и скелетоны из app.js Докки */
const cloudLibrary = {
  dialog: null, artifact: null, tab: 'recent', cursor: null, items: new Map(),
  loading: false, error: '', request: 0, abort: null, artifactAbort: null,
  favoritesPending: new Set(),
};

function dismissCloudLibrary() {
  const dialog=cloudLibrary.dialog;
  if(!dialog||dialog.classList.contains('is-closing'))return;
  dialog.classList.add('is-closing');
  const reduced=document.documentElement.dataset.motion==='reduced'||matchMedia('(prefers-reduced-motion:reduce)').matches;
  if(reduced)return closeCloudLibrary();
  setTimeout(()=>{if(cloudLibrary.dialog===dialog)closeCloudLibrary();},150);
}

function closeCloudLibrary() {
  cloudLibrary.request++;
  cloudLibrary.abort?.abort();
  cloudLibrary.artifactAbort?.abort();
  closeCloudImage(true);
  cloudLibrary.artifact?.remove();
  cloudLibrary.artifact = null;
  cloudLibrary.dialog?.remove();
  cloudLibrary.dialog = null;
  cloudLibrary.items.clear();
  cloudLibrary.loading = false;
}

function appendLibrarySkeletons(grid, count) {
  for (let index = 0; index < count; index++) {
    const card = document.createElement('div');
    card.className = 'library-card-skeleton';
    card.setAttribute('aria-hidden', 'true');
    const preview = document.createElement('span');
    preview.className = 'library-preview library-skeleton-block';
    const details = document.createElement('span');
    details.className = 'library-card-details';
    for (const width of [index % 2 ? '68%' : '82%', '46%']) {
      const line = document.createElement('i');
      line.className = 'library-skeleton-line';
      line.style.width = width;
      details.append(line);
    }
    card.append(preview, details);
    grid.append(card);
  }
}

async function toggleCloudFavorite(item, button) {
  if (button.disabled || cloudLibrary.favoritesPending.has(item.id)) return;
  cloudLibrary.favoritesPending.add(item.id);
  const dialog = cloudLibrary.dialog, favorite = !item.favorite;
  button.disabled = true;
  try {
    const saved = await api(cloudPath(`library/${encodeURIComponent(item.id)}/favorite`), {
      method: 'PUT', body: JSON.stringify({favorite}),
    });
    if (dialog !== cloudLibrary.dialog || !dialog?.open) return;
    item.favorite = Boolean(saved.favorite);
    const current=cloudLibrary.items.get(item.id);
    if(current)current.favorite=item.favorite;
    if (cloudLibrary.tab === 'favorites' && !item.favorite) cloudLibrary.items.delete(item.id);
    renderCloudLibrary();
    notify(item.favorite ? 'Добавлено в избранное' : 'Удалено из избранного');
  } catch (error) {
    if (dialog === cloudLibrary.dialog) notify(error.message, true);
  } finally {
    cloudLibrary.favoritesPending.delete(item.id);
    button.disabled = false;
    if(dialog===cloudLibrary.dialog)renderCloudLibrary();
  }
}

function renderCloudLibrary() {
  const dialog = cloudLibrary.dialog;
  if (!dialog?.open) return;
  const grid = $('.library-grid', dialog), scroll = $('.library-scroll', dialog), top = scroll.scrollTop;
  grid.replaceChildren();
  grid.setAttribute('aria-busy', String(cloudLibrary.loading));
  for (const item of cloudLibrary.items.values()) {
    const url = cloudFileURL(item);
    if (!url) continue;
    const card = document.createElement('div');
    card.className = 'library-card';
    const open = document.createElement('button');
    open.type = 'button'; open.className = 'library-open-file';
    open.setAttribute('aria-label', `Открыть: ${item.name}`);
    const preview = document.createElement('span');
    preview.className = 'library-preview';
    if (item.kind === 'image') {
      preview.classList.add('is-loading');
      const image = document.createElement('img');
      image.alt = item.name; image.loading = 'lazy'; image.decoding = 'async'; image.src = url;
      image.onload = () => preview.classList.remove('is-loading');
      image.onerror = () => {
        preview.classList.remove('is-loading'); image.remove(); preview.innerHTML = icon('ImageOff');
        open.onclick = () => notify('Не удалось загрузить изображение', true);
      };
      preview.append(image);
      open.onclick = () => openCloudImage(item, image);
    } else {
      preview.innerHTML = icon('file');
      open.onclick = guarded(() => openCloudArtifact(item));
    }
    const details = document.createElement('span'); details.className = 'library-card-details';
    const title = document.createElement('b'), subtitle = document.createElement('small');
    title.textContent = item.name;
    const date = new Date(item.created_at);
    subtitle.textContent = [item.source, item.chat, Number.isNaN(date.valueOf()) ? '' : date.toLocaleDateString('ru-RU')].filter(Boolean).join(' · ');
    subtitle.title = subtitle.textContent;
    details.append(title, subtitle); open.append(preview, details); card.append(open);
    const actions = document.createElement('div'); actions.className = 'library-card-actions';
    actions.innerHTML = iconButton(item.favorite ? 'Убрать из избранного' : 'В избранное', 'Star');
    const favorite = $('button', actions); favorite.setAttribute('aria-pressed', String(Boolean(item.favorite)));
    favorite.disabled=cloudLibrary.favoritesPending.has(item.id);
    favorite.onclick = () => toggleCloudFavorite(item, favorite);
    const download = document.createElement('a'); download.href = url; download.download = item.name;
    download.title = 'Скачать'; download.setAttribute('aria-label', `Скачать: ${item.name}`); download.innerHTML = icon('download');
    actions.append(download); card.append(actions); grid.append(card);
  }
  if (cloudLibrary.loading) appendLibrarySkeletons(grid, cloudLibrary.items.size ? 4 : 8);
  if (!cloudLibrary.loading && !cloudLibrary.items.size) {
    const empty = document.createElement('p'); empty.className = 'library-empty';
    empty.textContent = cloudLibrary.tab === 'favorites' ? 'В избранном пока ничего нет.' : 'Материалов пока нет.';
    grid.append(empty);
  }
  const error = $('.library-feedback', dialog); error.replaceChildren();
  if (cloudLibrary.error) {
    const text = document.createElement('p'); text.textContent = cloudLibrary.error;
    const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'secondary'; retry.textContent = 'Повторить';
    retry.onclick = () => loadCloudLibrary(!cloudLibrary.items.size); error.append(text, retry);
  }
  const more = $('.library-load', dialog);
  more.hidden = !cloudLibrary.cursor; more.disabled = cloudLibrary.loading;
  more.textContent = cloudLibrary.loading ? 'Загружаю…' : 'Показать ещё'; scroll.scrollTop = top;
}

async function loadCloudLibrary(reset = false) {
  if (cloudLibrary.loading && !reset) return;
  cloudLibrary.abort?.abort();
  const abort = new AbortController(), request = ++cloudLibrary.request, dialog = cloudLibrary.dialog;
  cloudLibrary.abort = abort;
  if (reset) { cloudLibrary.items.clear(); cloudLibrary.cursor = null; }
  cloudLibrary.loading = true; cloudLibrary.error = ''; renderCloudLibrary();
  try {
    const query = new URLSearchParams({tab: cloudLibrary.tab, limit: '48'});
    if (!reset && cloudLibrary.cursor) query.set('cursor', cloudLibrary.cursor);
    const data = await api(cloudPath(`library?${query}`), {signal: abort.signal});
    if (request !== cloudLibrary.request || dialog !== cloudLibrary.dialog) return;
    for (const item of data.items || []) if (cloudFileURL(item)) cloudLibrary.items.set(item.id, item);
    cloudLibrary.cursor = data.next_cursor || null;
    $('.library-intro', dialog).textContent = Number.isInteger(data.retention_days)
      ? `Изображения и файлы из ваших чатов хранятся ${data.retention_days} дн.`
      : 'Изображения и файлы из ваших чатов';
  } catch (error) {
    if (request === cloudLibrary.request && error.name !== 'AbortError') cloudLibrary.error = error.message;
  } finally {
    if (request === cloudLibrary.request) { cloudLibrary.loading = false; renderCloudLibrary(); }
  }
}

function openCloudLibrary() {
  if (!dokkyState.account?.authenticated) return;
  closeCloudLibrary(); cloudLibrary.tab = 'recent';
  const dialog = document.createElement('dialog'); dialog.className = 'cloud-library'; dialog.setAttribute('aria-label', 'Библиотека Докки');
  dialog.innerHTML = `<div class="modal-top"><strong>Библиотека</strong>${iconButton('Закрыть библиотеку','close')}</div><p class="library-intro">Изображения и файлы из ваших чатов</p><div class="library-tabs" role="tablist" aria-label="Разделы библиотеки"></div><div class="library-content"><div class="library-scroll"><div class="library-grid"></div><div class="library-feedback" role="status"></div><button class="library-load" type="button" hidden>Показать ещё</button></div></div>`;
  cloudLibrary.dialog = dialog; document.body.append(dialog);
  $('.modal-top button', dialog).onclick = dismissCloudLibrary;
  dialog.addEventListener('cancel', event => {event.preventDefault(); dismissCloudLibrary();});
  const tabs = $('.library-tabs', dialog);
  for (const [id, label] of [['recent','Недавние'],['favorites','Избранное'],['images','Изображения'],['files','Файлы']]) {
    const button = document.createElement('button'); button.type = 'button'; button.role = 'tab'; button.dataset.libraryTab = id; button.textContent = label;
    button.setAttribute('aria-selected', String(id === cloudLibrary.tab));
    button.onclick = () => {
      cloudLibrary.tab = id;
      tabs.querySelectorAll('button').forEach(tab => tab.setAttribute('aria-selected', String(tab === button)));
      $('.library-scroll', dialog).scrollTop = 0; void loadCloudLibrary(true);
    };
    button.onkeydown = event => {
      const list = [...tabs.children], index = list.indexOf(button);
      const next = event.key === 'ArrowRight' ? (index + 1) % list.length : event.key === 'ArrowLeft' ? (index + list.length - 1) % list.length : event.key === 'Home' ? 0 : event.key === 'End' ? list.length - 1 : null;
      if (next !== null) { event.preventDefault(); list[next].focus(); list[next].click(); }
    };
    tabs.append(button);
  }
  $('.library-load', dialog).onclick = () => loadCloudLibrary();
  dialog.showModal(); void loadCloudLibrary(true);
}

async function openCloudArtifact(file) {
  if (!cloudFileURL(file)) return;
  file={...file,name:file.name||'Файл'};
  cloudLibrary.artifactAbort?.abort(); cloudLibrary.artifact?.remove();
  const abort = new AbortController(), dialog = document.createElement('dialog');
  cloudLibrary.artifactAbort = abort; cloudLibrary.artifact = dialog;
  dialog.className = 'cloud-artifact'; dialog.setAttribute('aria-label', `Просмотр файла: ${file.name}`);
  dialog.innerHTML = `<div class="modal-top"><strong class="cloud-artifact-title"></strong>${iconButton('Закрыть файл','close')}</div><div class="cloud-artifact-body skeleton" aria-busy="true"></div><footer></footer>`;
  $('.cloud-artifact-title', dialog).textContent = file.name;
  const close = () => {abort.abort(); dialog.remove(); if (cloudLibrary.artifact === dialog) cloudLibrary.artifact = null;};
  $('.modal-top button', dialog).onclick = close; dialog.addEventListener('cancel', event => {event.preventDefault(); close();});
  document.body.append(dialog); dialog.showModal();
  const download = document.createElement('a'); download.href = cloudFileURL(file); download.download = file.name; download.className = 'secondary'; download.innerHTML = icon('download') + '<span>Скачать</span>'; $('footer', dialog).append(download);
  try {
    const artifact = await api(cloudPath(`artifacts/art_${(file.file_id||file.id).slice(5)}`), {signal: abort.signal});
    if (!dialog.isConnected || abort.signal.aborted) return;
    const body = $('.cloud-artifact-body', dialog); body.classList.remove('skeleton'); body.setAttribute('aria-busy','false');
    if (typeof artifact.content === 'string') {
      if (/\.md$/i.test(file.name)) {body.classList.add('message-body'); renderMarkdown(body, artifact.content, true);}
      else {
        const pre=document.createElement('pre'),code=document.createElement('code');
        code.textContent=artifact.content;
        const extension=file.name.split('.').pop().toLowerCase();
        const language=({py:'python',js:'javascript',ts:'typescript',json:'json',html:'xml',css:'css',sh:'bash',sql:'sql'})[extension];
        if(language&&window.hljs?.getLanguage(language)&&artifact.content.length<=100000)code.innerHTML=hljs.highlight(artifact.content,{language,ignoreIllegals:true}).value;
        pre.append(code);body.append(pre);
      }
    } else {const text = document.createElement('p'); text.textContent = 'Предпросмотр этого формата недоступен. Скачайте файл, чтобы открыть его.'; body.append(text);}
    const info = document.createElement('span'); info.className = 'muted'; info.textContent = Number.isFinite(artifact.size) ? `${new Intl.NumberFormat('ru-RU').format(artifact.size)} байт · Только просмотр` : 'Только просмотр'; $('footer', dialog).append(info);
  } catch (error) {
    if (abort.signal.aborted || !dialog.isConnected) return;
    const body = $('.cloud-artifact-body', dialog); body.classList.remove('skeleton'); body.setAttribute('aria-busy','false'); body.textContent = error.message;
    const retry = document.createElement('button'); retry.className = 'secondary'; retry.textContent = 'Повторить'; retry.onclick = () => openCloudArtifact(file); body.append(retry);
  }
}

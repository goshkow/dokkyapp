const localQueue = { timer: null, stopping: false, project: undefined, permissionsVersion: 0 };
const localHistory = { key:'', older:[], current:[], exhausted:false, busy:false };
const renderLocalHistoryBase = renderChatMessages;
renderChatMessages = (messages) => {
  if (window.koddiProductScope !== 'development') return renderLocalHistoryBase(messages);
  const key = `${state.chat?.id}:${state.viewVersion}`;
  if (localHistory.key !== key) Object.assign(localHistory,{key,older:[],current:[],exhausted:false,busy:false});
  localHistory.current = messages;
  const oldest = messages[0]?.id || Infinity;
  const combined = [...localHistory.older.filter(item=>item.id<oldest),...messages];
  renderLocalHistoryBase(combined.slice(-1000));
  $('#load-local-history')?.remove();
  if (!localHistory.exhausted && combined.length >= 200 && combined.length < 1000) {
    const button = document.createElement('button'); button.id='load-local-history'; button.type='button';
    button.className='secondary'; button.textContent=localHistory.busy?'Загрузка…':'Предыдущие сообщения'; button.disabled=localHistory.busy;
    button.onclick=guarded(async()=>{
      const chat=state.chat?.id, version=state.viewVersion, height=$('#feed').scrollHeight, top=$('#feed').scrollTop;
      localHistory.busy=true; button.disabled=true; button.textContent='Загрузка…';
      try {
        const previous=await api(`/api/chats/${chat}/messages?before=${combined[0].id}&limit=200`);
        if (state.chat?.id!==chat || state.viewVersion!==version) return;
        localHistory.older=[...previous,...localHistory.older]; localHistory.exhausted=previous.length<200;
        state.followFeed=false;
        renderChatMessages(localHistory.current);
        $('#feed').scrollTop=top+$('#feed').scrollHeight-height;
      } finally { localHistory.busy=false; if(button.isConnected){button.disabled=false;button.textContent='Предыдущие сообщения';} }
    });
    $('#feed').prepend(button);
  }
};

async function refreshProjectPermissions() {
  const project = state.project?.id || null;
  if (localQueue.project === project) return;
  localQueue.project = project;
  const version = ++localQueue.permissionsVersion;
  state.settings.permission_profile = 'ask';
  const settings = await api('/api/settings' + (project ? `?project_id=${project}` : ''));
  if (version !== localQueue.permissionsVersion || (state.project?.id || null) !== project) return;
  state.settings.permission_profile = settings.permission_profile;
  state.settings.private_network = settings.private_network;
  if (typeof updateProductNavigation === 'function') updateProductNavigation();
  updateEnvironment();
}

async function refreshLocalQueue() {
  clearTimeout(localQueue.timer);
  const root = $('#local-queue'), chat = state.chat?.id, version = state.viewVersion;
  if (!root || !chat || window.koddiProductScope !== 'development') {
    if (root) root.hidden = true;
    return;
  }
  const snapshot = await api(`/api/chats/${chat}/queue`);
  if (chat !== state.chat?.id || version !== state.viewVersion || window.koddiProductScope !== 'development') return;
  const signature=JSON.stringify([chat,snapshot.paused,snapshot.items.map(item=>[item.id,item.version,item.status])]);
  const changed=localQueue.signature!==signature;
  const previous=localQueue.signature;
  localQueue.signature=signature;
  root.replaceChildren();
  root.hidden = !snapshot.items.length;
  if (snapshot.items.length) {
    const header = document.createElement('div');
    header.className = 'queue-header';
    const title = document.createElement('span');
    title.textContent = snapshot.paused ? 'Очередь приостановлена' : 'Очередь запросов';
    header.append(title);
    if (snapshot.paused && snapshot.items.some(item => item.status === 'queued')) {
      const resume = document.createElement('button');
      resume.type = 'button'; resume.className = 'secondary'; resume.textContent = 'Продолжить';
      resume.onclick = guarded(async () => { await post(`/api/chats/${chat}/queue/resume`); await refreshLocalQueue(); });
      header.append(resume);
    }
    root.append(header);
  }
  for (const item of snapshot.items) {
    const row = document.createElement('div'); row.className = 'queue-row';
    const text = document.createElement('span');
    text.textContent = (item.status === 'running' ? 'Выполняется: ' : item.status === 'failed' ? 'Не выполнен, проверьте результат: ' : '') + item.content;
    text.title = text.textContent; row.append(text);
    if (item.status === 'queued') {
      const edit = document.createElement('button'); edit.type = 'button';
      edit.className = 'icon-button'; edit.title = 'Изменить запрос'; edit.setAttribute('aria-label', edit.title); edit.innerHTML = icon('pencil');
      edit.onclick = guarded(async () => {
        const value = await inputDialog('Изменить ожидающий запрос', [{name:'content',label:'Запрос',value:item.content}], 'Сохранить');
        if (!value) return;
        await api(`/api/chats/${chat}/queue/${item.id}`, {method:'PATCH',body:JSON.stringify({version:item.version,content:value.content})});
        await refreshLocalQueue();
      });
      row.append(edit);
    }
    if (item.status !== 'running') {
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'icon-button';
      remove.title = 'Удалить из очереди'; remove.setAttribute('aria-label',remove.title); remove.innerHTML = icon('close');
      remove.onclick = guarded(async () => { await api(`/api/chats/${chat}/queue/${item.id}?version=${item.version}`, {method:'DELETE'}); await refreshLocalQueue(); });
      row.append(remove);
    }
    root.append(row);
  }
  if (snapshot.items.length) localQueue.timer = setTimeout(() => guarded(refreshLocalQueue)(), 1200);
  if(changed && previous && (snapshot.items.some(item=>item.status==='running') || localQueue.wasRunning)) {
    localQueue.wasRunning=snapshot.items.some(item=>item.status==='running');
    await syncChat();
    if(state.chat?.id===chat&&state.running&&!state.streamAbort) attachStream(chat);
  }
}

async function enqueueLocalDraft() {
  const content = $('#prompt').value.trim(), chat = state.chat?.id;
  if (!content || !chat || !attachmentsReady()) return;
  const attachments = [...state.attachments];
  const payload={
    content, mode: $('#mode').value, model: $('#model').value,
    options: {}, attachment_ids: attachments.map(item => item.id),
  };
  const signature=JSON.stringify([chat,payload]);
  if(localQueue.pending?.signature!==signature) localQueue.pending={signature,key:crypto.randomUUID()};
  await post(`/api/chats/${chat}/queue`, {...payload,request_id:localQueue.pending.key});
  localQueue.pending=null;
  if (state.chat?.id !== chat) return;
  $('#prompt').value = ''; state.attachments = []; renderAttachments(); autoSize();
  await refreshLocalQueue();
}

const queueDraftButton = document.createElement('button');
queueDraftButton.type = 'button'; queueDraftButton.className = 'composer-icon';
queueDraftButton.id = 'queue-draft'; queueDraftButton.hidden = true;
queueDraftButton.title = 'Добавить в очередь'; queueDraftButton.setAttribute('aria-label', queueDraftButton.title);
queueDraftButton.innerHTML = icon('ListPlus'); queueDraftButton.onclick = guarded(enqueueLocalDraft);
$('#send').before(queueDraftButton);
$('#prompt').addEventListener('input', () => {
  queueDraftButton.hidden = !(state.running && window.koddiProductScope === 'development' && $('#prompt').value.trim());
});

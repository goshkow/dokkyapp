/* копирование как в message-actions Докки, варианты ответа - по DTO API v1 */
const cloudVariants = {selected: new Map(), pending: new Map()};

function cloudVariantRoot(message,byId) {
  let current=message;const visited=new Set();
  while(current.branch_of&&byId.has(String(current.branch_of))&&!visited.has(current.id)) {
    visited.add(current.id);current=byId.get(String(current.branch_of));
  }
  return String(current.id);
}

function projectCloudVariants(messages) {
  if (window.koddiProductScope === 'development') return messages;
  const byId = new Map(messages.map(message=>[String(message.id),message]));
  const rootOf = message => cloudVariantRoot(message,byId);
  const groups=new Map();
  for(const message of messages) {
    if(message.role!=='assistant')continue;
    const root=rootOf(message),group=groups.get(root)||[];
    group.push(message);groups.set(root,group);
  }
  const result=[];
  for(const message of messages) {
    const root=rootOf(message),group=groups.get(root);
    if(message.role!=='assistant'||!group||group.length<2){result.push(message);continue;}
    if(String(message.id)!==root)continue;
    const key=`${state.chat?.id}:${root}`,selected=cloudVariants.selected.get(key);
    const index=selected?Math.max(0,group.findIndex(item=>String(item.id)===selected)):group.length-1;
    const chosen=group[index];
    result.push({...chosen,metadata:{...chosen.metadata,cloud_variant:{root,key,index,ids:group.map(item=>String(item.id))}}});
  }
  return result;
}

function renderCloudActions(node,message) {
  const actions=$('.message-actions',node);
  if(!actions||message.role!=='assistant')return;
  const variant=message.metadata?.cloud_variant;
  if(variant) {
    const group=document.createElement('span');group.className='cloud-variants';
    group.setAttribute('role','group');group.setAttribute('aria-label','Варианты ответа');
    group.innerHTML=`${iconButton('Предыдущий вариант','ChevronLeft')}<span>${variant.index+1} / ${variant.ids.length}</span>${iconButton('Следующий вариант','ChevronRight')}`;
    const controls=group.querySelectorAll('button');
    const select=index=>{cloudVariants.selected.set(variant.key,variant.ids[index]);state.followFeed=false;renderChatMessages([...state.messageSources.values()]);};
    controls[0].disabled=variant.index===0;controls[0].onclick=()=>select(variant.index-1);
    controls[1].disabled=variant.index===variant.ids.length-1;controls[1].onclick=()=>select(variant.index+1);
    actions.prepend(group);
  }
  // кнопки «Повторить ответ» на сайте нет, поэтому здесь её тоже нет
}

async function regenerateCloudAnswer(message) {
  if(state.running||!state.chat||dokkyState.scope!=='chat')return;
  const chatId=state.chat.id,view=state.viewVersion,scope=dokkyState.scope;
  const pendingKey=`${chatId}:${message.id}`;
  let pending=cloudVariants.pending.get(pendingKey);
  if(!pending){pending={key:crypto.randomUUID()};cloudVariants.pending.set(pendingKey,pending);}
  if(pending.running)return;
  pending.running=true;
  const context={version:view,scope,chat:state.chat,cancelled:false};
  dokkyState.submission=context;
  const current=()=>state.chat?.id===chatId&&state.viewVersion===view&&dokkyState.scope===scope;
  setRunning(true);status('Думаю…');state.followFeed=true;beginCloudProgress(chatId);
  try {
    const result=pending.result||await api(cloudPath(`chats/${encodeURIComponent(chatId)}/messages/${encodeURIComponent(message.id)}/regenerate`),{method:'POST',headers:{'Idempotency-Key':pending.key},body:'{}'});
    pending.result=result;
    if(context.cancelled){await post(cloudPath(`runs/${encodeURIComponent(result.run_id)}/cancel`));cloudVariants.pending.delete(pendingKey);return;}
    if(!current())return;
    cloudVariants.selected.delete(`${chatId}:${cloudVariantRoot(message,state.messageSources)}`);
    dokkyState.run=result.run_id;window.koddiCloudCursor='0';
    if(cloudProgress.live)cloudProgress.live.runId=result.run_id;
    await cloudSync();
    cloudVariants.pending.delete(pendingKey);
    if(current()&&dokkyState.run)await cloudAttach(chatId);
  } catch(error) {
    if(current()){setRunning(false);clearCloudProgress();}
    throw error;
  } finally {
    pending.running=false;
    if(dokkyState.submission===context)dokkyState.submission=null;
    if(context.cancelled&&current()){setRunning(false);clearCloudProgress();await cloudSync();status('Повтор ответа остановлен');}
  }
}

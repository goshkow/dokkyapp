/* карточки источников и ленты вложений из чата и поиска Докки */
function cloudFileURL(file) {
  if(file.viewer_url)return file.viewer_url;
  const id=file.file_id||file.id;
  return /^file_[A-Za-z0-9_-]{1,128}$/.test(id||"")?cloudPath(`files/${encodeURIComponent(id)}/content`):null;
}
function sourceLink(source,text="",className="") {
  let url;try{url=new URL(source.url);}catch{return null;}
  if(!["https:","http:"].includes(url.protocol)||url.username||url.password)return null;
  const link=document.createElement("a");link.className=className;link.href=url.href;link.textContent=text;link.target="_blank";link.rel="noopener noreferrer";
  if(window.koddiDesktop)link.onclick=event=>{event.preventDefault();window.location.href=`koddi://open-url?url=${encodeURIComponent(url.href)}`;};
  return link;
}
function sourceDomain(source){try{return new URL(source.url).hostname.replace(/^www\./,"");}catch{return "Источник";}}
function sourceIcon(source){const node=document.createElement("span");node.className="ai-source-icon";node.textContent=sourceDomain(source)[0].toUpperCase();node.setAttribute("aria-hidden","true");return node;}

function openCloudSources(sources,anchor) {
  document.querySelector(".ai-source-popover")?.remove();
  const dialog=document.createElement("dialog");dialog.className="ai-source-popover";dialog.setAttribute("aria-label","Источники утверждения");
  const head=document.createElement("header");head.className="ai-source-popover-head";head.innerHTML=`<strong>Источники</strong>${iconButton("Закрыть источники","close")}`;dialog.append(head);
  for(const source of sources){const link=sourceLink(source,"","ai-source-popover-row");if(!link)continue;const label=document.createElement("span");label.textContent=source.title||sourceDomain(source);link.append(sourceIcon(source),label);dialog.append(link);}
  document.body.append(dialog);
  const close=()=>{dialog.close();dialog.remove();anchor?.focus({preventScroll:true});};
  $("button",head).onclick=close;dialog.addEventListener("cancel",event=>{event.preventDefault();close();});dialog.onclick=event=>{if(event.target===dialog)close();};
  dialog.showModal();const rect=anchor.getBoundingClientRect(),width=dialog.getBoundingClientRect().width,height=dialog.getBoundingClientRect().height;
  dialog.style.left=`${Math.max(12,Math.min(rect.left,innerWidth-width-12))}px`;dialog.style.top=`${Math.max(12,Math.min(rect.bottom+8,innerHeight-height-12))}px`;
}
function citationChip(sources) {
  const first=sources[0];if(!first)return null;
  const node=sources.length===1?sourceLink(first,"","ai-source-chip"):document.createElement("button");if(!node)return null;
  node.className="ai-source-chip";node.setAttribute("aria-label",`Источники: ${sources.map(source=>sourceDomain(source)).join(", ")}`);
  if(node.tagName==="BUTTON"){node.type="button";node.onclick=()=>openCloudSources(sources,node);}
  const label=document.createElement("span");label.textContent=sourceDomain(first);node.append(sourceIcon(first),label);
  if(sources.length>1){const count=document.createElement("span");count.className="ai-citation-count";count.textContent=`+${sources.length-1}`;node.append(count);}
  return node;
}
function attachCloudCitations(body,message) {
  if(!body)return;
  const groups=new Map(),source=String(message.content||"");
  const walker=document.createTreeWalker(body,NodeFilter.SHOW_TEXT),texts=[];let text="";
  while(walker.nextNode()){const node=walker.currentNode;if(node.parentElement.closest("pre,code,button,a,.ai-source-chip"))continue;texts.push({node,start:text.length,end:text.length+node.length});text+=node.textContent;}
  for(const citation of message.citations||[]){
    if(!sourceLink(citation))continue;
    for(const span of citation.spans||[]){
      if(!Number.isInteger(span.start)||!Number.isInteger(span.end)||span.start<0||span.end<=span.start||span.end>source.length)continue;
      const fragment=document.createElement("div");renderMarkdown(fragment,source.slice(span.start,span.end),false);
      if(fragment.querySelector("pre,code,table"))continue;
      const quote=fragment.textContent.trim(),position=text.indexOf(quote);
      if(quote.length<3||position<0||position!==text.lastIndexOf(quote))continue;
      const end=position+quote.length,key=String(end);const entry=groups.get(key)||{end,sources:[]};
      if(!entry.sources.some(item=>item.url===citation.url))entry.sources.push(citation);groups.set(key,entry);
    }
  }
  for(const {end,sources} of [...groups.values()].sort((a,b)=>b.end-a.end)){
    const position=texts.find(item=>end>item.start&&end<=item.end);if(!position)continue;
    const chip=citationChip(sources);if(!chip)continue;
    const range=document.createRange();range.setStart(position.node,end-position.start);range.collapse(true);range.insertNode(chip);
  }
}
function renderCloudSources(parent,sources) {
  const section=document.createElement("section");section.className="cloud-sources";
  const heading=document.createElement("h3");heading.textContent="Источники ответа";section.append(heading);
  const list=document.createElement("div");list.className="ai-source-card-list";
  const seen=new Set();
  for(const source of sources||[]){
    if(seen.has(source.url))continue;const card=sourceLink(source,"","ai-source-card");if(!card)continue;seen.add(source.url);
    const copy=document.createElement("div");copy.className="ai-source-card-copy";
    const site=document.createElement("div");site.className="ai-source-card-site";const domain=document.createElement("span");domain.textContent=sourceDomain(source);site.append(sourceIcon(source),domain);
    const title=document.createElement("div");title.className="ai-source-card-title";title.textContent=source.title||sourceDomain(source);
    const description=document.createElement("p");description.className="ai-source-card-description";description.textContent=source.snippet||"";
    copy.append(site,title,description);card.append(copy);list.append(card);
  }
  if(!list.children.length)return;
  section.append(list);
  if(list.children.length>3){list.classList.add("is-limited");const more=document.createElement("button");more.type="button";more.className="ai-source-show-all";more.textContent=`Показать все (${list.children.length})`;more.setAttribute("aria-expanded","false");more.onclick=()=>{const limited=list.classList.toggle("is-limited");more.textContent=limited?`Показать все (${list.children.length})`:"Свернуть";more.setAttribute("aria-expanded",String(!limited));};section.append(more);}
  parent.append(section);
}

function renderCloudFiles(node,message) {
  const strip=document.createElement("div");strip.className="message-attachments";
  const files=[...(message.attachments||[]),...(message.artifacts||[])],seen=new Set();
  for(const file of files){
    // картинка уже показана в тексте ответа - второй раз её не повторяем
    if(message._richUsed?.has(file.file_id||file.id))continue;
    const url=cloudFileURL(file);if(!url||seen.has(url))continue;seen.add(url);
    const name=file.name||"Файл",isImage=String(file.media_type||"").startsWith("image/")||file.type==="image";
    const card=document.createElement("button");card.className="message-attachment"+(isImage?" is-image":"");card.title=name;card.setAttribute("aria-label",isImage?`Посмотреть ${name}`:`Открыть файл: ${name}`);
    if(isImage){card.type="button";const image=document.createElement("img");image.alt=name;image.loading="lazy";image.decoding="async";image.src=url;image.onload=()=>{if(image.closest("#feed")&&state.followFeed)follow();};card.append(image);card.onclick=()=>openCloudImage(file,image);}
    else {card.type="button";card.innerHTML=icon("file")+`<span>${escapeHtml(name)}</span>`;card.onclick=guarded(()=>openCloudArtifact(file));}
    strip.append(card);
  }
  if(strip.children.length){
    strip.classList.toggle("user-attachments",message.role==="user");
    const article=node.querySelector("article");
    // вложения ответа стоят под текстом и над кнопками действий
    if(message.role==="user")node.insertBefore(strip,article);
    else if(article)article.insertBefore(strip,article.querySelector(".message-actions"));
    else node.append(strip);
  }
}

const cloudImage={dialog:null,moved:null,zoom:1,fit:1,motion:[],closing:false,transition:0};
function fitCloudImage(){
  const image=cloudImage.moved?.image,stage=$(".cloud-image-stage",cloudImage.dialog);if(!image?.naturalWidth||!stage)return;
  cloudImage.fit=Math.min((stage.clientWidth-32)/image.naturalWidth,(stage.clientHeight-32)/image.naturalHeight,1);
  image.style.width=`${Math.max(1,image.naturalWidth*cloudImage.fit*cloudImage.zoom)}px`;image.style.height="auto";
  $(".cloud-image-zoom",cloudImage.dialog).textContent=`${Math.round(cloudImage.zoom*100)}%`;
}
async function closeCloudImage(immediate=false){
  if(!cloudImage.dialog?.open)return;
  if(cloudImage.closing&&!immediate)return;
  const transition=++cloudImage.transition;
  cloudImage.closing=true;for(const animation of cloudImage.motion)animation.cancel();
  const image=cloudImage.moved?.image;
  if(!immediate&&image&&document.documentElement.dataset.motion!=="reduced")await image.animate([{opacity:1},{opacity:0}],{duration:120}).finished.catch(()=>{});
  if(transition!==cloudImage.transition)return;
  const moved=cloudImage.moved;if(moved){moved.image.className=moved.className;moved.image.style.cssText=moved.style;if(moved.placeholder.isConnected)moved.placeholder.replaceWith(moved.image);else moved.image.remove();}
  cloudImage.moved=null;cloudImage.dialog.close();cloudImage.closing=false;
}
function openCloudImage(file,origin){
  if(!cloudFileURL(file)||cloudImage.dialog?.open)return;
  if(!cloudImage.dialog){
    const dialog=document.createElement("dialog");dialog.className="cloud-image-viewer";dialog.setAttribute("aria-label","Просмотр изображения");
    dialog.innerHTML=`${iconButton("Закрыть изображение","close")}<div class="cloud-image-stage"><div class="cloud-image-canvas"></div></div><footer><span class="cloud-image-name"></span><div class="actions">${iconButton("Уменьшить","ZoomOut")}<span class="cloud-image-zoom">100%</span>${iconButton("Увеличить","ZoomIn")}<a class="secondary cloud-image-download">${icon("download")}<span>Скачать</span></a></div></footer>`;
    document.body.append(dialog);cloudImage.dialog=dialog;
    $("button",dialog).onclick=()=>closeCloudImage();dialog.addEventListener("cancel",event=>{event.preventDefault();closeCloudImage();});
    const zoom=change=>{cloudImage.zoom=Math.min(4,Math.max(.5,cloudImage.zoom+change));fitCloudImage();};
    dialog.querySelector('[aria-label="Уменьшить"]').onclick=()=>zoom(-.25);dialog.querySelector('[aria-label="Увеличить"]').onclick=()=>zoom(.25);
    dialog.onclick=event=>{if(event.target===dialog||event.target.classList.contains('cloud-image-stage')||event.target.classList.contains('cloud-image-canvas'))closeCloudImage();};
  }
  const dialog=cloudImage.dialog,rect=origin.getBoundingClientRect(),placeholder=document.createElement("span");placeholder.className="viewer-image-placeholder";
  cloudImage.moved={image:origin,placeholder,className:origin.className,style:origin.style.cssText};
  origin.replaceWith(placeholder);origin.className="viewer-original-image";$(".cloud-image-canvas",dialog).append(origin);cloudImage.zoom=1;
  $(".cloud-image-name",dialog).textContent=file.name||"Изображение";const download=$(".cloud-image-download",dialog);download.href=cloudFileURL(file);download.download=file.name||"image";
  dialog.showModal();fitCloudImage();if(!origin.complete)origin.addEventListener("load",fitCloudImage,{once:true});
  const target=origin.getBoundingClientRect();
  if(document.documentElement.dataset.motion!=="reduced"&&target.width&&target.height){const x=rect.left+rect.width/2-target.left-target.width/2,y=rect.top+rect.height/2-target.top-target.height/2;cloudImage.motion=[origin.animate([{transform:`translate(${x}px,${y}px) scale(${rect.width/target.width},${rect.height/target.height})`,opacity:.6},{transform:"none",opacity:1}],{duration:280,easing:"cubic-bezier(.22,1,.36,1)"})];}
}
window.addEventListener("resize",()=>{document.querySelector(".ai-source-popover")?.remove();if(cloudImage.dialog?.open)fitCloudImage();});

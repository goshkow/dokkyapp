/* unified diff для локальных изменений */
const changeDiffCache=new Map();
function parseUnifiedDiff(source,metadata=[]) {
  const lines=String(source||'').split(/\r?\n/),files=[];let file=null,hunk=null,old=0,fresh=0,leftOld=0,leftNew=0;
  const clean=path=>path==='/dev/null'?null:path.replace(/^[ab]\//,'').split('\t')[0];
  for(let index=0;index<lines.length;index++) {
    const line=lines[index];
    if((!hunk||(!leftOld&&!leftNew))&&line.startsWith('--- ')&&lines[index+1]?.startsWith('+++ ')) {
      const oldPath=clean(line.slice(4)),newPath=clean(lines[++index].slice(4));
      file={path:newPath||oldPath||'Файл',oldPath,newPath,hunks:[],added:0,removed:0};files.push(file);hunk=null;continue;
    }
    const match=line.match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$/);
    if(match) {
      if(!file){file={path:metadata[0]?.path||'Файл',hunks:[],added:0,removed:0};files.push(file);}
      old=Number(match[1]);fresh=Number(match[3]);leftOld=Number(match[2]??1);leftNew=Number(match[4]??1);
      hunk={header:line,rows:[]};file.hunks.push(hunk);continue;
    }
    if(!hunk)continue;
    if(line.startsWith('\\ ')){hunk.rows.push({kind:'marker',text:'Нет перевода строки в конце файла'});continue;}
    const prefix=line[0];
    if(![' ','+','-'].includes(prefix)||(!leftOld&&!leftNew))continue;
    const row={kind:prefix==='+'?'added':prefix==='-'?'removed':'context',text:line.slice(1),old:null,fresh:null};
    if(prefix!=='+'){row.old=old++;leftOld=Math.max(0,leftOld-1);}
    if(prefix!=='-'){row.fresh=fresh++;leftNew=Math.max(0,leftNew-1);}
    if(prefix==='+')file.added++;
    if(prefix==='-')file.removed++;
    hunk.rows.push(row);
  }
  for(const item of metadata) {
    let found=files.find(file=>file.path===item.path);
    if(!found){found={path:item.path,hunks:[],added:0,removed:0};files.push(found);}
    found.status=item.before_hash===null?'created':item.after_hash===null?'deleted':'modified';
  }
  return files;
}
function diffStats(files){return files.reduce((sum,file)=>({add:sum.add+file.added,remove:sum.remove+file.removed}),{add:0,remove:0});}
function changeDiffModel(change) {
  const cached=changeDiffCache.get(change.id),meta=JSON.stringify(change.files||[]);
  if(cached?.source===change.diff&&cached.meta===meta)return cached.files;
  const files=parseUnifiedDiff(change.diff,change.files||[]);
  changeDiffCache.set(change.id,{source:change.diff,meta,files});return files;
}
function diffRows(files) {
  const rows=[];
  for(const file of files) {
    rows.push({kind:'file',file});
    for(const hunk of file.hunks){rows.push({kind:'hunk',text:hunk.header});for(const row of hunk.rows)rows.push({...row,path:file.path});}
    if(!file.hunks.length)rows.push({kind:'marker',text:file.status==='created'?'Создан пустой файл':file.status==='deleted'?'Удалён пустой файл':'Текстовых изменений нет'});
  }
  return rows;
}
function diffRowHTML(row) {
  if(row.kind==='file')return `<div class="diff-file-heading">${icon('file',15)}<span>${escapeHtml(row.file.path)}</span><small class="positive">+${row.file.added}</small><small class="danger">−${row.file.removed}</small></div>`;
  if(row.kind==='hunk')return `<div class="diff-line hunk"><span></span><span></span><code>${escapeHtml(row.text)}</code></div>`;
  if(row.kind==='marker')return `<div class="diff-line marker"><span></span><span></span><code>${escapeHtml(row.text)}</code></div>`;
  let text=escapeHtml(row.text||' ');
  const extension=String(row.path||'').split('.').pop().toLowerCase();
  const language=({py:'python',js:'javascript',ts:'typescript',json:'json',html:'xml',css:'css',sh:'bash',sql:'sql'})[extension];
  if(language&&window.hljs?.getLanguage(language)&&row.text.length<=4000)text=hljs.highlight(row.text,{language,ignoreIllegals:true}).value;
  return `<div class="diff-line ${row.kind}"><span>${row.old??''}</span><span>${row.fresh??''}</span><code>${text||' '}</code></div>`;
}
function mountDiff(root,model,key='') {
  if(root._diffModel===model&&root._diffKey===key)return;
  const same=root._diffKey===key,top=same?root.scrollTop:0,left=same?root.scrollLeft:0;
  const count=same?(root._diffVisible||800):800;
  root._diffModel=model;root._diffKey=key;root._diffVisible=count;root.replaceChildren();
  const rows=diffRows(Array.isArray(model)?model:[model]);let shown=0;
  const append=limit=>{
    $('.diff-load-more',root)?.remove();
    root.insertAdjacentHTML('beforeend',rows.slice(shown,limit).map(diffRowHTML).join(''));
    shown=Math.min(limit,rows.length);root._diffVisible=shown;
    if(shown<rows.length){const more=document.createElement('button');more.type='button';more.className='secondary diff-load-more';more.textContent=`Показать ещё строки (${rows.length-shown})`;more.onclick=()=>append(shown+800);root.append(more);}
  };
  append(count);root.scrollTop=top;root.scrollLeft=left;
}

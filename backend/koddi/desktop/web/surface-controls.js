/* projectSelectControl и анимация контекста чата из Докки (1620c6a).
   значением остаётся нативный select, меняется только его вид */
const SurfaceUI = (() => {
  const controls = new Map();
  let active = null, tooltipTimer = 0, tooltip = null;
  const reduced = () => document.documentElement.dataset.motion === 'reduced' || matchMedia('(prefers-reduced-motion: reduce)').matches;
  function close(menu = active, restore = false) {
    if (!menu || menu.hidden) return;
    if (active === menu) active = null;
    menu._trigger?.setAttribute('aria-expanded', 'false');
    menu.getAnimations().forEach(animation => animation.cancel());
    if (restore) menu._trigger?.focus({preventScroll:true});
    const finish = () => { if (menu !== active) {if(menu.matches(':popover-open'))menu.hidePopover();menu.hidden = true; menu.style.pointerEvents = ''; } };
    menu.style.pointerEvents = 'none';
    if (reduced()) return finish();
    const animation = menu.animate([{opacity:1,transform:'translateY(0) scale(1)'},{opacity:0,transform:'translateY(-4px) scale(.98)'}],{duration:130,easing:'ease-in'});
    animation.finished.then(finish).catch(()=>{});
  }
  function position(menu, anchor, point) {
    const viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    const width = viewport?.width || innerWidth, height = viewport?.height || innerHeight;
    const rect = anchor?.getBoundingClientRect();
    const x = point?.x ?? rect?.left ?? left + 8;
    const below = point?.y ?? (rect ? rect.bottom + 6 : top + 8);
    const h = menu.offsetHeight, w = menu.offsetWidth;
    const y = rect && below + h > top + height - 8 && rect.top - top > top + height - rect.bottom
      ? rect.top - h - 6 : below;
    menu.style.left = `${Math.max(left+8, Math.min(x,left+width-w-8))}px`;
    menu.style.top = `${Math.max(top+8, Math.min(y,top+height-h-8))}px`;
  }
  function open(menu, trigger, point) {
    if (active && active !== menu) close(active);
    menu.getAnimations().forEach(animation => animation.cancel());
    menu.style.pointerEvents = '';
    menu._trigger = trigger;
    menu._point = point;
    menu.hidden = false;
    if(getComputedStyle(menu).position==='fixed'&&typeof menu.showPopover==='function'){
      menu.popover='manual';if(!menu.matches(':popover-open'))menu.showPopover();
    }
    active = menu;
    trigger?.setAttribute('aria-expanded','true');
    if ((trigger || point) && getComputedStyle(menu).position==='fixed') position(menu, trigger, point);
    if (!reduced()) menu.animate([{opacity:0,transform:'translateY(-5px) scale(.97)'},{opacity:1,transform:'translateY(0) scale(1)'}],{duration:160,easing:'cubic-bezier(.22,1,.36,1)'});
  }
  function menuKeys(event, menu) {
    const options = [...menu.querySelectorAll('button:not(:disabled)')].filter(button=>!button.hidden);
    const current = options.indexOf(document.activeElement);
    if (event.key === 'Escape') {event.preventDefault();event.stopPropagation();close(menu,true);return;}
    let next;
    if (event.key === 'ArrowDown') next = options[(current+1)%options.length];
    if (event.key === 'ArrowUp') next = options[(current+options.length-1)%options.length];
    if (event.key === 'Home') next = options[0];
    if (event.key === 'End') next = options.at(-1);
    if (next) {event.preventDefault();next.focus({preventScroll:true});}
    if (event.key === 'Tab') close(menu);
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && event.key !== ' ') {
      const match = options.find(button=>button.textContent.trim().toLocaleLowerCase().startsWith(event.key.toLocaleLowerCase()));
      if (match) {event.preventDefault();match.focus({preventScroll:true});}
    }
  }
  function sync(select) {
    const control = controls.get(select);
    if (!control) return;
    const {trigger} = control;
    const set = (name, value) => { if (trigger.getAttribute(name) !== value) trigger.setAttribute(name, value); };
    if (trigger.hidden !== select.hidden) trigger.hidden = select.hidden;
    if (trigger.disabled !== select.disabled) trigger.disabled = select.disabled;
    const className = `ui-select-trigger ${select.className}`;
    if (trigger.className !== className) trigger.className = className;
    set('aria-label', select.getAttribute('aria-label') || select.title || 'Выбрать');
    if(!select.getAttribute('aria-label')&&select.closest('label'))set('aria-label', select.closest('label').firstChild?.textContent||'Выбрать');
    const label = select.selectedOptions[0]?.textContent || 'Выбрать';
    const caption = trigger.querySelector('span');
    if (caption.textContent !== label) caption.textContent = label;
    if (trigger.dataset.tooltip !== (select.selectedOptions[0]?.textContent || '')) trigger.dataset.tooltip = select.selectedOptions[0]?.textContent || '';
    if (trigger.hidden || trigger.disabled || !select.isConnected) close(control.menu);
  }
  function enhance(select) {
    if (controls.has(select) || select.multiple || select.size > 1) return;
    const hadFocus=document.activeElement===select;
    const trigger = document.createElement('button');
    trigger.type='button';trigger.innerHTML=`<span></span>${icon('ChevronDown',14)}`;
    trigger.id=(select.id||'choice-'+crypto.randomUUID())+'-trigger';
    trigger.setAttribute('role','combobox');
    trigger.setAttribute('aria-haspopup','listbox');trigger.setAttribute('aria-expanded','false');
    trigger.dataset.selectId=select.id;
    select.dataset.enhanced='true';select.tabIndex=-1;
    select.after(trigger);
    for(const label of [...select.labels])label.htmlFor=trigger.id;
    const menu=document.createElement('div');
    menu.className='ui-select-menu surface-panel';menu.role='listbox';menu.hidden=true;
    menu.id='select-menu-'+crypto.randomUUID();trigger.setAttribute('aria-controls',menu.id);
    controls.set(select,{trigger,menu});
    const draw=()=>{
      menu.replaceChildren();
      menu.setAttribute('aria-label',trigger.getAttribute('aria-label'));
      for(const option of select.options) {
        if(option.hidden)continue;
        const button=document.createElement('button');button.type='button';button.role='option';
        button.disabled=option.disabled || Boolean(option.parentElement?.disabled);button.tabIndex=-1;
        button.setAttribute('aria-selected',String(option.selected));
        const label=document.createElement('span');label.textContent=option.textContent;
        button.append(label);button.insertAdjacentHTML('beforeend',icon('check',15));
        button.onclick=()=>{if(select.disabled)return;select.value=option.value;sync(select);close(menu,true);select.dispatchEvent(new Event('change',{bubbles:true}));};
        menu.append(button);
      }
    };
    trigger.onclick=()=>{
      if(active===menu)return close(menu,true);
      const owner=select.closest('dialog[open]')||document.body;
      if(menu.parentNode!==owner)owner.append(menu);
      draw();menu.style.minWidth=`${Math.min(320,Math.max(180,trigger.getBoundingClientRect().width))}px`;
      open(menu,trigger);(menu.querySelector('[aria-selected="true"]:not(:disabled)')||menu.querySelector('button:not(:disabled)'))?.focus({preventScroll:true});
    };
    trigger.onkeydown=event=>{if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();trigger.click();}};
    menu.onkeydown=event=>menuKeys(event,menu);
    select.addEventListener('change',()=>queueMicrotask(()=>sync(select)));
    sync(select);
    if(hadFocus)trigger.focus({preventScroll:true});
  }
  function refresh() {
    for(const [select,{trigger,menu}] of controls) {
      if(!select.isConnected){close(menu);trigger.remove();menu.remove();controls.delete(select);}
      else sync(select);
    }
  }
  const observer=new MutationObserver(records=>{
    const dirty=new Set(), feed=document.getElementById('feed');
    for(const record of records) {
      // поток ответа даёт сотни записей за кадр, а селектов в сообщениях нет
      if(feed&&feed.contains(record.target))continue;
      const select=record.target.closest?.('select');if(select)dirty.add(select);
      for(const node of record.addedNodes)if(node.nodeType===1){
        if(node.matches('select'))enhance(node);
        node.querySelectorAll('select').forEach(enhance);
      }
    }
    for(const select of dirty){enhance(select);sync(select);}
    for(const [select,{trigger,menu}] of controls)if(!select.isConnected){close(menu);trigger.remove();menu.remove();controls.delete(select);}
  });
  document.querySelectorAll('select').forEach(enhance);
  observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['hidden','disabled','label','selected']});
  document.addEventListener('pointerdown',event=>{if(active&&!active.contains(event.target)&&!active._trigger?.contains(event.target))close();hideTooltip();});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&active){event.preventDefault();event.stopPropagation();close(active,true);}},true);
  let positionFrame=0;
  document.addEventListener('scroll',event=>{
    if(active&&!active.contains(event.target)&&!positionFrame)positionFrame=requestAnimationFrame(()=>{
      positionFrame=0;
      if(active?._trigger?.isConnected&&getComputedStyle(active).position==='fixed')position(active,active._trigger,active._point);
      else if(active)close();
    });
    hideTooltip();
  },true);
  window.addEventListener('resize',()=>{close();hideTooltip();});
  function hideTooltip(){clearTimeout(tooltipTimer);tooltip?.remove();tooltip=null;}
  document.addEventListener('pointerover',event=>{
    const target=event.target.closest('[title],[data-tooltip]');if(!target)return;
    if(target.title){target.dataset.tooltip=target.title;target.removeAttribute('title');}
    hideTooltip();
    tooltipTimer=setTimeout(()=>{
      if(!target.isConnected||!target.dataset.tooltip)return;
      tooltip=document.createElement('div');tooltip.className='ui-tooltip';tooltip.role='tooltip';tooltip.textContent=target.dataset.tooltip;
      (target.closest('dialog[open]')||document.body).append(tooltip);
      if(typeof tooltip.showPopover==='function'){tooltip.popover='manual';tooltip.showPopover();}
      position(tooltip,target);
    },450);
  });
  document.addEventListener('pointerout',event=>{if(event.target.closest('[data-tooltip]')&&!event.target.closest('[data-tooltip]').contains(event.relatedTarget))hideTooltip();});
  document.addEventListener('close',event=>{
    if(active?.closest('dialog')===event.target){
      active.getAnimations().forEach(animation=>animation.cancel());
      if(active.matches(':popover-open'))active.hidePopover();
      active.hidden=true;active._trigger?.setAttribute('aria-expanded','false');active=null;
    }
    hideTooltip();
  },true);
  return {open,close,refresh,menuKeys};
})();

// раскрытие плана оставляет видимым начало документа
document.addEventListener('click', event => {
  const summary=event.target.closest('summary');
  const details=summary?.parentElement;
  if(!details?.matches('.nav-more,.plan-document details,.project-group'))return;
  if(event.target.closest('button,a,input'))return;
  if(details.closest('.plan-document')) {
    event.preventDefault();
    const body=details.nextElementSibling, start=body.getBoundingClientRect().height;
    details._disclosureAnimation?.cancel();
    details.open=!details.open;
    summary.setAttribute('aria-expanded',String(details.open));body.inert=!details.open;
    const end=body.getBoundingClientRect().height;
    if(document.documentElement.dataset.motion==='reduced') {follow();return;}
    const animation=body.animate([{height:`${start}px`,maxHeight:`${start}px`},{height:`${end}px`,maxHeight:`${end}px`}],{duration:220,easing:'cubic-bezier(.22,1,.36,1)'});
    details._disclosureAnimation=animation;
    animation.finished.then(()=>{if(details.isConnected)follow();}).catch(()=>{});
    return;
  }
  if(document.documentElement.dataset.motion==='reduced')return;
  event.preventDefault();
  const start=details.getBoundingClientRect().height;
  const expanding=details._targetOpen===undefined?!details.open:!details._targetOpen;
  details._targetOpen=expanding;
  details.dataset.animOpen=String(expanding);
  details._disclosureAnimation?.cancel();
  if(expanding)details.open=true;
  const end=expanding?details.scrollHeight:summary.getBoundingClientRect().height;
  details.style.overflow='hidden';
  const animation=details.animate([{height:`${start}px`},{height:`${end}px`}],{duration:190,easing:'cubic-bezier(.22,1,.36,1)'});
  details._disclosureAnimation=animation;
  animation.finished.then(()=>{
    if(details._disclosureAnimation!==animation)return;
    details.open=expanding;details.style.overflow='';delete details._targetOpen;delete details.dataset.animOpen;
  }).catch(()=>{});
});

// действия редактирования держим внутри окна приложения
document.addEventListener('contextmenu', event => {
  if(event.target.closest('.chat-row'))return;
  event.preventDefault();
  const field=event.target.closest('textarea,input:not([type="file"]):not([type="checkbox"]):not([type="radio"])');
  const link=event.target.closest('a[href]');
  const selection=field?field.value.slice(field.selectionStart||0,field.selectionEnd||0):String(getSelection());
  if(!field&&!selection&&!link){SurfaceUI.close();return;}
  const menu=document.createElement('div');menu.className='surface-panel text-context';menu.role='menu';menu.hidden=true;
  (field?.closest('dialog[open]')||event.target.closest('dialog[open]')||document.body).append(menu);
  const add=(title,symbol,action)=>{
    const button=document.createElement('button');button.type='button';button.role='menuitem';
    button.innerHTML=icon(symbol)+`<span>${title}</span>`;
    button.onclick=guarded(async()=>{SurfaceUI.close(menu);await action();});menu.append(button);
  };
  if(selection)add('Копировать','copy',()=>navigator.clipboard.writeText(selection));
  if(link)add('Копировать ссылку','link',()=>navigator.clipboard.writeText(link.href));
  const replace=value=>{field.focus();if(field.selectionStart===null)field.value=value;else field.setRangeText(value,field.selectionStart,field.selectionEnd,'end');field.dispatchEvent(new Event('input',{bubbles:true}));};
  if(field&&!field.readOnly&&!field.disabled){
    if(selection)add('Вырезать','Scissors',async()=>{await navigator.clipboard.writeText(selection);replace('');});
    add('Вставить','ClipboardPaste',async()=>replace(await navigator.clipboard.readText()));
  }
  if(field)add('Выделить всё','TextSelect',()=>{field.focus();field.select();});
  menu.onkeydown=event=>SurfaceUI.menuKeys(event,menu);
  SurfaceUI.open(menu,field||null,{x:event.clientX,y:event.clientY});menu.querySelector('button')?.focus();
  const dispose=new MutationObserver(()=>{if(menu.hidden){dispose.disconnect();menu.remove();}});
  dispose.observe(menu,{attributes:true,attributeFilter:['hidden']});
});

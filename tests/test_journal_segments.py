"""Изолированные проверки публичного журнала без backend и профиля пользователя."""

from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright


WEB = Path(__file__).resolve().parents[1] / "backend/koddi/desktop/web"


def test_keepalive_policy_and_server_charge_validation(page):
    source=(WEB/'product-ui.js').read_text(encoding='utf-8')
    page.add_script_tag(content=source[source.index('function codingKeepaliveDelay('):source.index('function scheduleCodingKeepalive(')])
    result=page.evaluate('''() => {
      const now=Date.now(), cache={valid_until:new Date(now+30*60000).toISOString(),prompt_tokens:5000};
      return [codingKeepaliveDelay(cache,now,0,true,false),
        codingKeepaliveDelay(cache,now,0,false,false),codingKeepaliveDelay(cache,now,91*60000,true,false),
        codingKeepaliveDelay(cache,now,0,true,true),codingKeepaliveDelay({...cache,prompt_tokens:3000},now,0,true,false),
        codingKeepaliveDelay({...cache,valid_until:'bad'},now,0,true,false),
        codingKeepaliveDelay({...cache,valid_until:new Date(now-1).toISOString()},now,0,true,false)];
    }''')
    assert result==[26*60000,None,None,None,None,None,None]


def test_compaction_has_separate_rows_and_pauses_work_timer(page):
    result=page.evaluate('''() => {
      const metadata={status:'completed',turn:{created_at:at(0),completed_at:at(40),timeline:[
        {id:1,sequence:1,created_at:at(0),kind:'agent.status',payload:{text:'Работаю'}},
        {id:2,sequence:2,created_at:at(10),kind:'context.compacting',payload:{text:'Сжимаю контекст…'}},
        {id:3,sequence:3,created_at:at(25),kind:'context.compacted',payload:{text:'Контекст сжат'}},
        {id:4,sequence:4,created_at:at(26),kind:'agent.status',payload:{text:'Продолжаю'}}]}};
      item.metadata=metadata;draw();
      return {duration:journalDuration(metadata.turn),
        types:journalSegments(orderedJournalEntries(metadata),metadata).map(entry=>entry.type),
        rows:[...document.querySelectorAll('.journal-timeline > .journal-stage')].map(node=>node.textContent)};
    }''')
    assert result['duration']=='25 сек.'
    assert result['types']==['segment','compaction','compaction','segment']
    assert result['rows']==['Сжимаю контекст…','Контекст сжат']


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1100, "height": 800})
    context.route("**/*", lambda route: route.abort())
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_content('<main id="feed"><section id="journal" class="assistant"></section></main>')
    page.add_style_tag(content=(WEB / "app.css").read_text(encoding="utf-8"))
    page.add_style_tag(content=(WEB / "local-journal.css").read_text(encoding="utf-8"))
    for name in ("lucide", "marked", "purify"):
        page.add_script_tag(content=(WEB / f"vendor/{name}.js").read_text(encoding="utf-8"))
    app = (WEB / "app.js").read_text(encoding="utf-8")
    page.add_script_tag(content="""
      const $ = (selector, root=document) => root.querySelector(selector);
      const state = {project: null};
      const guarded = fn => fn;
      const follow = () => {};
      let syncs = 0;
      const scheduleChatSync = () => syncs++;
      window.koddiProductScope = 'development';
    """)
    for start, end in (
        ("const iconNames =", "async function api("),
        ("function renderMarkdown(", "function isNearFeedBottom("),
        ("function actionText(", "function addActivity("),
    ):
        page.add_script_tag(content=app[app.index(start):app.index(end)])
    for name in ("work-progress.js", "stream-markdown.js", "local-journal.js"):
        page.add_script_tag(content=(WEB / name).read_text(encoding="utf-8"))
    page.add_script_tag(content="""
      const at = seconds => `2026-10-16T10:00:${String(seconds).padStart(2,'0')}Z`;
      const action = (id, sequence, seconds, event='tool.result') => ({event, sequence,
        created_at:at(seconds), data:{id,name:'file.read',arguments:{path:`${id}.js`},result:{ok:true}}});
      const comment = (id, sequence, seconds, text='Проверяю результат.') => ({id,role:'assistant',
        created_at:at(seconds),content:text,metadata:{channel:'commentary',sequence,status:'completed'}});
      const fixture = () => ({id:'journal-7',metadata:{kind:'journal',status:'running',
        turn:{id:7,status:'running',created_at:at(0),timeline:[
          {id:1,sequence:1,created_at:at(0),kind:'agent.status',payload:{text:'Изучаю проект'}},
          {id:2,sequence:7,created_at:at(7),kind:'agent.status',payload:{text:'Проверяю файлы'}}]},
        work_items:[
          {id:10,role:'assistant',created_at:at(1),content:'',metadata:{sequence:2,kind:'activity',events:[action('a',2,1),action('b',3,2)]}},
          comment(11,4,4,'Контекст получен.\\n\\n```js\\nconst x = 1;\\n```'),
          {id:12,role:'assistant',created_at:at(5),content:'',metadata:{sequence:5,kind:'activity',events:[action('c',5,5)]}}
        ]}});
      let item = fixture();
      window.clockNow = Date.parse(at(9));
      Date.now = () => window.clockNow;
      const draw = () => renderLocalJournal($('#journal'),item,false);
      draw();
    """)
    yield page
    assert not errors, errors
    context.close()


def test_running_interleaves_segments_and_public_commentary(page):
    assert page.evaluate("""() => [...$('.journal-timeline').children].map(node =>
      node.classList.contains('journal-segment') ? 'segment' : 'commentary')""") == [
        "segment", "commentary", "segment"
    ]
    assert page.locator(".journal-segment.is-running").count() == 1
    assert page.locator(".journal-segment").first.locator(".journal-duration").inner_text() == "4 сек."
    assert page.locator(".journal-segment").last.locator(".journal-summary-copy").inner_text() == "Проверяю файлы"
    page.locator(".journal-segment summary").first.click()
    expect(page.locator(".journal-segment").first.locator(".activity-line")).to_have_count(2)
    assert page.locator(".journal-segment").first.locator(".activity-line").count() == 2
    assert page.locator(".journal-segment").first.locator(".journal-stage").count() == 1


def test_closed_status_stage_uses_past_tense_but_keeps_original_details(page):
    page.evaluate("""() => {
      item.metadata.work_items=[comment(11,4,4,'Нашёл причину.')];
      item.metadata.turn.timeline=[
        {id:1,sequence:1,created_at:at(0),kind:'agent.step',payload:{text:'Думаю…'}},
        {id:2,sequence:7,created_at:at(7),kind:'agent.status',payload:{text:'Проверяю исправление'}}];draw();
    }""")
    stages = page.locator(".journal-segment")
    # общее «Думаю…» без действий отдельной строкой не показывается
    assert stages.count() == 1
    assert stages.first.locator(".journal-summary-copy").text_content() == "Проверяю исправление"
    stages.first.locator("summary").click()
    expect(stages.first.locator(".journal-stage")).to_have_text("Проверяю исправление")
    page.evaluate("item.metadata.status='cancelled';item.metadata.turn.completed_at=at(10);draw()")
    assert stages.last.locator(".journal-summary-copy").text_content() == "Работал"


@pytest.mark.parametrize("status", ["cancelled", "failed", "completed"])
def test_no_final_keeps_chronology_disclosures_and_dom(page, status):
    page.locator(".journal-segment summary").first.click()
    expect(page.locator(".activity-line")).to_have_count(2)
    page.evaluate("""() => {
      window.savedSegment = $('.journal-segment');
      window.savedComment = $('.journal-commentary');
      window.savedCode = $('.journal-commentary code');
      window.savedAction = $('.activity-line');
    }""")
    page.evaluate("""status => {
      item.metadata.status=status; item.metadata.turn.status=status;
      item.metadata.turn.completed_at=at(10); draw(); draw();
    }""", status)
    assert page.evaluate("""() => savedSegment === $('.journal-segment') && savedSegment.open &&
      savedComment === $('.journal-commentary') && savedCode === $('.journal-commentary code') &&
      savedAction === $('.activity-line')""")
    assert page.locator(".journal-completed").count() == 0
    assert page.locator(".is-running").count() == 0
    assert page.locator(".journal-segment").last.locator(".journal-duration").inner_text() == "5 сек."
    assert page.locator("#journal > .journal-window > .journal-timeline").count() == 1


@pytest.mark.parametrize("status", ["cancelled", "failed", "running", "completed"])
def test_only_success_with_real_final_collapses_all_work(page, status):
    page.evaluate("""status => {
      window.savedComment = $('.journal-commentary');
      item.metadata.status=status; item.metadata.turn.status=status;
      item.metadata.turn.completed_at=at(10);
      item.metadata.messages=[{id:15,role:'assistant',content:'Готово.',created_at:at(10),
        metadata:{channel:'final',status:'completed',sequence:10}}]; draw();
    }""", status)
    if status == "completed":
        assert page.locator(".journal-completed").count() == 1
        assert not page.locator(".journal-completed").evaluate("node=>node.open")
        page.locator(".journal-completed > summary").click()
        page.evaluate("draw()")
        assert page.locator(".journal-completed").evaluate("node=>node.open")
        assert "10 сек." in page.locator(".journal-completed > summary").inner_text()
    else:
        assert page.locator(".journal-completed").count() == 0
    assert page.evaluate("savedComment === $('.journal-commentary')")


@pytest.mark.parametrize("content,final_status", [
    ("  ", "completed"), ("Ответ", "running"), ("Ответ", "failed"),
    ("Ответ", "truncated"), ("Ответ", "superseded"), ("Ответ", None),
])
def test_empty_or_interrupted_final_does_not_collapse(page, content, final_status):
    page.evaluate("""([content,status]) => {
      item.metadata.status='completed';
      item.metadata.messages=[{id:99,content,metadata:{channel:'final',status}}]; draw();
    }""", [content, final_status])
    assert page.locator(".journal-completed").count() == 0


def test_restore_sequence_timestamps_deduplication_and_private_channels(page):
    result = page.evaluate("""() => {
      const first=comment(21,undefined,2,'Публичный текст');
      const metadata={messages:[first,first,
        {id:22,content:'PRIVATE',metadata:{channel:'analysis',events:[action('private',1,0)]}},
        {id:23,role:'tool',content:'PRIVATE',metadata:{channel:'commentary'}}],
        work_items:[{id:20,metadata:{sequence:1,events:[action('a',1,0,'tool.call'),action('a',1,0)]}}],
        turn:{timeline:[
          {id:2,sequence:3,created_at:at(3),kind:'agent.status',payload:{text:'После комментария'}},
          {id:1,sequence:1,created_at:at(0),kind:'reasoning.delta',payload:{text:'PRIVATE'}},
          {id:3,sequence:4,created_at:at(4),kind:'agent.status',payload:{text:'PRIVATE',channel:'analysis'}}]}};
      const entries=orderedJournalEntries(metadata);
      return {types:entries.map(entry=>entry.type),text:JSON.stringify(entries),
        result:entries[0].event.event,blocks:journalSegments(entries,metadata).map(block=>block.type)};
    }""")
    assert result["types"] == ["action", "commentary", "status"]
    assert result["blocks"] == ["segment", "commentary", "segment"]
    assert result["result"] == "tool.result"
    assert "PRIVATE" not in result["text"]


def test_message_start_sequence_does_not_reorder_later_tools_before_statuses(page):
    result = page.evaluate("""() => {
      const tool=(id,event,seconds)=>({event,data:{id,name:'file.read',created_at:at(seconds),
        arguments:{path:id+'.js'},result:{ok:true}}});
      const metadata={status:'running',turn:{created_at:at(0),timeline:[
        {id:1,sequence:1,created_at:at(0),kind:'agent.step',payload:{}},
        {id:2,sequence:3,created_at:at(3),kind:'agent.status',payload:{text:'Статус между инструментами'}},
        {id:3,sequence:5,created_at:at(5),kind:'context.compacted',payload:{text:'Контекст сохранён'}}]},
        messages:[{id:1,created_at:at(0),metadata:{sequence:2,started_at:at(1),events:[
          tool('first','tool.call',1),tool('first','tool.result',2),
          tool('second','tool.call',4),tool('second','tool.result',7)]}},
          {id:2,content:'Комментарий',created_at:at(0),metadata:{channel:'commentary',sequence:6,
            started_at:at(8),completed_at:at(9),status:'completed'}}]};
      const entries=orderedJournalEntries(metadata),blocks=journalSegments(entries,metadata);
      item.metadata=metadata; draw();
      return {keys:entries.map(entry=>entry.key),duration:$('.journal-duration').textContent,
        title:$('.journal-summary-copy').textContent,blocks:blocks.map(block=>block.type)};
    }""")
    assert result == {
        "keys": ["status:1", "action:first", "status:2", "action:second", "context:3", "message:2:text"],
        "duration": "5 сек.", "title": "Прочитал 2 файла", "blocks": ["segment", "compaction", "commentary"],
    }


def test_out_of_order_call_result_restore_uses_call_start_and_latest_result(page):
    result = page.evaluate("""() => {
      const metadata={messages:[
        {id:2,metadata:{events:[action('a',8,8)]}},
        {id:1,metadata:{events:[action('a',1,1,'tool.call')]}}]};
      const entry=orderedJournalEntries(metadata)[0];
      return {sequence:entry.sequence,time:entry.created_at,event:entry.event.event,updated:entry.updated_at};
    }""")
    assert result == {"sequence": 1, "time": "2026-10-16T10:00:01Z", "event": "tool.result", "updated": "2026-10-16T10:00:08Z"}


def test_projection_merges_timeline_keeps_final_once_and_separates_turns(page):
    result = page.evaluate("""() => {
      const messages=[{id:1,role:'user',content:'Запрос'},
        {...comment(2,2,2),metadata:{channel:'commentary',turn_id:7,turn:{id:7,status:'completed',timeline:item.metadata.turn.timeline}}},
        {id:3,role:'assistant',content:'Готово',metadata:{channel:'final',status:'completed',turn_id:7,turn:{id:7,status:'completed'}}},
        {id:4,role:'assistant',content:'PRIVATE',metadata:{channel:'analysis',turn_id:7}},
        {...comment(5,1,1),metadata:{channel:'commentary',turn_id:8,turn:{id:8,status:'cancelled'}}}];
      const before=JSON.stringify(messages),projected=projectLocalJournal(messages);
      return {ids:projected.map(value=>value.id),same:before===JSON.stringify(messages),
        timeline:projected[1].metadata.turn.timeline.length,
        final:orderedJournalEntries(projected[1].metadata).filter(entry=>entry.type==='final').length,
        private:JSON.stringify(projected).includes('PRIVATE')};
    }""")
    assert result == {"ids": [1, "journal-7", 3, "journal-8"], "same": True, "timeline": 2, "final": 1, "private": False}


def test_live_header_updates_only_current_segment_and_terminal_syncs(page):
    result = page.evaluate("""() => {
      const first=$('.journal-segment .journal-summary-copy').textContent;
      handleLocalJournal('tool.call',{turn_id:7,id:'z',name:'file.read',arguments:{path:'next.js'}});
      const result={first:first===$('.journal-segment .journal-summary-copy').textContent,
        last:[...document.querySelectorAll('.journal-segment .journal-summary-copy')].at(-1).textContent};
      handleLocalJournal('turn.cancelled',{turn_id:7}); return {...result,syncs};
    }""")
    assert result == {"first": True, "last": "Читаю: next.js", "syncs": 1}


def test_safe_failure_reason_is_separate_plain_text_and_reuses_node(page):
    reason = 'Источник недоступен. <img src=x onerror="window.leaked=true">'
    page.evaluate("""reason => {
      item.metadata.status='failed'; item.metadata.turn.completed_at=at(10);
      item.metadata.turn.summary=reason; draw(); window.reasonNode=$('.journal-error-reason'); draw();
    }""", reason)
    assert page.locator(".journal-error-reason").inner_text() == reason
    assert page.evaluate("reasonNode===$('.journal-error-reason') && !window.leaked")
    assert page.locator(".journal-error-reason img").count() == 0
    assert page.evaluate("$('.journal-error-reason').previousElementSibling.classList.contains('journal-error')")
    page.evaluate("item.metadata.turn.summary=''; draw()")
    assert page.locator(".journal-error-reason").count() == 0


def test_waiting_heartbeats_update_only_current_elapsed_without_rendering(page):
    page.locator(".journal-segment > summary").last.click()
    expect(page.locator(".activity-line")).to_have_count(1)
    result = page.evaluate("""() => {
      const current=$('.journal-segment[data-journal-active="true"]');
      const comment=$('.journal-commentary'),code=comment.querySelector('code'),action=current.querySelector('.activity-line');
      let renders=0; const original=renderMarkdown; renderMarkdown=(...args)=>{renders++;return original(...args)};
      window.clockNow=Date.parse(at(12)); handleLocalJournal('agent.waiting',{seconds:1});
      const first=current.querySelector('.journal-duration').textContent;
      window.clockNow=Date.parse(at(15)); handleLocalJournal('tool.waiting',{turn_id:7,seconds:2,created_at:at(5)});
      const second=current.querySelector('.journal-duration').textContent;
      return {first,second,previous:$('.journal-segment .journal-duration').textContent,renders,syncs,
        same:current.open && action===current.querySelector('.activity-line') && code===$('.journal-commentary code') && comment===$('.journal-commentary')};
    }""")
    assert result == {"first": "7 сек.", "second": "10 сек.", "previous": "4 сек.", "renders": 0, "syncs": 0, "same": True}
    assert page.evaluate("""() => {
      const duration=$('.journal-segment[data-journal-active="true"] .journal-duration');
      const before=duration.textContent;
      window.clockNow=Date.parse(at(20)); handleLocalJournal('agent.waiting',{turn_id:999});
      return before===duration.textContent;
    }""")
    page.evaluate("""() => {
      item.metadata.status='cancelled'; item.metadata.turn.completed_at=at(13); draw();
      window.clockNow=Date.parse(at(30)); handleLocalJournal('agent.waiting',{turn_id:7});
    }""")
    assert page.locator(".journal-segment").last.locator(".journal-duration").inner_text() == "8 сек."


def test_terminal_event_stops_elapsed_before_history_sync(page):
    assert page.evaluate("""() => {
      handleLocalJournal('turn.failed',{turn_id:7});
      const duration=$('.journal-segment:last-child .journal-duration'),before=duration.textContent;
      window.clockNow=Date.parse(at(40)); handleLocalJournal('tool.waiting',{turn_id:7,seconds:35});
      return duration.textContent===before && !$('.journal-segment.is-running') && syncs===1;
    }""")


def test_new_public_stage_requests_snapshot_even_when_label_repeats(page):
    assert page.evaluate("""() => {
      handleLocalJournal('agent.step',{turn_id:7});
      handleLocalJournal('agent.status',{turn_id:7,text:'Думаю…'});
      handleLocalJournal('context.compacted',{turn_id:7});
      return syncs===3 && $('.journal-segment[data-journal-active="true"] .journal-summary-copy').textContent==='Контекст сохранён';
    }""")


def test_action_result_update_preserves_open_row_and_bounded_signature(page):
    page.evaluate("""() => {
      item.metadata.work_items[0].metadata.events[0]=action('a',2,1,'tool.call'); draw();
    }""")
    page.locator(".journal-segment summary").first.click()
    expect(page.locator(".activity-line")).to_have_count(2)
    assert page.evaluate("""() => {
      const details=$('.journal-segment'),row=$('.activity-line');
      item.metadata.work_items[0].metadata.events.push(action('a',9,9));
      item.metadata.work_items[0].metadata.events.at(-1).data.result.private='x'.repeat(1000000);
      draw(); const signature=localJournalSignature(item);
      return details.open && row===$('.activity-line') && row.textContent.includes('Прочитан') && signature.length<5000;
    }""")


def test_history_is_paged_without_losing_action_access(page):
    page.evaluate("""() => {
      item.metadata.turn.timeline=[];
      item.metadata.work_items=[{id:1,metadata:{events:Array.from({length:260},(_,i)=>action(String(i),i,i%60))}}]; draw();
    }""")
    page.locator(".journal-segment summary").click()
    expect(page.locator(".activity-line")).to_have_count(100)
    assert page.locator(".journal-pagination button svg").count() == 2
    page.get_by_role("button", name="Предыдущие действия").click()
    assert page.locator(".activity-line").count() == 100
    page.get_by_role("button", name="Предыдущие действия").click()
    assert "0.js" in page.locator(".activity-line").first.inner_text()
    assert page.get_by_role("button", name="Предыдущие действия").is_disabled()
    assert page.evaluate("$('.journal-segment')._segmentView.rows.size") == 100


def test_block_paging_keeps_latest_visible_and_restores_disclosure(page):
    page.evaluate("""() => {
      item.metadata.turn.timeline=[]; item.metadata.work_items=[];
      for(let i=0;i<130;i++) {
        item.metadata.work_items.push({id:`a${i}`,metadata:{events:[action(String(i),2*i,0)]}});
        item.metadata.work_items.push(comment(`c${i}`,2*i+1,1));
      }
      draw();
    }""")
    assert page.locator(".journal-timeline > *").count() == 100
    assert "129.js" in page.locator(".journal-segment").last.inner_text()
    page.locator(".journal-segment > summary").first.click()
    page.locator(".journal-window > .journal-pagination").get_by_role("button", name="Предыдущие действия").click()
    page.locator(".journal-window > .journal-pagination").get_by_role("button", name="Следующие действия").click()
    assert page.locator(".journal-segment").first.evaluate("node=>node.open")
    assert page.evaluate("$('#journal')._journalView.blocks.size") == 50


def test_pager_does_not_follow_new_blocks_while_user_reads_earlier_content(page):
    result = page.evaluate("""() => {
      item.metadata.turn.timeline=[]; item.metadata.work_items=[];
      for(let i=0;i<150;i++) item.metadata.work_items.push(comment(i,i,i%60,'Комментарий '+i));
      draw();
      const timeline=$('.journal-timeline'),first=timeline.firstElementChild,last=timeline.lastElementChild;
      const offset=$('#journal')._journalView.offset;
      state.followFeed=false;
      const feed=$('#feed'); feed.style.height='400px'; feed.style.overflow='auto'; feed.scrollTop=120;
      const top=feed.scrollTop,position=first.getBoundingClientRect().top;
      for(let i=150;i<155;i++) item.metadata.work_items.push(comment(i,i,i%60,'Новый комментарий '+i));
      draw();
      return {same:first===timeline.firstElementChild && last===timeline.lastElementChild,
        offset:offset===$('#journal')._journalView.offset,scroll:top===feed.scrollTop,
        position:position===first.getBoundingClientRect().top,count:timeline.children.length};
    }""")
    assert result == {"same": True, "offset": True, "scroll": True, "position": True, "count": 100}


def test_expanded_segment_pager_keeps_rows_while_feed_follow_is_disabled(page):
    page.evaluate("""() => {
      item.metadata.turn.timeline=[];
      item.metadata.work_items=[{id:1,metadata:{events:Array.from({length:150},(_,i)=>action(String(i),i,i%60))}}]; draw();
    }""")
    page.locator(".journal-segment > summary").click()
    expect(page.locator(".activity-line")).to_have_count(100)
    assert page.evaluate("""() => {
      state.followFeed=false;
      const first=$('.activity-line'),last=[...document.querySelectorAll('.activity-line')].at(-1);
      const offset=$('.journal-segment')._segmentView.offset;
      item.metadata.work_items[0].metadata.events.push(action('new',151,9)); draw();
      return first===$('.activity-line') && last===[...document.querySelectorAll('.activity-line')].at(-1) &&
        offset===$('.journal-segment')._segmentView.offset;
    }""")


@pytest.mark.parametrize("status", ["cancelled", "failed", "completed"])
def test_existing_chat_renderer_reuses_journal_node_and_projects_final_once(page, status):
    app = (WEB / "app.js").read_text(encoding="utf-8")
    page.add_script_tag(content=app[app.index("function createMessage("):app.index("function queueStreamDelta(")])
    page.evaluate("""() => {
      $('#feed').replaceChildren();
      state.messages=new Map(); state.messageSources=new Map(); state.streamBuffers=new Map();
      window.raw=[{id:1,role:'user',content:'Проверь проект'},...item.metadata.work_items.map(message=>({
        ...message,metadata:{...message.metadata,turn_id:7,turn:item.metadata.turn}}))];
      renderChatMessages(raw);
      window.journalNode=$('.local-work'); window.codeNode=$('.journal-commentary code');
    }""")
    page.locator(".journal-segment > summary").first.click()
    expect(page.locator(".activity-line")).to_have_count(2)
    page.evaluate("""status => {
      for(const message of raw.slice(1)) message.metadata.turn={...message.metadata.turn,status,completed_at:at(10)};
      if(status==='completed') raw.push({id:99,role:'assistant',content:'Готово.',created_at:at(10),
        metadata:{turn_id:7,channel:'final',status:'completed',sequence:10,turn:raw[1].metadata.turn}});
      renderChatMessages(raw); renderChatMessages(raw);
    }""", status)
    assert page.evaluate("journalNode===$('.local-work') && codeNode===$('.journal-commentary code') && $('.journal-segment').open")
    assert page.locator(".message.assistant").count() == (1 if status == "completed" else 0)
    assert page.locator(".journal-completed").count() == (1 if status == "completed" else 0)


@pytest.mark.parametrize("width", [360, 1100])
def test_open_journal_layout(page, tmp_path, width):
    page.set_viewport_size({"width": width, "height": 800})
    page.locator(".journal-segment > summary").first.click()
    expect(page.locator(".activity-line")).to_have_count(2)
    assert page.evaluate("""() => [...document.querySelectorAll('.journal-segment > summary')].every(summary=>{
      const title=summary.querySelector('.journal-summary-copy').getBoundingClientRect();
      const duration=summary.querySelector('.journal-duration').getBoundingClientRect();
      return title.right<=duration.left && summary.getBoundingClientRect().right<=innerWidth;
    })""")
    page.screenshot(path=str(tmp_path / f"journal-open-{width}.png"), animations="disabled")


@pytest.mark.parametrize("width", [360, 1100])
def test_layout_reduced_motion_and_untrusted_text(page, tmp_path, width):
    page.set_viewport_size({"width": width, "height": 800})
    page.emulate_media(reduced_motion="reduce")
    page.evaluate("""() => {
      item.metadata.work_items[1].content='<script>window.leaked=true</script><img src=x onerror="window.leaked=true">\\n\\n'+ 'ДлинныйЗаголовок'.repeat(20);
      item.metadata.turn.timeline.at(-1).payload.text='<img onerror=alert(1)>'+ 'Проверяю'.repeat(30); draw();
    }""")
    assert not page.evaluate("!!window.leaked")
    assert page.locator("#journal script,#journal [onerror],#journal img").count() == 0
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    assert page.evaluate("""() => [...document.querySelectorAll('.local-work *')].every(node =>
      getComputedStyle(node).animationName==='none' && parseFloat(getComputedStyle(node).transitionDuration)===0)""")
    page.screenshot(path=str(tmp_path / f"journal-{width}.png"), animations="disabled")


def test_running_text_goes_to_journal_only_after_work_and_summary_is_specific(page):
    result = page.evaluate("""() => {
      const message = (id, content, meta) => ({id, role: 'assistant', content,
        metadata: {turn_id: 1, turn: {id: 1, status: 'running', created_at: at(0)}, ...meta}});
      const read = (id, seq) => [
        {event: 'tool.call', sequence: seq, created_at: at(seq), data: {id, name: 'file.read', arguments: {path: id + '.py'}}},
        {event: 'tool.result', sequence: seq + .5, created_at: at(seq + 1), data: {id, name: 'file.read', arguments: {path: id + '.py'}, result: {ok: true}}}];
      const user = {id: 1, role: 'user', content: 'x', metadata: {}};
      // первый шаг без работы остаётся обычным ответом, но журнал знает, что текст печатается
      const first = projectLocalJournal([user, message(2, 'Печатаю', {status: 'running',
        turn: {id: 1, status: 'running', created_at: at(0), timeline: [{id: 1, sequence: 1, created_at: at(0), kind: 'agent.step', payload: {text: 'Думаю…'}}]}})]);
      // после работы живой текст идёт внутри журнала
      const later = projectLocalJournal([user,
        message(2, 'Читаю.', {channel: 'commentary', status: 'completed', events: read('a', 2)}),
        message(3, 'Печатаю дальше', {status: 'running'})]);
      const journal = later.find(item => item.metadata?.kind === 'journal');
      const entries = journalSummary([
        ...read('a', 2), ...read('b', 4),
        {event: 'tool.call', sequence: 6, created_at: at(6), data: {id: 'c', name: 'shell.run', arguments: {command: 'pytest'}}},
        {event: 'tool.result', sequence: 6.5, created_at: at(7), data: {id: 'c', name: 'shell.run', arguments: {command: 'pytest'}, result: {ok: false}}},
      ].map((event, index) => ({type: 'action', key: 'k' + index, event})));
      return {
        firstKinds: first.map(item => item.metadata?.kind || item.role),
        firstLive: first.find(item => item.metadata?.kind === 'journal')?.metadata.live_text,
        laterKinds: later.map(item => item.metadata?.kind || item.role),
        provisional: journal.metadata.work_items.find(item => item.id === 3)?.metadata.provisional,
        summary: entries.text,
      };
    }""")
    assert result["firstKinds"] == ["user", "journal", "assistant"] and result["firstLive"] is True, result
    assert result["laterKinds"] == ["user", "journal"], result
    assert result["provisional"] is True, result
    assert result["summary"] == "Прочитал 2 файла, выполнил 1 команду (1 с ошибкой)", result

"""Проверка HTML-оболочки и страницы настроек в браузере на отдельном профиле."""

from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
from importlib import import_module
from pathlib import Path

os.environ["KODDI_DATA_DIR"] = tempfile.mkdtemp(prefix="koddi-web-smoke-")
os.environ["KODDI_INTERNAL_TEST"] = "1"

import uvicorn
import httpx
from playwright.sync_api import sync_playwright

from koddi.api import create_app


def main():
    project_base = Path(tempfile.mkdtemp(prefix="koddi-created-projects-"))
    import_module("koddi.api.app").project_directory = lambda: project_base
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("Сервер не запустился")
    output = Path(tempfile.mkdtemp(prefix="koddi-ui-"))
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            # wait_for_function выполняет тестовые выражения, поэтому CSP обходим только здесь
            page = browser.new_page(viewport={"width": 1440, "height": 900}, bypass_csp=True)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}/ui/", wait_until="domcontentloaded")
            page.wait_for_function("window.koddiProductScope === 'development'")
            page.wait_for_function("!document.body.classList.contains('booting')")
            sanitized = page.evaluate("""() => {
                const node=document.createElement('div');
                renderMarkdown(node, '# Заголовок\\n\\n**Жирный**\\n\\n|a|b|\\n|---|---|\\n|1|2|\\n\\n<script>window.unsafe=true</script><img src=x onerror=alert(1)>');
                return {heading:!!node.querySelector('h1'),table:!!node.querySelector('table'),strong:!!node.querySelector('strong'),safe:!node.querySelector('script,[onerror],iframe')};
            }""")
            assert all(sanitized.values()), sanitized
            stream_check = page.evaluate("""async () => {
                state.chat={id:99999};state.page='chat';
                const prefix='**Начало**\\n\\n';
                renderChatMessages([
                    {id:901,role:'assistant',content:'**Готовый ответ**',metadata:{status:'completed'}},
                    {id:902,role:'assistant',content:prefix,metadata:{status:'running'}}
                ]);
                queueStreamDelta(99999,{message_id:902,offset:prefix.length,content:'продолжение'});
                await new Promise(requestAnimationFrame);
                const result={
                    raw:state.streamBuffers.get('902').content===prefix+'продолжение',
                    preserved:!!state.messages.get('901').querySelector('strong')
                };
                newChat();
                return result;
            }""")
            assert all(stream_check.values()), stream_check
            journal_check = page.evaluate("""() => {
                newChat();state.chat={id:99999};state.page='chat';
                const at=n=>`2026-10-15T10:00:0${n}Z`;
                const turn={id:77,status:'running',created_at:at(0),completed_at:null,timeline:[
                  {id:1,sequence:1,created_at:at(0),kind:'agent.step',payload:{}},
                  {id:2,sequence:7,created_at:at(4),kind:'agent.step',payload:{}}]};
                const metadata=(channel,kind='text',sequence=1,seconds=0)=>({turn_id:77,turn,channel,kind,sequence,started_at:at(seconds),status:'completed'});
                const input=[{id:901,role:'user',content:'Изучи проект',metadata:{turn_id:77,turn}},
                  {id:902,role:'assistant',content:'Сначала изучу файлы.',metadata:metadata('commentary','text',2,1)},
                  {id:903,role:'assistant',content:'',metadata:{...metadata(null,'activity',3,2),events:[{event:'tool.call',data:{id:'read',name:'file.read',arguments:{path:'main.py'}}},{event:'tool.result',data:{id:'read',name:'file.read',arguments:{path:'main.py'},result:{ok:true}}}]}},
                  {id:904,role:'assistant',content:'Проверю результат.',metadata:metadata('commentary','text',6,3)}];
                renderChatMessages(input);
                const live=document.querySelectorAll('.journal-timeline > .journal-commentary').length===2;
                handleLocalJournal('agent.status',{turn_id:77,text:'Проверяю проект…'});
                const status=document.querySelector('.journal-segment.is-running .journal-summary-copy').textContent==='Проверяю проект…';
                document.querySelector('.journal-segment').open=true;
                turn.status='completed';turn.completed_at='2026-10-15 10:00:05';
                renderChatMessages([...input,{id:905,role:'assistant',content:'Готово, проект изучен.',metadata:metadata('final','text',8,5)}]);
                const collapsed=!document.querySelector('.journal-completed').open;
                const result={live,status,collapsed,one:document.querySelectorAll('.journal-completed').length===1,final:document.querySelectorAll('.message.assistant').length===1,duration:document.querySelector('.journal-completed .journal-summary-copy').textContent.includes('5 сек.')};
                newChat();return result;
            }""")
            assert all(journal_check.values()), journal_check
            journal_reuse = page.evaluate("""async () => {
                newChat();state.chat={id:99999};state.page='chat';state.followFeed=false;
                const turn={id:88,status:'running',created_at:'2026-10-15 10:00:00'};
                const input=[{id:1000,role:'user',content:'Длинная задача',metadata:{turn_id:88,turn}}];
                for(let n=0;n<150;n++)input.push({id:1001+n,role:'assistant',content:'Комментарий '+n+'\\n\\n```python\\nprint('+n+')\\n```',metadata:{turn_id:88,turn,kind:'text',channel:'commentary',status:'completed'}});
                renderChatMessages(input);
                const journal=document.querySelector('.local-work');
                const first=document.querySelectorAll('.journal-timeline > .journal-commentary')[40],code=first.querySelector('code');
                const feed=document.querySelector('#feed');feed.scrollTop=200;const top=feed.scrollTop;
                const original=renderMarkdown;let parsed=0;
                renderMarkdown=(...args)=>{parsed++;return original(...args);};
                try {
                  renderChatMessages([...input,{id:1200,role:'assistant',content:'',metadata:{turn_id:88,turn,kind:'activity',status:'running',events:[{event:'tool.call',data:{id:'new-read',name:'file.write_patch',arguments:{path:'main.py',content:'x'.repeat(1000000)}}}]}}]);
                }finally{renderMarkdown=original;}
                const result={same:journal===document.querySelector('.local-work'),row:first.isConnected,code:code===first.querySelector('code'),rows:document.querySelector('.journal-timeline').children.length,parsed,scroll:feed.scrollTop===top,attribute:!document.querySelector('[data-signature]'),bounded:document.querySelector('.local-work')._messageSignature.length<30000};
                newChat();return result;
            }""")
            assert journal_reuse == {
                "same": True,
                "row": True,
                "code": True,
                "rows": 100,
                "parsed": 0,
                "scroll": True,
                "attribute": True,
                "bounded": True,
            }, journal_reuse
            stream_markdown = page.evaluate("""async () => {
                newChat();state.chat={id:99999};state.page='chat';state.followFeed=false;
                const first='# Живой заголовок\\n\\n|a|b|\\n|---|---|\\n|1|2|\\n\\n';
                queueStreamDelta(99999,{message_id:1300,offset:0,content:first});
                await new Promise(resolve=>setTimeout(resolve,150));
                const target=state.messages.get('1300').querySelector('.message-body'),table=target.querySelector('table');
                queueStreamDelta(99999,{message_id:1300,offset:first.length,content:'**Продолжение**'});
                await new Promise(resolve=>setTimeout(resolve,150));
                const result={heading:!!target.querySelector('h1'),table:table===target.querySelector('table'),bold:target.querySelector('strong')?.textContent==='Продолжение'};
                newChat();return result;
            }""")
            assert all(stream_markdown.values()), stream_markdown
            diff_check = page.evaluate("""() => {
                newChat();setPanel('right',false,false);void selectInspector('diff');
                const a='--- a/a.py\\n+++ b/a.py\\n@@ -0,0 +1,2 @@\\n+\\n+++ literal\\n';
                const b='--- a/b.py\\n+++ b/b.py\\n@@ -7 +7 @@\\n-old\\n+print("<script>" + "'+('x'.repeat(200))+'")\\n';
                const change={id:901,description:'Два файла',status:'applied',diff:a+b};
                renderChangeInspector([change]);
                document.querySelectorAll('.change-file')[1].click();
                const root=document.querySelector('#diff'),row=root.querySelector('.diff-line.added');root.scrollLeft=40;const left=root.scrollLeft;
                renderChangeInspector([JSON.parse(JSON.stringify(change)),{id:902,diff:'',description:'Пустой файл',files:[{path:'empty.py',before_hash:null,after_hash:'hash'}]}]);
                const result={counts:JSON.stringify(diffCounts(a+b))===JSON.stringify({add:3,remove:1}),selected:root.querySelector('.diff-file-heading span').textContent==='b.py',row:row===root.querySelector('.diff-line.added'),scroll:root.scrollLeft===left,number:root.querySelector('.diff-line.added span:nth-child(2)').textContent==='7',safe:!root.querySelector('script'),highlight:!!root.querySelector('.hljs-string'),rollback:document.querySelector('#rollback').dataset.fileCount==='2'};
                document.querySelectorAll('.change-file')[2].click();
                result.empty=root.textContent.includes('Создан пустой файл');
                document.querySelectorAll('.change-file')[1].click();
                return result;
            }""")
            assert all(diff_check.values()), diff_check
            page.screenshot(path=str(output / "diff-review.png"), animations="disabled")
            old_theme = page.evaluate("document.documentElement.dataset.theme")
            page.evaluate("document.documentElement.dataset.theme='light'")
            page.screenshot(path=str(output / "diff-review-light.png"), animations="disabled")
            page.evaluate("theme=>document.documentElement.dataset.theme=theme", old_theme)
            page.locator("#rollback").click()
            page.get_by_text(
                "Будет отменён весь набор изменений. Файлов: 2. Это затронет не только открытый файл.",
                exact=True,
            ).wait_for()
            page.get_by_role("button", name="Отмена", exact=True).click()
            diff_paging = page.evaluate("""() => {
                const diff='--- a/large.py\\n+++ b/large.py\\n@@ -0,0 +1,2500 @@\\n'+Array.from({length:2500},(_,n)=>'+print('+n+')').join('\\n')+'\\n';
                const root=document.querySelector('#diff');mountDiff(root,parseUnifiedDiff(diff),'large');
                const first=root.querySelector('.diff-line.added'),initial=root.querySelectorAll('.diff-line.added').length;
                root.querySelector('.diff-load-more').click();
                const result={bounded:initial===798,more:root.querySelectorAll('.diff-line.added').length===1598,preserved:first===root.querySelector('.diff-line.added')};
                newChat();setPanel('right',true,false);return result;
            }""")
            assert all(diff_paging.values()), diff_paging
            page.screenshot(path=str(output / "home.png"))
            page.locator(".nav-more summary").click()
            page.locator('[data-page="llm"]').click()
            page.get_by_role("button", name="Войти с помощью Докки", exact=True).wait_for()
            assert "undefined" not in page.locator("body").inner_text()
            page.screenshot(path=str(output / "desktop.png"))
            page.evaluate("closeSettings()")
            page.locator(".settings-window").wait_for(state="hidden")
            httpx.post(f"http://127.0.0.1:{port}/api/ai/test-enable").raise_for_status()
            page.evaluate("localUI.refresh()")
            page.wait_for_function(
                "document.querySelector('#model').value === 'dokky-test-scripted'"
            )
            page.locator('[data-page="chat"]').click()
            page.locator("#attach").click()
            page.get_by_role("menuitem", name="Создать проект", exact=True).click()
            page.get_by_label("Название", exact=True).fill("Тестовый проект")
            page.get_by_role("button", name="Создать", exact=True).click()
            page.wait_for_function("state.project?.name==='Тестовый проект'")
            project = page.evaluate("state.project")
            workspace = Path(project["path"])
            assert workspace.parent == project_base and workspace.is_dir()
            page.evaluate("setPanel('right',true,false)")
            page.locator("#attach").click()
            page.get_by_role("menuitemcheckbox", name="Планирование", exact=True).click()
            assert page.locator("#planning-chip").is_visible()
            assert page.locator("#mode").is_hidden()
            assert page.locator("#permission-selector").input_value() in {"ask", "trusted"}
            assert page.locator("#permission-selector").is_disabled()
            page.locator("#prompt").fill("/dokky-demo plan-greeting")
            page.locator("#send").click()
            page.get_by_role("button", name="Реализуй план", exact=True).wait_for()
            assert not (workspace / "greeting.py").exists()
            assert page.locator("#planning-chip").is_visible()
            page.locator("#plan-approval summary").click()
            page.locator("#plan-approval details[open]").wait_for()
            page.get_by_role("heading", name="Критерии приёмки", exact=True).wait_for()
            assert (
                page.locator("#plan-approval .plan-document-body").inner_text().find("greeting.py")
                >= 0
            )
            assert page.locator(".composer-meta").is_hidden()
            assert page.locator('[data-select-id="model"]').is_hidden()
            assert page.locator('[data-select-id="reasoning"]').is_visible()
            assert page.locator("#reasoning").input_value() == "light"
            page.screenshot(path=str(output / "plan-ready.png"), animations="disabled")
            with page.expect_download() as plan_download:
                page.get_by_role("button", name="Скачать план", exact=True).click()
            assert plan_download.value.suggested_filename == "plan.md"
            assert "Критерии приёмки" in Path(plan_download.value.path()).read_text(
                encoding="utf-8"
            )
            page.get_by_role("button", name="Реализуй план", exact=True).click()
            page.get_by_role("button", name="Разрешить", exact=True).wait_for()
            assert not (workspace / "greeting.py").exists()
            page.get_by_role("button", name="Разрешить", exact=True).click()
            page.wait_for_function(
                "state.chat !== null && !state.running && document.querySelector('#feed').textContent.includes('Файл greeting.py')",
                timeout=30000,
            )
            assert (workspace / "greeting.py").is_file()
            assert page.locator("#permission-selector").is_enabled()
            assert page.locator("#planning-chip").is_hidden()
            assert page.get_by_role("button", name="Реализуй план", exact=True).count() == 0
            assert page.locator("#plan-approval").is_visible()
            page.get_by_text("Реализуй план", exact=True).wait_for()
            page.wait_for_function("document.querySelectorAll('#projects .chat-row').length===1")
            assert page.locator("#chats .chat-row").count() == 0
            assert (
                "verification.run"
                in httpx.get(
                    f"http://127.0.0.1:{port}/api/chats/{page.evaluate('state.chat.id')}/messages"
                ).text
            )
            page.screenshot(path=str(output / "chat.png"))
            page.evaluate("""() => {
              window.savedProjects=state.projects;window.savedChats=state.chats;
              const name='Очень длинное название проекта '+ 'название'.repeat(40);
              state.projects=[{id:99999,name,path:'test'}];
              state.chats=[{id:99999,title:name,project_id:99999}];renderSidebar();
            }""")
            assert page.evaluate(
                """() => [...document.querySelectorAll('#projects summary>span,#projects .item span')].every(node=>node.scrollWidth>node.clientWidth&&getComputedStyle(node).textOverflow==='ellipsis')"""
            )
            assert page.locator("#left").bounding_box()["width"] <= 280
            page.screenshot(path=str(output / "sidebar-long-titles.png"), animations="disabled")
            page.evaluate(
                "state.projects=window.savedProjects;state.chats=window.savedChats;renderSidebar()"
            )
            assert page.locator(".journal-completed").count() == 2
            page.locator(".journal-completed > summary").last.click()
            for summary in page.locator(
                ".journal-completed[open] .journal-segment > summary"
            ).all():
                summary.click()
            page.locator(".journal-completed[open]").get_by_text(
                "Прочитан файл: greeting.py", exact=False
            ).first.wait_for()
            page.screenshot(path=str(output / "journal-open.png"), animations="disabled")
            page.locator(".journal-completed > summary").last.click()
            page.wait_for_function("!document.querySelector('.journal-completed[open]')")
            page.locator("#prompt").fill("Добавь обработку пустого ввода")
            page.locator("#send").click()
            page.get_by_role("button", name="Разрешить", exact=True).wait_for()
            page.get_by_role("button", name="Разрешить", exact=True).click()
            page.wait_for_function(
                "!state.running && document.querySelectorAll('.message.user').length >= 2",
                timeout=30000,
            )
            assert "Введите приветствие." in (workspace / "greeting.py").read_text(encoding="utf-8")
            page.wait_for_function("state.streamAbort===null")
            page.evaluate("clearTimeout(state.syncTimer)")
            chat_id = page.evaluate("state.chat.id")
            page.route(
                f"**/api/chats/{chat_id}/messages",
                lambda route: route.fulfill(
                    status=503,
                    content_type="application/json",
                    body='{"detail":"Тестовая ошибка истории"}',
                ),
                times=1,
            )
            page.locator("#projects .chat-row .item").first.click()
            page.get_by_role("button", name="Повторить загрузку", exact=True).wait_for()
            assert page.evaluate(
                "state.chatLoadState==='failed'&&document.querySelector('#composer').inert"
            )
            page.get_by_role("button", name="Повторить загрузку", exact=True).click()
            page.wait_for_function(
                "state.chatLoadState==='ready'&&document.querySelectorAll('.journal-completed').length===3"
            )
            page.locator("#toggle-right").click()
            page.locator('[data-inspector="files"]').click()
            page.get_by_role("button", name="greeting.py", exact=True).click()
            page.locator(".file-editor").wait_for()
            assert "Введите приветствие" in page.locator(".file-editor").input_value()
            page.screenshot(path=str(output / "editor.png"))
            original_hash = page.evaluate("state.editor.hash")
            edited = (
                page.locator(".file-editor").input_value()
                + "\n# Проверка сохранения из интерфейса\n"
            )
            page.locator(".file-editor").fill(edited)
            page.get_by_role("button", name="Сохранить файл", exact=True).click()
            page.wait_for_function("hash=>state.editor.hash!==hash", arg=original_hash)
            assert (workspace / "greeting.py").read_text(encoding="utf-8") == edited
            draft_text = edited + "\n# Несохранённый черновик\n"
            page.locator(".file-editor").fill(draft_text)
            file_writes = []
            page.on(
                "request",
                lambda request: (
                    file_writes.append(request.url)
                    if request.method == "PUT" and request.url.endswith("/file")
                    else None
                ),
            )
            page.evaluate("""() => {
                window.retiredSave=document.querySelector('#editor-view button').onclick;
                const original=window.fetch;
                window.fetch=(url,options)=>String(url).includes('/file?path=greeting.py')?new Promise(resolve=>{
                  window.releaseOldFile=()=>{window.fetch=original;resolve(new Response(JSON.stringify({content:'Устаревший файл другого проекта',hash:'old'}),{headers:{'Content-Type':'application/json'}}));};
                }):original(url,options);
                void openFile('greeting.py');
            }""")
            page.locator("#attach").click()
            page.get_by_role("menuitem", name="Создать проект", exact=True).click()
            page.get_by_label("Название", exact=True).fill("Другой проект")
            page.get_by_role("button", name="Создать", exact=True).click()
            page.wait_for_function("state.project?.name==='Другой проект'")
            other_workspace = Path(page.evaluate("state.project.path"))
            other_file = other_workspace / "greeting.py"
            other_file.write_text(edited, encoding="utf-8")
            page.evaluate("window.releaseOldFile()")
            page.evaluate("window.retiredSave()")
            assert not file_writes, file_writes
            assert page.evaluate("state.editor===null")
            assert page.locator("#editor-view").is_hidden()
            assert other_file.read_text(encoding="utf-8") == edited
            assert (workspace / "greeting.py").read_text(encoding="utf-8") == edited
            page.evaluate("project=>newChat(project)", project)
            page.evaluate("openFile('greeting.py')")
            page.locator(".file-editor").wait_for()
            assert page.locator(".file-editor").input_value() == draft_text
            assert not file_writes, file_writes
            page.locator("#toggle-right").click()
            for key in (
                "projects",
                "mission",
                "skills",
                "automations",
                "review",
                "logs",
                "settings",
                "sources",
            ):
                page.evaluate("key => page(key)", key)
                if key == "settings":
                    page.locator(".settings-window").wait_for()
                    page.evaluate("closeSettings()")
                    page.locator(".settings-window").wait_for(state="hidden")
                else:
                    page.locator(".page-content").wait_for()
                assert not page.locator(".error-state").count(), key
                assert "undefined" not in page.locator("body").inner_text(), key
            page.evaluate("openChat(state.chats[0])")
            page.locator(".message.user").first.wait_for()
            page.set_viewport_size({"width": 760, "height": 720})
            page.screenshot(path=str(output / "compact.png"), animations="disabled")
            assert not errors, errors
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.set_viewport_size({"width": 390, "height": 780})
            page.evaluate("newChat(); setPanel('left',true,false); setPanel('right',true,false)")
            page.screenshot(path=str(output / "mobile.png"), animations="disabled")
            geometry = page.evaluate(
                """() => ({viewport:innerWidth,main:document.querySelector('main').getBoundingClientRect().width,composer:document.querySelector('#composer').getBoundingClientRect().width,prompt:document.querySelector('#prompt').getBoundingClientRect().width,columns:getComputedStyle(document.querySelector('#shell')).gridTemplateColumns,shell:document.querySelector('#shell').className})"""
            )
            assert (
                geometry["composer"] >= geometry["viewport"] - 48 and geometry["prompt"] >= 100
            ), geometry
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            browser.close()
        print(f"Снимки: {output}")
        print(f"Проект: {workspace}")
    finally:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    main()

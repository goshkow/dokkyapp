"""Проверка интерфейса в браузере на выдуманных облачных данных, к настоящему AI не ходит."""

import json
import io
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright
from PIL import Image


def main():
    output = Path(tempfile.mkdtemp(prefix="koddi-cloud-ui-"))
    web = Path(__file__).resolve().parents[1] / "backend/koddi/desktop/web"

    def local_asset(route):
        relative = urlsplit(route.request.url).path.removeprefix("/ui/") or "index.html"
        target = (web / relative).resolve()
        if not target.is_relative_to(web.resolve()) or not target.is_file():
            route.fulfill(status=404, body="Не найдено")
            return
        route.fulfill(path=str(target))

    image_bytes = io.BytesIO()
    Image.new("RGB", (640, 480), (38, 105, 87)).save(image_bytes, format="PNG")
    bootstrap = {
        "models": [
            {
                "id": "dokky-default",
                "name": "Докки",
                "roles": ["chat", "search"],
                "available": True,
            },
            {"id": "dokky-code", "name": "Разработка", "roles": ["developer"], "available": True},
        ],
        "preferences": {},
        "capabilities": {"multi_chat": True},
    }
    messages = [{"id": "msg_1", "role": "user", "content": "Первое сообщение", "version": 3}]
    active = False
    history_fail = False
    active_run_id = "run_1"
    regenerated = []
    authenticated = False
    preferences = {"detail": "standard", "sources": "both", "memory_enabled": True}
    appearance = {"theme": "dark", "ui_motion": "full", "chat_width": "standard", "text_size": 15}
    sent = []
    library_favorite = False
    library_fail = False
    library_file = {
        "id": "file_text",
        "name": "example.py",
        "kind": "file",
        "favorite": False,
        "source": "Докки",
        "chat": "Серверный чат",
        "created_at": "2026-10-15T10:00:00Z",
    }
    library_image = {
        "id": "file_image",
        "name": "Изображение.png",
        "kind": "image",
        "favorite": False,
        "source": "Вы",
        "chat": "Серверный чат",
        "created_at": "2026-10-15T10:00:00Z",
    }
    chat = {
        "id": "chat_1",
        "title": "Серверный чат",
        "mode": "chat",
        "model": "dokky-default",
        "version": 7,
    }

    def upstream(route):
        nonlocal active, active_run_id, library_favorite, library_fail, history_fail
        request = route.request
        path = urlsplit(request.url).path.removeprefix("/api/dokky/")
        if path == "cloud/me/avatar":
            route.fulfill(status=404, body="")
            return
        query = parse_qs(urlsplit(request.url).query)
        body = (
            request.post_data_json
            if request.method in {"POST", "PATCH", "PUT"} and request.post_data
            else {}
        )
        value = {}
        if path == "cloud/files/file_image/content":
            route.fulfill(content_type="image/png", body=image_bytes.getvalue())
            return
        if path == "cloud/library":
            if library_fail:
                library_fail = False
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps({"detail": "Библиотека временно недоступна"}),
                )
                return
            tab = query.get("tab", ["recent"])[0]
            items = [dict(library_file, favorite=library_favorite), library_image]
            if tab == "favorites":
                items = [dict(library_file, favorite=True)] if library_favorite else []
            elif tab == "images":
                items = [library_image]
            elif tab == "files":
                items = [dict(library_file, favorite=library_favorite)]
            if query.get("cursor"):
                assert query["cursor"] == ["library-next"]
                items = [dict(library_file, id="file_more", name="Ещё файл.txt")]
            value = {
                "items": items,
                "next_cursor": "library-next"
                if tab == "recent" and not query.get("cursor")
                else None,
                "retention_days": 30,
            }
            route.fulfill(content_type="application/json", body=json.dumps(value))
            return
        if path == "cloud/library/file_text/favorite":
            library_favorite = body["favorite"]
            route.fulfill(
                content_type="application/json",
                body=json.dumps({"id": "file_text", "favorite": library_favorite}),
            )
            return
        if path in {"cloud/artifacts/art_text", "cloud/artifacts/art_test"}:
            route.fulfill(
                content_type="application/json",
                body=json.dumps(
                    {
                        "id": "art_text",
                        "content": 'print("Привет!")\n# <script>window.unsafe=true</script>',
                        "size": 75,
                        "type": "text",
                    }
                ),
            )
            return
        if path == "session":
            value = {
                "authenticated": authenticated,
                "user": {"display_name": "Тест API"},
                "bootstrap": bootstrap,
            }
        elif path == "auth/start":
            value = {"authorize_url": "https://dokky.goshkow.com/oauth/authorize?test=1"}
        elif path == "cloud/preferences":
            preferences.update(body)
            value = preferences
        elif path == "cloud/subscription":
            value = {
                "plan": "plus",
                "remaining_percent": 83,
                "developer": {"window_used_percent": 12},
            }
        elif path == "bootstrap":
            value = bootstrap
        elif path == "cloud/chats" and request.method == "GET":
            value = {"items": [chat] if query.get("mode") == ["chat"] else [], "next_cursor": None}
        elif path == "cloud/chats" and request.method == "POST":
            assert body["model"] == "dokky-default", body
            value = chat
        elif path == "cloud/chats/chat_1":
            if request.method == "PATCH":
                assert body["expected_version"] == 7
                chat.update(body)
            if request.method == "DELETE":
                assert query.get("confirm") == ["true"]
            value = {
                **chat,
                "active_run": {"id": active_run_id, "status": "running"} if active else None,
            }
        elif path == "cloud/chats/chat_1/messages/msg_1" and request.method == "PATCH":
            assert body["expected_version"] == 3
            messages[0].update(content=body["content"], version=4)
            value = messages[0]
        elif path == "cloud/chats/chat_1/messages/msg_2/regenerate":
            regenerated.append(request.headers["idempotency-key"])
            active = True
            active_run_id = "run_regen"
            value = {"run_id": "run_regen"}
        elif path == "cloud/chats/chat_1/messages" and request.method == "GET":
            if history_fail:
                history_fail = False
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps({"detail": "История временно недоступна"}),
                )
                return
            if "cursor" in query:
                assert query["cursor"] == ["opaque-cursor"] and "before" not in query
                value = {
                    "items": [{"id": "msg_0", "role": "assistant", "content": "Старая история"}],
                    "next_cursor": None,
                }
            else:
                value = {"items": messages, "next_cursor": "opaque-cursor"}
        elif path == "cloud/chats/chat_1/messages" and request.method == "POST":
            sent.append(body)
            assert body["model"] == "dokky-default"
            assert body["client_message_id"] == request.headers["idempotency-key"]
            active = True
            active_run_id = "run_1"
            value = {"run_id": "run_1"}
        elif path == "cloud/runs/run_regen/events":
            active = False
            messages.append(
                {
                    "id": f"msg_{2 + len(regenerated)}",
                    "role": "assistant",
                    "content": "Другой вариант ответа",
                    "branch_of": "msg_2",
                }
            )
            route.fulfill(
                content_type="text/event-stream",
                body='id: 1\nevent: turn.completed\ndata: {"message_id":"msg_3"}\n\n',
            )
            return
        elif path == "cloud/runs/run_regen/cancel":
            active = False
            value = {"ok": True}
        elif path == "cloud/runs/run_1/events":
            assert query.get("after") == ["0"]
            active = False
            messages.append(
                {
                    "id": "msg_2",
                    "role": "assistant",
                    "content": "**Готово**",
                    "citations": [{"url": "https://example.org/source", "title": "Источник"}],
                    "artifacts": [{"file_id": "file_test", "name": "result.txt"}],
                }
            )
            stream = [
                (
                    1,
                    "message.delta",
                    {"message_id": "pending_run_1", "content": "Черновик", "offset": 0},
                ),
                (
                    2,
                    "message.delta",
                    {"message_id": "pending_run_1", "content": "", "offset": 0, "replace": True},
                ),
                (
                    3,
                    "message.delta",
                    {"message_id": "pending_run_1", "content": "**Готово**", "offset": 0},
                ),
                (
                    4,
                    "turn.completed",
                    {"message_id": "msg_2", "pending_message_id": "pending_run_1"},
                ),
            ]
            route.fulfill(
                content_type="text/event-stream",
                body="".join(
                    f"id: {i}\nevent: {event}\ndata: {json.dumps(data)}\n\n"
                    for i, event, data in stream
                ),
            )
            return
        else:
            raise AssertionError(f"Unexpected cloud route: {request.method} {path}")
        route.fulfill(content_type="application/json", body=json.dumps(value))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, bypass_csp=True)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def local_api(route):
            if urlsplit(route.request.url).path == '/api/app/diagnostics':
                route.fulfill(status=200,content_type='application/json',body=json.dumps({'build_number':'20261015.1'}))
                return
            path = urlsplit(route.request.url).path
            if path == "/api/events":
                route.fulfill(content_type="text/event-stream", body="")
            elif path in {"/api/projects", "/api/chats", "/api/models/options"}:
                route.fulfill(json=[])
            elif path == "/api/modes":
                route.fulfill(
                    json=[{"id": mode, "name": mode} for mode in ["development", "plan", "chat"]]
                )
            else:
                raise AssertionError("Неожиданный локальный запрос: " + path)

        page.route("**/api/**", local_api)
        page.route("**/api/dokky/**", upstream)

        def settings_route(route):
            if route.request.method == "PUT":
                appearance.update(route.request.post_data_json)
            route.fulfill(content_type="application/json", body=json.dumps(appearance))

        page.route("**/api/settings", settings_route)
        page.route("**/ui/**", local_asset)
        page.goto("http://127.0.0.1:8878/ui/", wait_until="domcontentloaded")
        page.wait_for_function("window.koddiProductScope === 'chat'")
        page.get_by_role("button", name="Войти с помощью Докки", exact=True).wait_for()
        assert page.locator("#shell").is_hidden()
        assert page.locator("#title").is_hidden()
        page.screenshot(path=str(output / "login.png"))
        page.evaluate("window.open=()=>null")
        page.locator("#auth-login").click()
        assert page.locator("#auth-login").is_disabled()
        authenticated = True
        page.locator("#auth-screen").wait_for(state="hidden")
        assert page.locator("#shell").is_visible()
        assert page.locator("#segmented-mode").is_visible()
        page.wait_for_function("document.querySelector('#model').value === 'dokky-default'")
        assert page.locator("#model option[value='dokky-code']").count() == 0
        # завершение загрузки должно попасть в тот черновик, где она началась
        page.evaluate("""() => {
          window.originalUploadFetch=window.fetch;
          window.uploadKeys=[];
          window.fetch=(url,options)=>String(url).endsWith('/cloud/files')?new Promise(resolve=>{
            window.uploadKeys.push(options.headers['Idempotency-Key']);
            window.uploadSignal=options.signal;
            window.releaseUpload=(failed=false)=>resolve(new Response(JSON.stringify(failed?{detail:'Ошибка сети'}:{id:'file_upload',original_name:'draft.txt'}),{status:failed?503:200,headers:{'Content-Type':'application/json'}}));
          }):window.originalUploadFetch(url,options);
          void upload([new File(['hello'],'draft.txt')]);
        }""")
        page.get_by_text("Загрузка…", exact=True).wait_for()
        page.locator("#prompt").fill("Сообщение с файлом")
        page.locator("#send").click()
        assert not sent
        assert page.locator("#prompt").input_value() == "Сообщение с файлом"
        page.evaluate("window.releaseUpload(true)")
        page.get_by_text("Ошибка загрузки", exact=True).wait_for()
        page.get_by_role("button", name="Повторить загрузку", exact=True).click()
        page.evaluate("window.releaseUpload()")
        page.wait_for_function("state.attachments.length===1&&state.uploads.size===0")
        assert page.evaluate("window.uploadKeys[0]===window.uploadKeys[1]")
        page.get_by_role("button", name="Убрать вложение", exact=True).click()
        page.evaluate("void upload([new File(['cancel'],'draft.txt')])")
        page.get_by_role("button", name="Отменить загрузку", exact=True).click()
        assert page.evaluate("window.uploadSignal.aborted")
        page.evaluate("window.releaseUpload()")
        page.wait_for_function("state.attachments.length===0&&state.uploads.size===0")
        page.evaluate("void upload([new File(['stale'],'draft.txt')])")
        page.evaluate("cloudNewChat()")
        assert page.evaluate("window.uploadSignal.aborted")
        page.evaluate("window.releaseUpload();window.fetch=window.originalUploadFetch")
        page.wait_for_function("state.attachments.length===0&&state.uploads.size===0")
        page.locator("#prompt").fill("")
        page.locator("#account-menu-toggle").click()
        page.get_by_role("menuitem", name="Настройки", exact=True).wait_for()
        page.screenshot(path=str(output / "account-menu.png"), animations="disabled")
        page.keyboard.press("Escape")
        page.wait_for_function("document.activeElement.id==='account-menu-toggle'")
        page.evaluate("""() => {
          const host=document.createElement('div');host.id='menu-fixture';
          host.style.cssText='position:fixed;bottom:8px;right:8px;width:180px;z-index:500';
          host.innerHTML='<select id="menu-fixture-select" aria-label="Проверка меню"><option value="a">Альфа</option><option value="b">Бета</option><option value="c">Гамма</option></select>';
          document.body.append(host);
        }""")
        combo = page.get_by_role("combobox", name="Проверка меню", exact=True)
        combo.click()
        assert page.locator(".ui-select-menu:visible").evaluate(
            "node=>{const b=node.getBoundingClientRect();return b.left>=7&&b.top>=7&&b.right<=innerWidth-7&&b.bottom<=innerHeight-7}"
        )
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        assert page.locator("#menu-fixture-select").input_value() == "b"
        combo.click()
        page.keyboard.press("End")
        page.keyboard.press("Enter")
        assert page.locator("#menu-fixture-select").input_value() == "c"
        combo.click()
        page.keyboard.press("Escape")
        assert combo.evaluate("node=>node===document.activeElement")
        page.evaluate("document.querySelector('#menu-fixture').remove()")
        page.locator("#prompt").fill("Проверка контекстного меню")
        page.locator("#prompt").click(button="right")
        page.get_by_role("menuitem", name="Выделить всё", exact=True).click()
        assert page.locator("#prompt").evaluate(
            "node=>node.selectionStart===0&&node.selectionEnd===node.value.length"
        )
        page.locator("#prompt").fill("")
        history_fail = True
        page.evaluate("openChat(dokkyState.chats[0])")
        page.get_by_role("button", name="Повторить загрузку", exact=True).wait_for()
        assert page.evaluate(
            "state.chatLoadState==='failed'&&document.querySelector('#composer').inert"
        )
        page.screenshot(path=str(output / "chat-load-error.png"), animations="disabled")
        page.get_by_role("button", name="Повторить загрузку", exact=True).click()
        page.get_by_text("Первое сообщение", exact=True).wait_for()
        assert page.evaluate(
            "state.chatLoadState==='ready'&&!document.querySelector('#composer').inert"
        )
        for stale_error in (False, True):
            page.evaluate(
                """failed => {
              const original=window.fetch;
              window.fetch=(url,options)=>String(url).endsWith('/chats/chat_1/messages?limit=100')?new Promise(resolve=>{
                window.releaseOldChat=()=>{window.fetch=original;resolve(new Response(JSON.stringify(failed?{detail:'Запоздалая ошибка'}:{items:[{id:'stale',role:'assistant',content:'Запоздалый ответ'}],next_cursor:null}),{status:failed?503:200,headers:{'Content-Type':'application/json'}}));};
              }):original(url,options);
              void openChat(dokkyState.chats[0]);
            }""",
                stale_error,
            )
            page.locator(".chat-skeleton").wait_for()
            assert page.evaluate(
                "state.chatLoadState==='loading'&&document.querySelector('#composer').inert"
            )
            page.locator('[data-page="chat"]').click()
            page.evaluate("window.releaseOldChat()")
            assert page.locator("#title").inner_text() == ""
            assert page.locator(".chat-load-error").count() == 0
            assert "Запоздалый ответ" not in page.locator("#feed").inner_text()
            assert page.evaluate("state.chatLoadState==='ready'")
        page.evaluate("openChat(dokkyState.chats[0])")
        page.get_by_text("Первое сообщение", exact=True).wait_for()
        assert page.locator("#segmented-mode").is_visible()
        page.evaluate("""() => {
          const original=window.fetch;window.olderCalls=0;
          window.fetch=(url,options)=>String(url).includes('cursor=opaque-cursor')?new Promise(resolve=>{
            window.olderCalls++;
            window.releaseOlderChat=()=>{window.fetch=original;resolve(new Response(JSON.stringify({items:[{id:'stale_old',role:'assistant',content:'Устаревшая страница истории'}],next_cursor:null}),{headers:{'Content-Type':'application/json'}}));};
          }):original(url,options);
        }""")
        page.get_by_role("button", name="Предыдущие сообщения", exact=True).click()
        page.get_by_role("button", name="Предыдущие сообщения", exact=True).evaluate(
            "node=>node.click()"
        )
        assert page.evaluate("window.olderCalls") == 1
        page.evaluate("openChat(dokkyState.chats[0])")
        page.get_by_text("Первое сообщение", exact=True).wait_for()
        page.evaluate("window.releaseOlderChat()")
        assert "Устаревшая страница истории" not in page.locator("#feed").inner_text()
        page.get_by_role("button", name="Предыдущие сообщения").click()
        page.get_by_text("Старая история", exact=True).wait_for()
        page.evaluate("""() => {
          setRunning(true);beginCloudProgress(state.chat.id,'run_preview');
          handleCloudProgress('activity.updated',{activity:{kind:'commentary',text:'Проверяю **источники** перед ответом.'}});
          handleCloudProgress('activity.updated',{activity:{kind:'page_reading',text:'Читаю найденную страницу…'}});
        }""")
        page.get_by_text("Читаю найденную страницу…", exact=True).wait_for()
        assert page.locator(".work-progress-commentary strong").inner_text() == "источники"
        page.screenshot(path=str(output / "work-live.png"), animations="disabled")
        bounded = page.evaluate("""() => {
          for(let n=0;n<100;n++){
            handleCloudProgress('activity.updated',{activity:{kind:'page_reading',text:'Страница '+n}});
            handleCloudProgress('activity.updated',{activity:{kind:'commentary',text:'Комментарий '+n}});
          }
          return {comments:document.querySelector('.work-progress-comments').children.length,entries:cloudProgress.live.controller.snapshot().entries.length};
        }""")
        assert bounded["comments"] <= 40 and bounded["entries"] <= 160, bounded
        page.evaluate("""() => {
          handleCloudProgress('turn.completed',{message_id:'msg_preview'});setRunning(false);
          renderChatMessages([cloudMessage({id:'msg_preview',role:'assistant',content:'Итоговый ответ',status:'completed'})]);
        }""")
        assert page.locator(".work-progress-live").is_hidden()
        page.locator(".work-progress-toggle").click()
        assert page.locator(".work-progress-toggle").get_attribute("aria-expanded") == "true"
        assert page.locator(".work-progress-entry").count() <= 160
        page.get_by_text("Комментарий 99", exact=True).wait_for()
        page.screenshot(path=str(output / "work-history.png"), animations="disabled")
        persisted = page.evaluate("""() => {
          const elapsed=cloudDurations().msg_preview;
          cloudProgress.snapshots.delete('msg_preview');
          const node=document.createElement('div');
          renderCloudMessageProgress(node,{id:'msg_preview',role:'assistant',activity:[{kind:'page_reading',text:'Прочитал страницу'}]});
          const label=node.querySelector('.work-progress-toggle')?.textContent;
          disposeCloudMessage(node);
          return {elapsed,label};
        }""")
        assert persisted["elapsed"] >= 0 and "Работал" in persisted["label"]
        markdown_check = page.evaluate("""async () => {
          const article=document.createElement('article');article.className='message assistant';const target=document.createElement('div');target.className='message-body';article.append(target);document.querySelector('#feed').append(article);
          const prefix='# Заголовок\\n\\n|a|b|\\n|---|---|\\n|1|2|\\n\\n```python\\nprint(1)\\n```\\n\\n';
          renderCloudStreamMarkdown(target,prefix+'**Первый фрагмент**');
          await new Promise(resolve=>setTimeout(resolve,90));
          const table=target.querySelector('table');
          state.followFeed=false;document.querySelector('#feed').scrollTop=0;
          renderCloudStreamMarkdown(target,prefix+'**Первый фрагмент**\\n\\nНовый фрагмент<script>window.unsafe=true</script>');
          await new Promise(resolve=>setTimeout(resolve,90));
          return {heading:!!target.querySelector('h1'),bold:!!target.querySelector('strong'),table:table===target.querySelector('table'),code:target.querySelector('code').textContent.includes('print(1)'),safe:!target.querySelector('script'),scroll:document.querySelector('#feed').scrollTop===0};
        }""")
        assert all(markdown_check.values()), markdown_check
        page.screenshot(path=str(output / "markdown-stream.png"), animations="disabled")
        media = page.evaluate("""() => {
          clearFeed();
          renderChatMessages([cloudMessage({id:'media_answer',role:'assistant',content:'**Проверенный факт** и пояснение.',citations:[
            {url:'https://example.org/one',title:'Первый источник',spans:[{start:0,end:20}]},
            {url:'https://example.net/two',title:'Второй источник',spans:[{start:0,end:20}]},
            {url:'https://example.com/three',title:'Третий источник'},
            {url:'https://example.edu/four',title:'Четвёртый источник'},
            {url:'javascript:alert(1)',title:'Небезопасная ссылка'}
          ]}),cloudMessage({id:'media_only',role:'user',content:'',attachments:[
            {file_id:'file_image',name:'Тестовое изображение.png',media_type:'image/png'},
            {file_id:'../invalid',name:'Недопустимый файл'}
          ]})]);
          return {chips:document.querySelectorAll('.ai-source-chip').length,cards:document.querySelectorAll('.ai-source-card').length,files:document.querySelectorAll('.message-attachment').length};
        }""")
        assert media == {"chips": 1, "cards": 4, "files": 1}, media
        page.locator(".ai-source-chip").click()
        assert page.locator(".ai-source-popover-row").count() == 2
        page.get_by_role("button", name="Закрыть источники", exact=True).click()
        page.get_by_role("button", name="Показать все (4)", exact=True).click()
        assert page.locator(".ai-source-card:visible").count() == 4
        page.screenshot(path=str(output / "sources.png"), animations="disabled")
        page.evaluate("saveCloudPreferences({sources:'none'})")
        assert page.locator(".ai-source-chip,.cloud-sources").count() == 0
        page.evaluate("saveCloudPreferences({sources:'inline'})")
        assert page.locator(".ai-source-chip").count() == 1
        assert page.locator(".cloud-sources").count() == 0
        page.evaluate("saveCloudPreferences({sources:'end'})")
        assert page.locator(".ai-source-chip").count() == 0
        assert page.locator(".cloud-sources").count() == 1
        page.evaluate("saveCloudPreferences({sources:'both'})")
        page.wait_for_function(
            "document.querySelector('.message-attachment img').naturalWidth === 640"
        )
        page.locator(".message-attachment").click()
        page.locator(".cloud-image-viewer[open]").wait_for()
        page.get_by_role("button", name="Увеличить", exact=True).click()
        assert page.locator(".cloud-image-zoom").inner_text() == "125%"
        page.screenshot(path=str(output / "image-viewer.png"), animations="disabled")
        page.keyboard.press("Escape")
        page.wait_for_function("!document.querySelector('.cloud-image-viewer').open")
        assert page.locator(".message-attachment img").count() == 1
        page.set_viewport_size({"width": 390, "height": 780})
        page.locator("#toggle-left").click()
        page.locator(".ai-source-chip").click()
        assert page.evaluate("""() => {
          const r=document.querySelector('.ai-source-popover').getBoundingClientRect();
          return r.left>=0&&r.right<=innerWidth&&r.bottom<=innerHeight;
        }""")
        page.get_by_role("button", name="Закрыть источники", exact=True).click()
        page.locator(".message-attachment").click()
        page.screenshot(path=str(output / "image-viewer-mobile.png"), animations="disabled")
        assert page.evaluate("""() => {
          const r=document.querySelector('.viewer-original-image').getBoundingClientRect();
          return r.width>100&&r.left>=0&&r.right<=innerWidth;
        }""")
        page.keyboard.press("Escape")
        page.wait_for_function("!document.querySelector('.cloud-image-viewer').open")
        page.set_viewport_size({"width": 1440, "height": 900})
        page.locator("#toggle-left").click()
        page.locator(".message-attachment").click()
        page.evaluate("clearFeed()")
        assert page.locator(".cloud-image-viewer[open]").count() == 0
        page.evaluate("openChat(dokkyState.chats[0])")
        page.get_by_text("Первое сообщение", exact=True).wait_for()
        assert page.evaluate("cloudProgress.messageControllers.size") == 0
        page.locator("#prompt").fill("Проверка потока")
        page.evaluate("send()")
        assert len(sent) == 1
        page.wait_for_function("!state.running")
        assert page.locator("#feed strong").inner_text() == "Готово"
        assert page.locator('[data-message-id="pending_run_1"]').count() == 0
        assert page.get_by_text("Источник", exact=True).count() == 1
        assert page.get_by_text("result.txt", exact=True).count() == 1
        page.get_by_role("button", name="Открыть файл: result.txt", exact=True).click()
        page.locator(".cloud-artifact-body code").wait_for()
        assert "print(" in page.locator(".cloud-artifact-body code").inner_text()
        assert (
            page.locator(".cloud-artifact footer a")
            .get_attribute("href")
            .endswith("/files/file_test/content")
        )
        page.get_by_role("button", name="Закрыть файл", exact=True).click()
        # кнопки «Повторить ответ» нет (на сайте её нет), логика вариантов проверяется напрямую
        assert page.get_by_role("button", name="Повторить ответ", exact=True).count() == 0
        page.evaluate("void regenerateCloudAnswer(state.messageSources.get('msg_2'))")
        page.get_by_text("Другой вариант ответа", exact=True).wait_for()
        page.wait_for_function("!state.running")
        assert len(regenerated) == 1
        assert page.locator(".cloud-variants > span").inner_text() == "2 / 2"
        page.get_by_role("button", name="Предыдущий вариант", exact=True).click()
        assert page.locator("#feed strong").inner_text() == "Готово"
        assert page.get_by_text("Другой вариант ответа", exact=True).count() == 0
        page.get_by_role("button", name="Следующий вариант", exact=True).click()
        page.get_by_text("Другой вариант ответа", exact=True).wait_for()
        assert page.locator("#feed > :first-child").inner_text() == "Предыдущие сообщения"
        page.screenshot(path=str(output / "answer-variants.png"), animations="disabled")
        retry_after_sync_error = page.evaluate("""async () => {
          const original=cloudSync;
          cloudSync=async()=>{throw new Error('Тестовый разрыв после принятия запроса');};
          try {await regenerateCloudAnswer(state.messageSources.get('msg_2'));}
          catch(error) {if(!error.message.includes('Тестовый разрыв'))throw error;}
          finally {cloudSync=original;}
          const retained=cloudVariants.pending.size;
          await regenerateCloudAnswer(state.messageSources.get('msg_2'));
          return {retained,pending:cloudVariants.pending.size};
        }""")
        assert retry_after_sync_error == {"retained": 1, "pending": 0}, retry_after_sync_error
        assert len(regenerated) == 2
        assert page.locator(".cloud-variants > span").inner_text() == "3 / 3", page.evaluate(
            "({variant:document.querySelector('.cloud-variants > span').textContent,ids:[...state.messageSources.keys()],run:dokkyState.run})"
        )
        page.get_by_role("button", name="Изменить сообщение", exact=True).click()
        page.get_by_label("Сообщение", exact=True).fill("Исправленное сообщение")
        page.locator("dialog").filter(has=page.get_by_label("Сообщение", exact=True)).get_by_role(
            "button", name="Сохранить", exact=True
        ).click()
        page.get_by_text("Исправленное сообщение", exact=True).wait_for()
        assert messages[0]["version"] == 4
        page.evaluate("""() => {
          const original=window.fetch;
          window.fetch=(url,options)=>String(url).endsWith('/msg_2/regenerate')?new Promise((resolve,reject)=>{
            window.releaseRegenerate=()=>{window.fetch=original;original(url,options).then(resolve,reject);};
          }):original(url,options);
          void regenerateCloudAnswer(state.messageSources.get('msg_2'));
        }""")
        page.locator(".cloud-work .status-line").get_by_text("Думаю…", exact=True).wait_for()
        page.evaluate("cloudSend()")
        page.evaluate("window.releaseRegenerate()")
        page.wait_for_function("dokkyState.submission===null&&!state.running")
        assert page.evaluate("cloudVariants.pending.size") == 0
        assert not active and len(regenerated) == 3
        assert "Черновик" not in page.locator("#feed").inner_text()
        page.screenshot(path=str(output / "chat.png"))
        feed = page.locator("#feed").inner_text()
        page.locator("#sidebar-settings").click()
        page.locator(".settings-window").wait_for()
        page.screenshot(path=str(output / "settings-general.png"), animations="disabled")
        page.get_by_role("combobox", name="Тема", exact=True).click()
        page.get_by_role("option", name="Светлая", exact=True).click()
        page.wait_for_function("document.documentElement.dataset.theme==='light'")
        page.get_by_role("combobox", name="Тема", exact=True).click()
        page.get_by_role("option", name="Тёмная", exact=True).wait_for()
        assert page.locator(".ui-select-menu:visible").evaluate(
            'node=>node.matches(":popover-open")'
        )
        page.screenshot(path=str(output / "select-light.png"), animations="disabled")
        page.get_by_role("option", name="Тёмная", exact=True).click()
        page.wait_for_function("document.documentElement.dataset.theme==='dark'")
        page.get_by_role("combobox", name="Ширина чата", exact=True).click()
        page.get_by_role("option", name="Широкая", exact=True).click()
        page.wait_for_function("state.settings.chat_width === 'wide'")
        page.get_by_role("combobox", name="Анимации", exact=True).click()
        page.get_by_role("option", name="Минимальные", exact=True).click()
        page.wait_for_function("document.documentElement.dataset.motion === 'reduced'")
        page.locator('[data-settings-section="conversation"]').click()
        page.get_by_role("combobox", name="Подробность ответов", exact=True).click()
        page.get_by_role("option", name="Подробно", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#settings-feedback').textContent === 'Сохранено'"
        )
        assert preferences["detail"] == "full"
        page.locator('[data-settings-section="usage"]').click()
        page.locator("#settings-body").get_by_text("83%", exact=True).wait_for()
        page.screenshot(path=str(output / "settings.png"))
        page.set_viewport_size({"width": 390, "height": 780})
        page.screenshot(path=str(output / "settings-mobile.png"))
        assert page.evaluate(
            "document.querySelector('.settings-window').scrollWidth <= document.querySelector('.settings-window').clientWidth"
        )
        page.get_by_role("button", name="Закрыть настройки", exact=True).click()
        page.locator(".settings-window").wait_for(state="hidden")
        assert page.locator("#feed").inner_text() == feed
        page.set_viewport_size({"width": 1440, "height": 900})
        page.locator("#cloud-library-button").click()
        page.get_by_role("dialog", name="Библиотека Докки", exact=True).wait_for()
        page.get_by_role("button", name="Открыть: example.py", exact=True).wait_for()
        assert page.locator(".cloud-library .library-card").count() == 2
        page.get_by_role("button", name="Показать ещё", exact=True).click()
        page.get_by_role("button", name="Открыть: Ещё файл.txt", exact=True).wait_for()
        assert page.locator(".cloud-library .library-card").count() == 3
        page.locator(".library-card").filter(
            has=page.get_by_role("button", name="Открыть: example.py", exact=True)
        ).get_by_role("button", name="В избранное", exact=True).click()
        page.get_by_role("tab", name="Избранное", exact=True).click()
        page.get_by_role("button", name="Открыть: example.py", exact=True).wait_for()
        assert library_favorite and page.locator(".library-card").count() == 1
        page.get_by_role("button", name="Открыть: example.py", exact=True).click()
        page.locator(".cloud-artifact-body code").wait_for()
        assert "<script>" in page.locator(".cloud-artifact-body code").inner_text()
        assert not page.evaluate("Boolean(window.unsafe)")
        page.screenshot(path=str(output / "library-file.png"), animations="disabled")
        page.get_by_role("button", name="Закрыть файл", exact=True).click()
        page.get_by_role("button", name="Убрать из избранного", exact=True).click()
        page.get_by_text("В избранном пока ничего нет.", exact=True).wait_for()
        assert not library_favorite
        library_fail = True
        page.get_by_role("tab", name="Файлы", exact=True).click()
        page.get_by_text("Библиотека временно недоступна", exact=True).wait_for()
        page.get_by_role("button", name="Повторить", exact=True).click()
        page.get_by_role("button", name="Открыть: example.py", exact=True).wait_for()
        page.get_by_role("tab", name="Изображения", exact=True).click()
        page.wait_for_function("document.querySelector('.library-preview img')?.naturalWidth===640")
        page.screenshot(path=str(output / "library.png"), animations="disabled")
        page.get_by_role("button", name="Открыть: Изображение.png", exact=True).click()
        page.locator(".cloud-image-viewer[open]").wait_for()
        page.keyboard.press("Escape")
        page.wait_for_function("!document.querySelector('.cloud-image-viewer').open")
        assert page.locator(".library-preview img").count() == 1
        page.set_viewport_size({"width": 390, "height": 780})
        page.screenshot(path=str(output / "library-mobile.png"), animations="disabled")
        assert page.evaluate("document.querySelector('.cloud-library').scrollWidth<=innerWidth")
        page.get_by_role("button", name="Закрыть библиотеку", exact=True).click()
        page.locator(".cloud-library").wait_for(state="detached")
        assert page.locator("#feed").inner_text() == feed
        page.set_viewport_size({"width": 1440, "height": 900})
        page.evaluate("""() => {
          const original=window.fetch;
          window.fetch=(url,options)=>String(url).includes('/cloud/library?')?new Promise((resolve,reject)=>{
            window.releaseLibrary=()=>{window.fetch=original;original(url,options).then(resolve,reject);};
          }):original(url,options);
        }""")
        page.locator("#cloud-library-button").click()
        page.locator(".library-card-skeleton").first.wait_for()
        page.get_by_role("button", name="Закрыть библиотеку", exact=True).click()
        page.locator(".cloud-library").wait_for(state="detached")
        page.evaluate("window.releaseLibrary()")
        assert page.locator(".cloud-library").count() == 0
        page.evaluate("switchDokkyScope('search')")
        assert page.locator("#attach").is_hidden()
        # поиск заменяет только область чата: список чатов слева не меняется
        assert page.locator("#chats").get_by_text("Серверный чат", exact=True).count() == 1
        page.screenshot(path=str(output / "search.png"))
        page.locator("#product-switch").click()
        page.wait_for_function("window.koddiProductScope === 'development'")
        assert page.locator(".sidebar-brand .brand-name").inner_text() == "Докки"
        assert page.locator(".sidebar-brand .brand-badge").is_visible()
        assert page.locator("#segmented-mode").is_hidden()
        assert page.locator("#chats").get_by_text("Серверный чат", exact=True).count() == 0
        page.evaluate("switchDokkyScope('chat')")
        page.evaluate("""() => {
          const original=window.fetch;
          window.fetch=(url,options)=>String(url).endsWith('/chats/chat_1/messages')&&options?.method==='POST'?new Promise(resolve=>{window.releaseTestSend=()=>{window.fetch=original;resolve(original(url,options));};}):original(url,options);
          state.chat={id:'chat_1',title:'Серверный чат',mode:'chat',model:'dokky-default'};
          document.querySelector('#prompt').value='Проверка переключения';void send();
        }""")
        page.locator(".cloud-work .status-line").get_by_text("Думаю…", exact=True).wait_for()
        page.evaluate("switchDokkyScope('search')")
        page.evaluate("window.releaseTestSend()")
        page.wait_for_function("dokkyState.submission === null")
        assert page.locator("#title").inner_text() == ""
        assert page.locator(".cloud-work").count() == 0
        page.evaluate("openCloudLibrary();window.koddiAuthRevoked()")
        assert page.locator(".cloud-library,.cloud-artifact").count() == 0
        page.get_by_role("button", name="Войти с помощью Докки", exact=True).wait_for()
        assert page.locator("#shell").is_hidden()
        assert not errors, errors
        browser.close()
    print(f"Проверка серверных режимов пройдена. Снимки: {output}")


if __name__ == "__main__":
    main()

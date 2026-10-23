"""Изолированные проверки UI без подключения к провайдеру ИИ."""

import json
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def main():
    web = Path(__file__).resolve().parents[1] / "backend/koddi/desktop/web"
    output = Path(tempfile.mkdtemp(prefix="koddi-layout-"))

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(web)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = {"coding_reasoning_mode": "pro", "theme": "dark"}
    session = {"id": "session-test", "reasoning_mode": "pro", "version": 1,
               "history": {"length": 0}, "last_usage": None}
    file = {"content": "# Документ\n\nИсходный текст", "hash": "initial", "read_only": False}
    patches = []
    saves = []
    errors = []
    unexpected = []
    chat = {"id": 1, "title": "Проверка UI", "mode": "development", "model": "dokky-code"}

    def route_api(route):
        request = route.request
        path = urlsplit(request.url).path
        if path in {"/api/dokky/events", "/api/events"}:
            route.fulfill(content_type="text/event-stream", body="")
            return
        if path == "/api/settings":
            if request.method == "PUT":
                settings.update(request.post_data_json)
            value = settings
        elif path == "/api/dokky/session":
            value = {"internal_test": True, "authenticated": True, "bootstrap": {"models": []}}
        elif path == "/api/dokky/bootstrap":
            value = {}
        elif path == "/api/projects":
            value = [{"id": 1, "name": "Проект", "path": "C:/test"}]
        elif path == "/api/chats":
            value = []
        elif path == "/api/models/options":
            value = [{"tag": "dokky-code", "name": "Докки", "source": "test"}]
        elif path == "/api/modes":
            value = [{"id": key, "name": key} for key in ["development", "chat", "plan"]]
        elif path == "/api/chats/1/coding-session":
            if request.method == "PATCH":
                body = request.post_data_json
                patches.append(body)
                session.update(reasoning_mode=body["reasoning_mode"], version=session["version"] + 1)
            value = session
        elif path == "/api/app/diagnostics":
            value = {"build_number": "test"}
        elif path == "/api/dokky/cloud/preferences":
            value = {}
        elif path == "/api/dokky/cloud/subscription":
            value = {"plan": "plus", "remaining_percent": 91, "developer": {}}
        elif path == "/api/chats/1/coding-session/switch-plan":
            value = {"recommended": "switch", "options": [{"action": "switch", "usd": 0}]}
        elif path == "/api/projects/1/files":
            value = {"files": [{"path": "notes.md", "is_dir": False}]}
        elif path == "/api/projects/1/file":
            if request.method == "PUT":
                saves.append(request.post_data_json)
                file.update(content=saves[-1]["content"], hash="saved")
            value = file
        elif path in {"/api/chats/1/messages", "/api/chats/1/changes", "/api/agent/requests"}:
            value = []
        elif path == "/api/chats/1/turn":
            value = {"active": False}
        elif path == "/api/chats/1/plan":
            value = {"steps": [], "document": "", "turn_id": None}
        else:
            unexpected.append(f"{request.method} {path}")
            route.fulfill(status=404, json={"detail": "Неожиданный тестовый запрос"})
            return
        route.fulfill(content_type="application/json", body=json.dumps(value))

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900}, bypass_csp=True)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/**", route_api)
            page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded")
            page.wait_for_function("window.koddiProductScope === 'development'")
            page.get_by_role("combobox", name="Режим разработки", exact=True).wait_for()
            assert page.locator("#reasoning").input_value() == "pro"

            def choose_mode(label):
                page.get_by_role("combobox", name="Режим разработки", exact=True).click()
                page.get_by_role("option", name=label, exact=True).click()

            choose_mode("Light")
            page.wait_for_function("state.settings.coding_reasoning_mode === 'light' && !state.reasoningBusy")
            assert settings["coding_reasoning_mode"] == "light" and not patches
            page.evaluate("chat => {state.chat=chat;state.codingSession=null;updateProductNavigation();}", chat)
            page.evaluate("loadCodingSession(1,state.viewVersion)")
            choose_mode("Max")
            page.wait_for_function("state.codingSession.reasoning_mode === 'max' && !state.reasoningBusy")
            assert patches[-1] == {"reasoning_mode": "max", "acknowledge_cache_cost": False}
            session["history"] = {"length": 2}
            choose_mode("Pro")
            warning = "Смена режима может увеличить стоимость следующего запроса, поскольку ИИ может потребоваться повторно обработать историю диалога. Сама история сохранится."
            page.get_by_text(warning, exact=True).wait_for()
            page.get_by_role("button", name="Отмена", exact=True).click()
            page.wait_for_function("!state.reasoningBusy")
            assert len(patches) == 1 and page.locator("#reasoning").input_value() == "max"
            choose_mode("Pro")
            page.get_by_role("button", name="Сменить режим", exact=True).click()
            page.wait_for_function("!state.reasoningBusy && state.codingSession.reasoning_mode === 'pro'")
            assert patches[-1]["acknowledge_cache_cost"] is True
            page.evaluate("setRunning(true)")
            assert page.get_by_role("combobox", name="Режим разработки", exact=True).is_disabled()
            page.evaluate("setRunning(false)")

            page.evaluate("""() => {
              renderPlan([{title:'Изучить проект',status:'completed'},{title:'Внести изменения',status:'running'},{title:'Проверить',status:'pending'}],
                {turn_id:10,ready:true,document:'# План проверки\\n\\nВидимое начало документа.\\n\\n'+('## Раздел\\n\\nТекст плана.\\n\\n'.repeat(12))});
            }""")
            assert not page.locator("#plan-approval details").evaluate("node=>node.open")
            assert page.locator(".plan-document-body").is_visible()
            assert 70 <= page.locator(".plan-document-body").bounding_box()["height"] <= 115
            assert page.locator("#live-plan [aria-current=step]").inner_text() == "Внести изменения"
            page.locator("#plan-approval summary").click()
            page.wait_for_timeout(270)
            assert page.locator(".plan-document-body").bounding_box()["height"] > 400
            page.locator("#plan-approval summary").click()
            page.wait_for_timeout(270)
            assert page.locator(".plan-document-body").bounding_box()["height"] <= 115
            page.screenshot(path=str(output / "plan-desktop.png"))

            page.evaluate("state.project=state.projects[0]")
            page.evaluate("openFile('notes.md')")
            page.locator(".file-editor").fill("# Черновик\n\n|a|b|\n|---|---|\n|1|2|\n\n<script>window.unsafe=true</script><img src=x onerror=alert(1)>")
            page.get_by_role("button", name="Предпросмотр", exact=True).click()
            assert page.locator(".file-preview h1").inner_text() == "Черновик"
            assert page.locator(".file-preview table").count() == 1
            assert page.locator(".file-preview script,.file-preview [onerror]").count() == 0
            page.get_by_role("button", name="Сохранить файл", exact=True).click()
            page.wait_for_function("state.editor.hash === 'saved'")
            assert saves[-1]["content"].startswith("# Черновик")
            page.get_by_role("button", name="Код", exact=True).click()
            assert page.locator(".file-editor").input_value().startswith("# Черновик")
            page.locator(".file-editor").fill("# Ещё черновик")
            page.get_by_role("button", name="Предпросмотр", exact=True).click()
            page.evaluate("clearWorkspaceEditor();openFile('notes.md')")
            page.wait_for_function("state.editor && document.querySelector('.file-editor')?.value === '# Ещё черновик'")

            result = page.evaluate("""async () => {
              clearFeed();state.followFeed=true;
              renderChatMessages([{id:44,role:'assistant',content:'старый ответ',metadata:{status:'running'}}]);
              queueStreamDelta(1,{message_id:44,content:' хвост'});
              const events=[['message.reset',{message_id:44}],['message.delta',{message_id:44,content:'Новый ответ'}],
                ['agent.usage',{usage:{prompt_tokens:100,context_tokens_limit:200000,prompt_tokens_details:{cached_tokens:60},completion_tokens_details:{reasoning_tokens:12}},dokky:{billing:{charged_rub:null,remaining_percent:91,window_used_percent:4}}}]];
              const payload=events.map(([event,data])=>'event: '+event+'\\ndata: '+JSON.stringify(data)+'\\n\\n').join('');
              await streamResponse(new Response(payload),1,new AbortController());
              await new Promise(resolve=>setTimeout(resolve,180));
              const text=state.messages.get('44').querySelector('.message-body').textContent;
              return {text,buffer:state.streamBuffers.get('44').content,source:state.messageSources.get('44').content,
                usage:Boolean(document.querySelector('#coding-usage')),limits:dokkyState.limits};
            }""")
            assert result["text"].strip() == result["buffer"] == "Новый ответ", result
            # статистика и цена запроса пользователю не показываются; остаток лимита уходит в меню аккаунта
            assert result["source"] == "" and not result["usage"], result
            assert result["limits"]["remaining"] == 91 and result["limits"]["windowRemaining"] == 89, result

            page.evaluate("setPanel('right',true,false);setPanel('left',true,false)")
            for width, height in [(1440, 900), (390, 844), (320, 568)]:
                page.set_viewport_size({"width": width, "height": height})
                page.evaluate("""() => {
                  clearFeed();state.followFeed=true;
                  renderChatMessages(Array.from({length:24},(_,n)=>({id:100+n,role:n%2?'assistant':'user',content:'Сообщение '+n+'\\n\\n'+('Текст '.repeat(40)),metadata:{status:'completed'}})));
                  document.querySelector('#prompt').value='Строка\\n'.repeat(5);autoSize();
                }""")
                page.wait_for_timeout(300)
                geometry = page.evaluate("""() => {
                  const feed=$('#feed'), main=$('main').getBoundingClientRect(), rect=feed.getBoundingClientRect();
                  const header=$('.chat-header').getBoundingClientRect(), composer=$('#composer').getBoundingClientRect();
                  const last=feed.lastElementChild.getBoundingClientRect();
                  feed.scrollTop=0;const first=feed.firstElementChild.getBoundingClientRect();
                  return {full:Math.abs(main.height-rect.height)<1,first:first.top>=header.bottom,last:last.bottom<=composer.top+1,
                    width:document.documentElement.scrollWidth<=innerWidth,following:state.followFeed};
                }""")
                assert all(geometry.values()), (width, geometry)
                page.evaluate("state.followFeed=false;$('#feed').scrollTop=200;$('#prompt').value='Строка\\n'.repeat(7);autoSize()")
                before = page.locator("#feed").evaluate("node=>node.scrollTop")
                page.wait_for_timeout(150)
                assert abs(page.locator("#feed").evaluate("node=>node.scrollTop") - before) < 2
                page.screenshot(path=str(output / f"layout-{width}.png"))

            page.evaluate("switchView('projects')")
            assert page.locator("#composer").is_hidden()
            assert page.locator(".chat-header").evaluate("node=>getComputedStyle(node).position") != "absolute"
            page.evaluate("dokkyState.scope='chat';window.koddiProductScope='chat';updateProductNavigation()")
            assert page.get_by_role("combobox", name="Режим разработки", exact=True).is_hidden()
            assert not errors, errors
            assert not unexpected, unexpected
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    print(f"Проверки layout, планов, Markdown, режимов и SSE пройдены: {output}")


if __name__ == "__main__":
    main()

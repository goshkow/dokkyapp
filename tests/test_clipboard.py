"""Clipboard-проверки без личного профиля, сети и AI."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright
from PySide6.QtCore import QUrl

from koddi.desktop import web_host


WEB = Path(__file__).parents[1] / "backend/koddi/desktop/web"


@pytest.fixture
def clipboard_page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('<div id="status"></div><div id="feed"></div><textarea id="prompt"></textarea>')
        for name in ("vendor/lucide.js", "vendor/marked.js", "vendor/purify.js"):
            page.add_script_tag(path=str(WEB / name))
        source = (WEB / "app.js").read_text(encoding="utf-8")
        page.add_script_tag(content=source.split("const startup = start()", 1)[0])
        page.add_script_tag(path=str(WEB / "stream-markdown.js"))
        page.add_script_tag(path=str(WEB / "work-progress.js"))
        page.add_script_tag(path=str(WEB / "cloud-progress.js"))
        page.add_script_tag(path=str(WEB / "cloud-media.js"))
        page.add_script_tag(path=str(WEB / "cloud-actions.js"))
        source = (WEB / "dokky.js").read_text(encoding="utf-8")
        page.add_script_tag(content=source.split("startup.then(initDokky)", 1)[0])
        page.evaluate("""() => {
          window.copies=[];window.bubbles=0;window.unhandled=0;
          window.addEventListener('unhandledrejection',()=>++unhandled);
          document.querySelector('#feed').addEventListener('click',()=>++bubbles);
          Object.defineProperty(navigator,'clipboard',{configurable:true,value:{
            writeText:async text=>{copies.push(text);}
          }});
        }""")
        yield page
        browser.close()


@pytest.mark.parametrize("scope", ["development", "chat", "search"])
@pytest.mark.parametrize("role", ["user", "assistant"])
def test_copy_message_web(clipboard_page, scope, role):
    page = clipboard_page
    text = "Текст **markdown** <>& ' \"\nВторая строка"
    page.evaluate("""({scope,role,text}) => {
      window.koddiProductScope=scope;dokkyState.scope=scope;
      const message=cloudMessage({id:'copy-1',role,content:text});
      const node=document.createElement('section');
      document.querySelector('#feed').append(node);
      updateMessage(node,message);
    }""", {"scope": scope, "role": role, "text": text})
    page.get_by_role("button", name="Скопировать сообщение", exact=True).locator("svg").click()
    assert page.evaluate("copies") == [text]
    assert page.locator("#toast").text_content() == "Сообщение скопировано"
    assert page.evaluate("bubbles") == 0
    assert page.evaluate("unhandled") == 0


def test_copy_code_after_stream_reconciliation(clipboard_page):
    page = clipboard_page
    page.evaluate("""() => {
      const body=document.createElement('div');body.id='code-test';
      document.querySelector('#feed').append(body);
      renderMarkdown(body,'```python\\nprint(1)\\n```');
      window.originalCopy=body.querySelector('button');
      const draft=document.createElement('div');
      renderMarkdown(draft,'```python\\nprint("Новый <>&")\\n```');
      reconcileStreamNodes(body,draft);
    }""")
    assert page.evaluate("originalCopy===document.querySelector('#code-test button')")
    page.get_by_role("button", name="Скопировать код").locator("svg").click()
    assert page.evaluate("copies") == ['print("Новый <>&")\n']
    assert page.locator("#toast").text_content() == "Код скопирован"
    assert page.evaluate("bubbles") == 0


@pytest.mark.parametrize("failure", ["missing", "denied", "native-false", "native-error", "native-missing"])
def test_copy_failure_never_reports_success(clipboard_page, failure):
    page = clipboard_page
    page.evaluate("""failure => {
      if(failure==='missing')Object.defineProperty(navigator,'clipboard',{value:undefined});
      if(failure==='denied')navigator.clipboard.writeText=async()=>{throw Error('private detail');};
      if(failure.startsWith('native')) {
        window.koddiDesktop=true;
        if(failure==='native-false')window.koddiClipboardWrite=async()=>false;
        if(failure==='native-error')window.koddiClipboardWrite=async()=>{throw Error('private detail');};
      }
      document.querySelector('#feed').append(messageActions({role:'user',content:'test'}));
    }""", failure)
    page.get_by_role("button", name="Скопировать сообщение", exact=True).click()
    assert page.locator("#toast").text_content() == "Не удалось скопировать в буфер обмена"
    assert "error" in page.locator("#toast").get_attribute("class")
    assert page.evaluate("copies") == []
    assert page.evaluate("bubbles") == 0
    assert page.evaluate("unhandled") == 0


def test_desktop_copy_waits_for_native_ack_and_ignores_synthetic_click(clipboard_page):
    page = clipboard_page
    page.evaluate("""() => {
      window.koddiDesktop=true;window.nativeCalls=0;
      window.koddiClipboardWrite=(text,event)=>{
        ++nativeCalls;window.nativeTrusted=event.isTrusted;
        return new Promise(resolve=>window.ack=resolve);
      };
      document.querySelector('#feed').append(messageActions({role:'user',content:'test'}));
      document.querySelector('button').click();
    }""")
    assert page.evaluate("nativeCalls") == 0
    assert page.locator("#toast").count() == 0
    page.get_by_role("button", name="Скопировать сообщение", exact=True).click()
    assert page.evaluate("nativeCalls===1 && nativeTrusted")
    assert page.locator("#toast").count() == 0
    page.evaluate("ack(true)")
    assert page.locator("#toast").text_content() == "Сообщение скопировано"
    assert page.evaluate("copies") == []


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:8877/ui/", "http://127.0.0.1:8878/ui/",
    "http://localhost:8877/ui/", "https://evil.example/ui/", "about:blank",
    "http://127.0.0.1:8877/api/health", "http://user@127.0.0.1:8877/ui/",
])
def test_native_clipboard_rejects_untrusted_page(monkeypatch, url):
    writes = []
    monkeypatch.setattr(web_host.QApplication, "clipboard", lambda: SimpleNamespace(setText=writes.append))
    view = SimpleNamespace(url=lambda: QUrl(url))
    bridge = SimpleNamespace(host=SimpleNamespace(api_base="http://127.0.0.1:8877", view=view), view=view, role="main")
    assert web_host.WindowBridge.copyText(bridge, "test") is False
    assert writes == []


def test_native_clipboard_write_only_and_limits(monkeypatch):
    writes = []
    monkeypatch.setattr(web_host.QApplication, "clipboard", lambda: SimpleNamespace(
        setText=writes.append, ownsClipboard=lambda: True,
    ))
    view = SimpleNamespace(url=lambda: QUrl("http://127.0.0.1:8877/ui/"))
    bridge = SimpleNamespace(host=SimpleNamespace(api_base="http://127.0.0.1:8877", view=view), view=view, role="main")
    assert web_host.WindowBridge.copyText(bridge, "Текст\n<>&") is True
    assert writes == ["Текст\n<>&"]
    assert web_host.WindowBridge.copyText(bridge, "x" * (8 * 1024 * 1024 + 1)) is False
    assert len(writes) == 1
    monkeypatch.setattr(web_host.QApplication, "clipboard", lambda: SimpleNamespace(
        setText=lambda text: None, ownsClipboard=lambda: False, text=lambda: "старое",
    ))
    assert web_host.WindowBridge.copyText(bridge, "test") is False
    # macOS: ownsClipboard всегда False, успех подтверждает обратное чтение буфера
    stored = []
    monkeypatch.setattr(web_host.QApplication, "clipboard", lambda: SimpleNamespace(
        setText=stored.append, ownsClipboard=lambda: False, text=lambda: stored[-1],
    ))
    assert web_host.WindowBridge.copyText(bridge, "test") is True
    monkeypatch.setattr(web_host.QApplication, "clipboard", lambda: None)
    assert web_host.WindowBridge.copyText(bridge, "test") is False
    monkeypatch.setattr(web_host.QApplication, "clipboard", lambda: SimpleNamespace(
        setText=lambda text: (_ for _ in ()).throw(RuntimeError("private detail")),
    ))
    assert web_host.WindowBridge.copyText(bridge, "test") is False

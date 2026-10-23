"""Изолированная проверка копирования в защищённом desktop origin Qt."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time

os.environ["KODDI_DATA_DIR"] = tempfile.mkdtemp(prefix="koddi-clipboard-smoke-")
os.environ["KODDI_INTERNAL_TEST"] = "1"
os.environ["KODDI_PORTABLE"] = "1"

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from koddi.desktop.server import start_api_server
from koddi.desktop.transport import desktop_access
from koddi.desktop.web_host import WebHost, _available_port


def main():
    app = QApplication([])
    port = _available_port()
    key = desktop_access.enable()
    threading.Thread(
        target=start_api_server, args=(port,), kwargs={"private": True}, daemon=True,
    ).start()
    host = WebHost(port, key)
    host.resize(1200, 800)
    host.show()

    def wait(predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            QTest.qWait(25)
        raise AssertionError("Истекло время ожидания clipboard-проверки")

    def js(source):
        result = []
        host.view.page().runJavaScript(source, result.append)
        wait(lambda: bool(result))
        return result[0]

    def click(selector):
        point = json.loads(js("""JSON.stringify((() => {
          const button=document.querySelector(%s);button.scrollIntoView({block:'center'});
          const r=button.getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2];
        })())""" % json.dumps(selector)))
        QTest.qWait(100)
        QTest.mouseClick(
            host.view.focusProxy(), Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier, QPoint(round(point[0]), round(point[1])),
        )

    try:
        wait(lambda: js("typeof state!=='undefined' && "
                        "Object.keys(state.settings).length>0 && "
                        "document.documentElement.dataset.windowControls==='ready'"))
        import httpx

        assert httpx.get(host.api_base + "/ui/").status_code == 404
        js("""window.clipboardBubbles=0;
          window.clipboardSendHandler=document.querySelector('#send').onclick;
          document.querySelector('#feed').addEventListener('click',()=>++clipboardBubbles);
          Object.defineProperty(navigator,'clipboard',{configurable:true,value:{
            writeText:async()=>{throw new DOMException('Denied','NotAllowedError');}
          }});
          newChat();state.chat={id:9999901};state.followFeed=false;
        """)
        user = "Мой текст <>& ' \"\nВторая строка"
        code = "print('Привет <>&')\n"
        assistant = "Ответ **AI**\n\n```python\n" + code + "```"
        for scope in ("development", "chat", "search"):
            js("""window.koddiProductScope=%s;dokkyState.scope=%s;
              renderChatMessages(%s);
            """ % (json.dumps(scope), json.dumps(scope), json.dumps([
                {"id": 991, "role": "user", "content": user, "metadata": {}},
                {"id": 992, "role": "assistant", "content": assistant, "metadata": {}},
            ])))
            for selector, expected, notice in (
                ('.message.user [aria-label="Скопировать сообщение"]', user, "Сообщение скопировано"),
                ('.message.assistant [aria-label="Скопировать сообщение"]', assistant, "Сообщение скопировано"),
                ('.code-head button', code, "Код скопирован"),
            ):
                QApplication.clipboard().setText("clipboard-test-sentinel")
                click(selector)
                try:
                    wait(lambda: QApplication.clipboard().text() == expected)
                except AssertionError:
                    details = js("""JSON.stringify({
                      toast:document.querySelector('#toast')?.textContent,
                      bubbles:clipboardBubbles,focus:document.activeElement.tagName
                    })""")
                    raise AssertionError(details) from None
                assert js("document.querySelector('#toast').textContent") == notice
                assert not js("document.querySelector('#toast').classList.contains('error')")
        # старый обработчик должен читать актуальный code, а не отделённый draft
        js("""const draft=document.createElement('div');
          renderMarkdown(draft,'```python\\nprint(123)\\n```');
          reconcileStreamNodes(document.querySelector('.message.assistant .message-body'),draft);
        """)
        click('.code-head button')
        wait(lambda: QApplication.clipboard().text() == "print(123)\n")
        QApplication.clipboard().setText("clipboard-test-sentinel")
        assert js("""document.querySelector('.code-head button').click();
          !document.querySelector('#send').disabled""")
        QTest.qWait(100)
        assert QApplication.clipboard().text() == "clipboard-test-sentinel"
        js("""window.syntheticCopyResult=null;
          koddiClipboardWrite('synthetic-copy',new MouseEvent('click'))
            .then(result=>window.syntheticCopyResult=result);
        """)
        wait(lambda: js("window.syntheticCopyResult===false"))
        assert QApplication.clipboard().text() == "clipboard-test-sentinel"
        js("document.querySelector('.code-head button').focus()")
        QTest.keyClick(host.view.focusProxy(), Qt.Key.Key_Space)
        wait(lambda: QApplication.clipboard().text() == "print(123)\n")
        assert js("clipboardBubbles") == 0
        assert js("document.querySelector('#send').onclick===clipboardSendHandler")
        js("document.querySelector('[data-window-action=\"maximize\"]').click()")
        wait(host.isMaximized)
        js("document.querySelector('[data-window-action=\"maximize\"]').click()")
        wait(lambda: not host.isMaximized())
        assert host._caption_ready
        js("window.koddiClipboardWrite=async()=>false;document.querySelector('#toast').hidden=true")
        QApplication.clipboard().setText("clipboard-test-sentinel")
        click('.code-head button')
        wait(lambda: js("!document.querySelector('#toast').hidden"))
        assert js("document.querySelector('#toast').classList.contains('error')")
        assert QApplication.clipboard().text() == "clipboard-test-sentinel"
        print("Qt clipboard: 9 проверок user/assistant/code, 3 режима, "
              "защищённый origin, отказ Clipboard API, обновление кода, "
              "клавиатура, запрет synthetic click, click propagation, "
              "Send/Stop, оконный мост и отказ записи - успешно")
    finally:
        QApplication.clipboard().clear()
        host.close()
        app.processEvents()


if __name__ == "__main__":
    main()

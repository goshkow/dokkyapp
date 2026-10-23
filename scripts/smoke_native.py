"""Проверка Qt-окна без экрана: рендер WebEngine, мост и геометрия при ресайзе."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ["KODDI_DATA_DIR"] = tempfile.mkdtemp(prefix="koddi-native-smoke-")
os.environ['KODDI_INTERNAL_TEST'] = '1'
# без экрана GPU-композиция теряет контекст: проверка идёт на программной отрисовке
os.environ.setdefault('KODDI_WEBENGINE_GPU', '0')
os.environ["QT_QPA_PLATFORM"] = "windows" if "--windows" in sys.argv else "offscreen"

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from koddi.desktop.server import start_api_server
from koddi.desktop.web_host import WebHost, _available_port
from koddi.desktop.transport import desktop_access


def main():
    port = _available_port()
    key = desktop_access.enable()
    threading.Thread(
        target=start_api_server, args=(port,), kwargs={"private": True}, daemon=True
    ).start()
    # проект нужен до загрузки страницы: правая панель живёт только там, где есть контекст
    import httpx

    for _ in range(100):
        try:
            httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=0.5)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    from koddi.projects.service import add_project

    workspace = Path(tempfile.mkdtemp(prefix="koddi-native-workspace-"))
    add_project(str(workspace), "smoke")
    app = QApplication([])
    host = WebHost(port, key)
    host.resize(1360, 850)
    host.show()
    output = Path(tempfile.mkdtemp(prefix="koddi-native-view-"))
    result = {"ready": False, "error": None}
    deadline = time.monotonic() + 25

    def inspect():
        host.view.grab().save(str(output / 'loading.png'))
        host.view.page().runJavaScript(
            "JSON.stringify({ready:!document.body.classList.contains('booting')&&!!document.querySelector('#shell #composer')&&document.documentElement.dataset.windowControls==='ready',bridge:window.koddiDesktop===true,title:document.title,overflow:document.documentElement.scrollWidth>innerWidth})",
            checked,
        )

    def checked(value):
        import json

        try:
            data = json.loads(value)
            result['last_state']=data
            if not data["ready"]:
                QTimer.singleShot(250, inspect)
                return
            assert data["bridge"], "Не загружен мост настольного окна"
            assert data["title"] == "Докки", f"Неожиданный заголовок: {data['title']}"
            assert not data["overflow"], "Интерфейс выходит за ширину окна"
            import httpx

            for path in ("/ui/", "/api/health", "/api/projects"):
                assert httpx.get(host.api_base + path).status_code == 404, (
                    "Доступ из браузера не закрыт"
                )
            frame = host.view.grab()
            frame.save(str(output / "native-desktop.png"))
            image = frame.toImage()
            colors = {
                image.pixelColor(x, y).rgba()
                for x in range(0, image.width(), 12)
                for y in range(0, image.height(), 12)
            }
            if len(colors) < 12:
                if time.monotonic() < deadline:
                    QTimer.singleShot(500, inspect)
                    return
                raise RuntimeError(
                    "DOM загрузился, но WebEngine не отрисовал интерфейс. Нативная визуальная проверка не пройдена."
                )
            # окно настроек и список выбора внутри него должны целиком помещаться в окно
            host.view.page().runJavaScript("""try {
              document.querySelector('#connection-badge').click();
              setTimeout(()=>{try{document.querySelector('dialog.settings-window .select-trigger').click();}catch(error){window.nativeMenuError=error.message;}},400);
            } catch(error) {window.nativeMenuError=error.message;}""")
            QTimer.singleShot(1100, inspect_menu)
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            app.quit()

    def inspect_menu():
        host.view.page().runJavaScript(
            """JSON.stringify((() => {
          const dialog=document.querySelector('dialog.settings-window[open]');
          const menu=document.querySelector('.popover');
          if(!dialog)return {error:window.nativeMenuError||'Окно настроек не открылось'};
          const box=(node)=>{const r=node?.getBoundingClientRect();return !!r&&r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight;};
          return {open:!!menu,hidden:true,fits:box(dialog)&&box(menu)};
        })())""",
            checked_menu,
        )
    def checked_menu(value):
        import json

        try:
            data = json.loads(value)
            assert data.get("open") and data.get("hidden") and data.get("fits"), f"Меню Qt: {data}"
            host.view.grab().save(str(output / "native-menu.png"))
            result["load_attempts"] = host._load_attempts
            host.view.page().runJavaScript(
                "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));document.querySelector('dialog.settings-window')?.close();window.nativeStableMarker=true;window.location.href='koddi://product?mode=development'"
            )
            QTimer.singleShot(700, inspect_stability)
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            app.quit()

    def inspect_stability():
        host.view.page().runJavaScript("window.nativeStableMarker===true", checked_stability)

    def checked_stability(stable):
        try:
            assert stable and host._load_attempts == result["load_attempts"], (
                "Служебный переход перезагрузил интерфейс"
            )
            host.resize(980, 680)
            host.move(40, 50)
            assert host.size().width() == 980, f"Размер окна: {host.size()}"
            right = host._resize_handles["right"].geometry()
            assert right.width() == 3 and right.x() >= host.width() - 3, (
                f"Зона ресайза: {right}; размер окна: {host.size()}"
            )
            # правая панель в узком окне расширяет окно, а не сжимает чат; при закрытии окно не уменьшается
            result["narrow_width"] = host.width()
            # правая панель появляется только там, где есть контекст: открываем новый чат в проекте
            QTimer.singleShot(100, open_project_chat)
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            app.quit()

    def open_project_chat():
        host.view.page().runJavaScript("document.querySelector('[aria-label^=\"Новый чат в проекте\"]').click()")
        QTimer.singleShot(1200, check_growth)

    def click_selector(selector):
        # настоящий щелчок мышью: у кода на странице нет жеста пользователя, а переход koddi:// без него блокируется
        def clicked(value):
            import json

            from PySide6.QtCore import QPoint, Qt
            from PySide6.QtTest import QTest

            box = json.loads(value)
            QTest.mouseClick(host.view.focusProxy(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(box["x"]), int(box["y"])))

        host.view.page().runJavaScript(
            f"(() => {{ const r = document.querySelector('{selector}').getBoundingClientRect(); return JSON.stringify({{x: r.left + r.width / 2, y: r.top + r.height / 2}}); }})()",
            clicked,
        )

    def check_growth():
        try:
            assert host.width() > result["narrow_width"], f"Окно не выросло под правую панель: {host.width()}"
            result["grown_width"] = host.width()
            host.view.page().runJavaScript("document.querySelector('#toggle-right').click()")
            QTimer.singleShot(900, check_no_shrink)
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            app.quit()

    def check_no_shrink():
        try:
            assert host.width() == result["grown_width"], f"Окно уменьшилось при закрытии панели: {host.width()}"
            # заголовок окна следует за режимом: «Докки» в чате, «Докки Code» в разработке
            click_selector("#scope-toggle")
            QTimer.singleShot(1200, check_title_chat)
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            app.quit()

    def check_title_chat():
        try:
            assert host.windowTitle() == "Докки", f"Заголовок окна в чате: {host.windowTitle()}"
            click_selector("#scope-toggle")
            QTimer.singleShot(1200, check_title_development)
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            app.quit()

    def check_title_development():
        try:
            assert host.windowTitle() == "Докки Code", f"Заголовок окна в разработке: {host.windowTitle()!r}"
            QTimer.singleShot(100, finish)
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            app.quit()

    def finish():
        host.view.grab().save(str(output / "native-compact.png"))
        result["ready"] = True
        app.quit()

    QTimer.singleShot(600, inspect)
    QTimer.singleShot(30000, app.quit)
    app.exec()
    host.close()
    if result["error"] or not result["ready"]:
        raise RuntimeError(result["error"] or f"Qt host не загрузился за 30 секунд: {result.get('last_state')}; {output}")
    print(output)


if __name__ == "__main__":
    main()

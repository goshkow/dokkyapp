import ctypes
import json
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

import pytest

from koddi.desktop import web_host


@pytest.mark.parametrize("scale", [1, 1.25, 1.5, 2, 3])
def test_caption_geometry_matches_css_at_dpi_and_page_zoom(scale):
    hit = web_host.WindowsFrame.caption_hit_test
    width = 1000 * scale
    for x, expected in [(885, 8), (931, 9), (977, 20)]:
        assert hit(x * scale, 20 * scale, width, scale) == expected
    for x, y in [(0, 20), (861, 20), (999, 20), (931, 5), (931, 42)]:
        assert hit(x * scale, y * scale, width, scale) is None
    assert hit(999 * scale, 0, width, scale, maximized=True) == 20


def test_hit_test_decodes_signed_monitor_coordinates():
    def client_origin(hwnd, point):
        point._obj.x, point._obj.y = -1800, -900
        return True

    def client_rect(hwnd, rect):
        rect._obj.right = 1000
        return True

    frame = SimpleNamespace(
        user32=SimpleNamespace(ClientToScreen=client_origin, GetClientRect=client_rect),
        caption_hit_test=web_host.WindowsFrame.caption_hit_test,
    )
    host = SimpleNamespace(
        _caption_ready=True, isVisible=lambda: True, isFullScreen=lambda: False,
        isMinimized=lambda: False, isMaximized=lambda: False, winId=lambda: 123,
        devicePixelRatioF=lambda: 1, view=SimpleNamespace(zoomFactor=lambda: 1),
    )
    lparam = ((-880 & 0xFFFF) << 16) | (-869 & 0xFFFF)
    assert web_host.WindowsFrame.hit_test(frame, host, lparam) == 9
    host._caption_ready = False
    assert web_host.WindowsFrame.hit_test(frame, host, lparam) is None


def test_dwm_gets_first_refusal_for_nonclient_messages():
    def dwm(hwnd, message, wparam, lparam, result):
        result._obj.value = 77
        return True

    host = SimpleNamespace(_windows_frame=SimpleNamespace(
        dwmapi=SimpleNamespace(DwmDefWindowProc=dwm),
        hit_test=lambda *args: pytest.fail("DWM уже обработал сообщение"),
    ))
    msg = wintypes.MSG(message=0x84)
    assert web_host.WebHost.nativeEvent(
        host, b"windows_generic_MSG", ctypes.addressof(msg)
    ) == (True, 77)


def test_html_caption_is_explicit_glyph_fallback_without_lucide_dependency():
    source = Path("backend/koddi/desktop/web/desktop-window.js").read_text(encoding="utf-8")
    assert "windows-caption-glyph" in source
    assert "icon(" not in source and "innerHTML" not in source
    for glyph in (r"\uE921", r"\uE922", r"\uE923", r"\uE8BB"):
        assert glyph in source


@pytest.mark.parametrize("message", [0xA0])
def test_nonclient_caption_mouse_uses_system_processing(message):
    calls = []
    frame = SimpleNamespace(
        dwmapi=SimpleNamespace(DwmDefWindowProc=lambda *args: False),
        user32=SimpleNamespace(DefWindowProcW=lambda *args: calls.append(args) or 5),
        track_leave=lambda hwnd: None,
    )
    host = SimpleNamespace(
        _windows_frame=frame, _caption_hover="maximize", _sync_window_frame=lambda: None,
    )
    msg = wintypes.MSG(hWnd=123, message=message, wParam=9, lParam=456)
    assert web_host.WebHost.nativeEvent(
        host, b"windows_generic_MSG", ctypes.addressof(msg)
    ) == (True, 5)
    assert calls == [(123, message, 9, 456)]


@pytest.mark.parametrize('code', [8, 9, 20])
def test_caption_click_dispatches_once_on_matching_release(monkeypatch, code):
    actions = []
    monkeypatch.setattr(web_host.QTimer, 'singleShot', lambda delay, fn: fn())
    host = SimpleNamespace(
        _windows_frame=SimpleNamespace(dwmapi=SimpleNamespace(DwmDefWindowProc=lambda *args: False)),
        _caption_pressed=0, run_caption_action=actions.append,
    )
    for message in (0xA1, 0xA2, 0xA3):
        msg = wintypes.MSG(hWnd=123, message=message, wParam=code, lParam=456)
        assert web_host.WebHost.nativeEvent(host, b'windows_generic_MSG', ctypes.addressof(msg)) == (True, 0)
    assert actions == [code] and host._caption_pressed == 0


def test_caption_release_without_matching_press_does_not_dispatch(monkeypatch):
    actions = []
    monkeypatch.setattr(web_host.QTimer, 'singleShot', lambda delay, fn: fn())
    host = SimpleNamespace(
        _windows_frame=SimpleNamespace(dwmapi=SimpleNamespace(DwmDefWindowProc=lambda *args: False)),
        _caption_pressed=8, run_caption_action=actions.append,
    )
    msg = wintypes.MSG(hWnd=123, message=0xA2, wParam=9, lParam=456)
    assert web_host.WebHost.nativeEvent(host, b'windows_generic_MSG', ctypes.addressof(msg)) == (True, 0)
    assert actions == [] and host._caption_pressed == 0


def test_native_hover_clears_even_when_dwm_consumes_leave():
    called = []
    host = SimpleNamespace(
        _windows_frame=SimpleNamespace(dwmapi=SimpleNamespace(DwmDefWindowProc=lambda *args: True)),
        _caption_hover="maximize", _sync_window_frame=lambda: called.append(True),
    )
    msg = wintypes.MSG(message=0x2A2)
    web_host.WebHost.nativeEvent(host, b"windows_generic_MSG", ctypes.addressof(msg))
    assert host._caption_hover == "" and called == [True]


@pytest.mark.parametrize("build, expected", [(19045, []), (22000, [20, 33, 34, 35, 36])])
def test_dwm_decoration_attributes_are_gated_to_windows_11(monkeypatch, build, expected):
    attributes = []
    frame = SimpleNamespace(
        get_style=lambda *args: 0x00CF0000,
        dwmapi=SimpleNamespace(DwmSetWindowAttribute=lambda hwnd, attribute, *args: attributes.append(attribute) or 0),
    )
    host = SimpleNamespace(_windows_frame=frame, winId=lambda: 123, _sync_window_frame=lambda: None, _initial_show_done=True)
    monkeypatch.setattr(web_host.sys, "getwindowsversion", lambda: SimpleNamespace(build=build), raising=False)
    web_host.WebHost._enable_native_frame(host)
    assert attributes == expected


# на раннерах GitHub нет интерактивного рабочего стола, QtWebEngine там падает с access violation
@pytest.mark.skipif(sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") == "true",
                    reason="нужен настоящий Windows HWND и рабочий стол")
@pytest.mark.parametrize("scale", ["1", "1.5"])
def test_windows_caption_hwnd_in_isolated_process(tmp_path, scale):
    source = r'''
import ctypes
import json
import os
from pathlib import Path
from ctypes import wintypes
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtWebEngineCore import QWebEngineProfile
from koddi.desktop.web_host import WebHost

app = QApplication([])
app.setApplicationName('koddi-caption-test')
profile = QWebEngineProfile.defaultProfile()
profile.setPersistentStoragePath(str(Path(os.environ['KODDI_DATA_DIR']) / 'webengine'))
profile.setCachePath(str(Path(os.environ['KODDI_DATA_DIR']) / 'cache'))
profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
WebHost._load_ui = lambda self: None
host = WebHost(1)
host.resize(1000, 700)
host.move(-900, -400)
host.show()
result = {}

def check():
    try:
        hwnd = int(host.winId())
        frame = host._windows_frame
        assert frame is not None
        assert frame.get_style(hwnd, -16) & 0x000F0000 == 0x000F0000
        assert frame.get_style(hwnd, -16) & 0x00C00000 == 0x00C00000  # нативные анимации; рамку убирает WM_NCCALCSIZE
        host._caption_ready = True
        send = frame.user32.SendMessageW
        send.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
        send.restype = ctypes.c_ssize_t
        for zoom in (1, 1.5):
            host.view.setZoomFactor(zoom)
            origin, rect = wintypes.POINT(), wintypes.RECT()
            assert frame.user32.ClientToScreen(hwnd, ctypes.byref(origin))
            assert frame.user32.GetClientRect(hwnd, ctypes.byref(rect))
            factor = host.devicePixelRatioF() * zoom
            hits = []
            for offset in (115, 69, 23):
                x = round(origin.x + rect.right - offset * factor)
                y = round(origin.y + 20 * factor)
                hits.append(send(hwnd, 0x84, 0, (y & 0xffff) << 16 | (x & 0xffff)))
            assert hits == [8, 9, 20], hits
        child = QWidget(host)
        child.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        child.setGeometry(host.width() - 100, 10, 80, 30)
        child.show()
        assert send(int(child.winId()), 0x84, 0, (y & 0xffff) << 16 | (x & 0xffff)) == -1
        child.close()
        host.view.setZoomFactor(1)
        send(hwnd, 0x112, 0xF030, 0)
        app.processEvents()
        assert host.isMaximized()
        send(hwnd, 0x112, 0xF120, 0)
        app.processEvents()
        assert not host.isMaximized()
        send(hwnd, 0x112, 0xF020, 0)
        app.processEvents()
        assert host.isMinimized()
        send(hwnd, 0x112, 0xF120, 0)
        app.processEvents()
        host._reset_caption(None)
        assert not host._caption_ready
        assert host.view.y() == 0
        result['ok'] = True
        result['mode'] = host._window_frame_state()['mode']
        send(hwnd, 0x112, 0xF060, 0)
        app.processEvents()
        assert not host.isVisible()
    except BaseException as exc:
        result['error'] = __import__('traceback').format_exc()
    finally:
        host.close()
        print(json.dumps(result), flush=True)
        app.quit()

QTimer.singleShot(500, check)
QTimer.singleShot(10000, app.quit)
app.exec()
assert result.get('ok') and 'error' not in result, result
'''
    env = dict(os.environ, KODDI_DATA_DIR=str(tmp_path / "profile"),
               KODDI_PORTABLE="1", QT_QPA_PLATFORM="windows", QT_SCALE_FACTOR=scale)
    result = subprocess.run([sys.executable, "-c", source], env=env, capture_output=True,
                            text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["mode"] == "windows-caption-glyph"


def test_caption_render_keyboard_initial_state_and_hover(tmp_path):
    from playwright.sync_api import sync_playwright

    web = Path("backend/koddi/desktop/web")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 980, "height": 680})
            page.set_content('<html class="desktop-window"><body style="margin:0;background:#101010"></body></html>')
            page.add_style_tag(content=(web / "desktop-window.css").read_text(encoding="utf-8"))
            page.evaluate("""() => {
              window.commands = [];
              window.qt = {webChannelTransport: {}};
              window.QWebChannel = function(transport, ready) {
                ready({objects: {koddiWindow: {
                  command: action => commands.push(action),
                  frameChanged: {connect: callback => window.frameChanged = callback},
                  requestState: () => frameChanged({mode:'windows-caption-glyph',maximized:true,hover:''})
                }}});
              };
            }""")
            page.add_script_tag(content=(web / "desktop-window.js").read_text(encoding="utf-8"))
            maximize = page.get_by_role("button", name="Восстановить")
            assert maximize.text_content() == "\uE923"
            for width in (980, 1440):
                page.set_viewport_size({"width": width, "height": 680})
                for button in page.locator(".desktop-window-controls button").all():
                    bounds = button.bounding_box()
                    assert bounds["width"] == 46 and bounds["height"] == 38
                    assert 0 <= bounds["x"] <= width - 46
                page.locator(".desktop-window-controls").screenshot(path=str(tmp_path / f"caption-{width}.png"))
            maximize.focus()
            page.keyboard.press("Enter")
            assert page.evaluate("commands") == ["maximize"]
            page.evaluate("frameChanged({mode:'windows-caption-glyph',maximized:false,hover:'close'})")
            assert page.get_by_role("button", name="Развернуть").text_content() == "\uE922"
            close = page.get_by_role("button", name="Закрыть")
            assert "native-hover" in close.get_attribute("class")
            assert close.evaluate("e => getComputedStyle(e).backgroundColor") == "rgb(196, 43, 28)"
            page.evaluate("frameChanged({mode:'windows-caption-glyph',maximized:false,hover:''})")
            assert "native-hover" not in (close.get_attribute("class") or "")
            page.get_by_role("button", name="Свернуть").click()
            close.click()
            assert page.evaluate("commands") == ["maximize", "minimize", "close"]
            assert page.locator("svg").count() == 0
        finally:
            browser.close()


def test_nccalcsize_makes_client_cover_whole_window():
    rect = wintypes.RECT(0, 0, 1000, 800)
    calls = []
    frame = SimpleNamespace(
        user32=SimpleNamespace(IsZoomed=lambda hwnd: bool(calls.append("zoomed") or False)),
        deflate_maximized=lambda *args: pytest.fail("рамку сдвигаем только у развёрнутого окна"),
    )
    host = SimpleNamespace(_windows_frame=frame)
    msg = wintypes.MSG(hWnd=1, message=0x83, wParam=1, lParam=ctypes.addressof(rect))
    assert web_host.WebHost.nativeEvent(host, b"windows_generic_MSG", ctypes.addressof(msg)) == (True, 0)
    assert (rect.left, rect.top, rect.right, rect.bottom) == (0, 0, 1000, 800)


def test_maximized_client_area_is_pulled_back_inside_the_screen():
    rect = wintypes.RECT(-9, -9, 1009, 809)
    user32 = SimpleNamespace(
        GetDpiForWindow=lambda hwnd: 96,
        GetSystemMetricsForDpi=lambda index, dpi: {32: 8, 92: 1}[index],
    )
    frame = SimpleNamespace(user32=user32)
    web_host.WindowsFrame.deflate_maximized(frame, 1, ctypes.addressof(rect))
    assert (rect.left, rect.top, rect.right, rect.bottom) == (0, 0, 1000, 800)


@pytest.mark.parametrize("action, maximized, expected", [("maximize", False, "maximize"), ("maximize", True, "restore"), ("minimize", False, "minimize"), ("close", False, "close")])
def test_window_buttons_use_native_system_commands(action, maximized, expected):
    sent = []
    host = SimpleNamespace(
        _windows_frame=SimpleNamespace(system_command=lambda hwnd, name: sent.append(name) or True),
        isVisible=lambda: True, isMaximized=lambda: maximized, isMinimized=lambda: False, winId=lambda: 7,
    )
    web_host.WebHost.window_action(host, action)
    assert sent == [expected]


def test_webengine_cache_is_separate_per_version(tmp_path):
    from koddi.desktop import webengine_cache_path

    legacy = tmp_path / "cache"
    legacy.mkdir()
    (legacy / "old").write_text("cached", encoding="utf-8")
    first = webengine_cache_path(tmp_path, "0.2.2")
    second = webengine_cache_path(tmp_path, "0.2.3")
    assert first != second and first != legacy and second != legacy
    assert (legacy / "old").read_text(encoding="utf-8") == "cached"
    with pytest.raises(ValueError):
        webengine_cache_path(tmp_path, "../../outside")

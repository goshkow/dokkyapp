"""Веб-вид macOS на нативном WKWebView вместо QtWebEngine.

Chromium перерисовывает страницу уже после того, как окно изменило размер (две-три смены кадра, ~40 мс),
поэтому нативное стекло и содержимое расходились. WKWebView - обычный NSView: размер меняется вместе с окном
в том же кадре Core Animation, и стекло с содержимым не разъезжаются.

Вид выглядит для WebHost как QWebEngineView: page().runJavaScript, load, url, loadFinished, urlChanged.
Мост окна (WindowBridge) вызывается из страницы сообщениями WKScriptMessageHandler; на странице его воспроизводит
маленький QWebChannel, поэтому desktop-window.js и фронтенд не меняются.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

import objc
from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget

LOGGER = logging.getLogger("koddi.desktop")

COOKIE = "koddi_desktop_session"
STORE_ID = uuid.uuid5(uuid.NAMESPACE_URL, "https://dokky.goshkow.com/webkit-store")
# методы моста, которые страница может вызвать; всё остальное отбрасывается
BRIDGE_METHODS = (
    "relay", "regions", "appearance", "overlay", "panels", "copyText", "copyImage",
    "pageAction", "requestState", "setGlass", "adjustWidth", "command", "focusComposer",
)

SHIM = """
(() => {
  const pending = new Map(); const signals = {}; let seq = 0;
  const post = message => window.webkit.messageHandlers.koddi.postMessage(message);
  window.__koddiReply = (id, value) => { const done = pending.get(id); pending.delete(id); if (done) done(value); };
  window.__koddiSignal = (name, value) => { for (const fn of signals[name] || []) fn(value); };
  const methods = %s;
  window.qt = { webChannelTransport: {} };
  window.QWebChannel = class {
    constructor(_transport, ready) {
      const bridge = {};
      for (const name of methods) {
        bridge[name] = (...args) => {
          const done = typeof args[args.length - 1] === 'function' ? args.pop() : null;
          const id = done ? ++seq : 0;
          if (done) pending.set(id, done);
          post({ m: name, a: args, id });
        };
      }
      bridge.frameChanged = { connect: fn => { (signals.frameChanged ||= []).push(fn); } };
      this.objects = { koddiWindow: bridge };
      if (ready) ready(this);
    }
  };
})();
""" % json.dumps(list(BRIDGE_METHODS))

EDIT_ACTIONS = {
    "undo": "undo:", "redo": "redo:", "cut": "cut:", "copy": "copy:", "paste": "paste:", "selectall": "selectAll:",
}


def same_origin(url: QUrl, base: QUrl) -> bool:
    return (url.scheme(), url.host(), url.port()) == (base.scheme(), base.host(), base.port())


def _qurl(nsurl) -> QUrl:
    return QUrl(str(nsurl.absoluteString())) if nsurl is not None else QUrl()


_classes: dict = {}


def _objc_classes():
    """Объектные классы создаются при первом использовании: до этого PyObjC и WebKit не нужны."""
    if _classes:
        return _classes
    from AppKit import NSAlert, NSEvent, NSOpenPanel, NSSavePanel, NSWorkspace
    from Foundation import NSObject, NSURL
    from WebKit import WKWebView

    class KoddiWebView(WKWebView):
        def mouseDown_(self, event):  # noqa: N802
            # перетаскивание окна страница просит уже после нажатия: нужно само событие нажатия
            self.last_mouse_down = event
            objc.super(KoddiWebView, self).mouseDown_(event)

        def acceptsFirstMouse_(self, event):  # noqa: N802
            return True

    class Delegate(NSObject):
        def initWithOwner_(self, owner):  # noqa: N802
            self = objc.super(Delegate, self).init()
            if self is None:
                return None
            self.owner = owner
            return self

        # мост окна
        def userContentController_didReceiveScriptMessage_(self, _controller, message):  # noqa: N802
            self.owner._message(message)

        # переходы
        def webView_decidePolicyForNavigationAction_decisionHandler_(self, web, action, handler):  # noqa: N802
            owner = self.owner
            try:
                request = action.request()
                url = _qurl(request.URL())
                if action.shouldPerformDownload():
                    handler(2)  # WKNavigationActionPolicyDownload
                    return
                frame = action.targetFrame()
                main = frame is None or frame.isMainFrame()
                clicked = int(action.navigationType()) == 0  # WKNavigationTypeLinkActivated
                allowed = owner.host.decide_navigation(owner, url, main, clicked)
            except Exception:  # noqa: BLE001
                LOGGER.exception("Ошибка проверки перехода")
                allowed = False
            handler(1 if allowed else 0)

        def webView_decidePolicyForNavigationResponse_decisionHandler_(self, web, response, handler):  # noqa: N802
            # файл, который страница не покажет сама, скачивается через диалог сохранения
            if response.isForMainFrame() and not response.canShowMIMEType():
                handler(2)  # WKNavigationResponsePolicyDownload
            else:
                handler(1)

        def webView_navigationAction_didBecomeDownload_(self, web, action, download):  # noqa: N802
            download.setDelegate_(self)
            self.owner._download = download

        def webView_navigationResponse_didBecomeDownload_(self, web, response, download):  # noqa: N802
            download.setDelegate_(self)
            self.owner._download = download

        def webView_didCommitNavigation_(self, web, navigation):  # noqa: N802
            self.owner.urlChanged.emit(_qurl(web.URL()))

        def webView_didFinishNavigation_(self, web, navigation):  # noqa: N802
            self.owner._loaded(True)

        def webView_didFailNavigation_withError_(self, web, navigation, error):  # noqa: N802
            self.owner._loaded(False)

        def webView_didFailProvisionalNavigation_withError_(self, web, navigation, error):  # noqa: N802
            self.owner._loaded(False)

        def webViewWebContentProcessDidTerminate_(self, web):  # noqa: N802
            LOGGER.warning("Процесс страницы завершился, перезагружаю")
            web.reload()

        # загрузка файлов
        def download_decideDestinationUsingResponse_suggestedFilename_completionHandler_(  # noqa: N802
            self, download, response, name, handler
        ):
            owner = self.owner
            origin = _qurl(download.originalRequest().URL()) if download.originalRequest() is not None else QUrl()
            if origin.scheme() == "blob":
                origin = QUrl(origin.toString()[5:])
            if not same_origin(origin, QUrl(owner.host.api_base)):
                handler(None)
                return
            panel = NSSavePanel.savePanel()
            panel.setNameFieldStringValue_(Path(str(name)).name or "file")
            panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(Path.home() / "Downloads")))
            if panel.runModal() == 1:
                handler(panel.URL())
            else:
                handler(None)

        def download_didFailWithError_resumeData_(self, download, error, data):  # noqa: N802
            LOGGER.warning("Не удалось скачать файл: %s", error)

        # окна, диалоги, микрофон
        def webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(  # noqa: N802
            self, web, config, action, features
        ):
            url = _qurl(action.request().URL())
            if url.scheme() in {"http", "https"} and url.host():
                NSWorkspace.sharedWorkspace().openURL_(action.request().URL())
            return None

        def webView_requestMediaCapturePermissionForOrigin_initiatedByFrame_type_decisionHandler_(  # noqa: N802
            self, web, origin, frame, kind, handler
        ):
            # микрофон нужен только голосовому вводу своей страницы; остальное запрещено
            own = (str(origin.protocol()), str(origin.host()), int(origin.port())) == (
                QUrl(self.owner.host.api_base).scheme(),
                QUrl(self.owner.host.api_base).host(),
                QUrl(self.owner.host.api_base).port(),
            )
            handler(1 if own and int(kind) == 1 else 2)  # Grant / Deny; тип 1 - только микрофон

        def webView_runJavaScriptAlertPanelWithMessage_initiatedByFrame_completionHandler_(  # noqa: N802
            self, web, message, frame, handler
        ):
            alert = NSAlert.alloc().init()
            alert.setMessageText_(str(message))
            alert.runModal()
            handler()

        def webView_runJavaScriptConfirmPanelWithMessage_initiatedByFrame_completionHandler_(  # noqa: N802
            self, web, message, frame, handler
        ):
            alert = NSAlert.alloc().init()
            alert.setMessageText_(str(message))
            alert.addButtonWithTitle_("OK")
            alert.addButtonWithTitle_("Отмена")
            handler(alert.runModal() == 1000)

        def webView_runOpenPanelWithParameters_initiatedByFrame_completionHandler_(  # noqa: N802
            self, web, parameters, frame, handler
        ):
            panel = NSOpenPanel.openPanel()
            panel.setAllowsMultipleSelection_(bool(parameters.allowsMultipleSelection()))
            panel.setCanChooseDirectories_(bool(parameters.allowsDirectories()))
            handler(panel.URLs() if panel.runModal() == 1 else None)

    _ = NSEvent
    _classes.update(view=KoddiWebView, delegate=Delegate)
    return _classes


class _Environment:
    """Общие для всех видов окна: хранилище данных (localStorage, IndexedDB) и сессионная кука."""

    def __init__(self, host):
        from Foundation import NSUUID
        from WebKit import WKWebsiteDataStore

        self.host = host
        self.store = WKWebsiteDataStore.dataStoreForIdentifier_(NSUUID.alloc().initWithUUIDString_(str(STORE_ID).upper()))
        self.ready = host.session_key is None
        self.waiting: list = []
        if not self.ready:
            self._set_cookie()

    def _set_cookie(self) -> None:
        from Foundation import NSHTTPCookie

        base = QUrl(self.host.api_base)
        # без Secure: адрес локальный http; SameSite=Strict и HttpOnly - страница свою куку прочитать не может
        cookie = NSHTTPCookie.cookieWithProperties_({
            "Name": COOKIE, "Value": self.host.session_key, "Domain": base.host(), "Path": "/",
            "HttpOnly": "TRUE", "SameSite": "strict",
        })
        self.store.httpCookieStore().setCookie_completionHandler_(cookie, self._cookie_ready)

    def _cookie_ready(self) -> None:
        self.ready = True
        waiting, self.waiting = self.waiting, []
        for call in waiting:
            call()

    def when_ready(self, call) -> None:
        call() if self.ready else self.waiting.append(call)


def environment(host) -> _Environment:
    env = getattr(host, "_web_environment", None)
    if env is None:
        env = host._web_environment = _Environment(host)
    return env


class MacPage(QObject):
    """Вид страницы для кода, который работал с QWebEnginePage."""

    def __init__(self, view: "MacWebView"):
        super().__init__(view)
        self._view = view

    def runJavaScript(self, code: str, callback=None) -> None:  # noqa: N802
        self._view.run_javascript(code, callback)

    def setBackgroundColor(self, color: QColor) -> None:  # noqa: N802
        self._view.set_background(color)

    def requestedUrl(self) -> QUrl:  # noqa: N802
        return self._view.requested

    def triggerPageAction(self, action) -> None:  # noqa: N802
        self._view.triggerPageAction(action)

    def profile(self):
        return None


class MacWebView(QWidget):
    loadFinished = Signal(bool)  # noqa: N815
    urlChanged = Signal(QUrl)  # noqa: N815

    def __init__(self, host, parent=None, role: str = "main"):
        super().__init__(parent)
        self.host, self.role = host, role
        self.requested = QUrl()
        self.bridge = None
        self._download = None
        self._page = MacPage(self)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._create()

    # создание
    def _create(self) -> None:
        import os

        from AppKit import NSViewHeightSizable, NSViewWidthSizable
        from Foundation import NSMakeRect
        from WebKit import WKUserContentController, WKWebViewConfiguration

        classes = _objc_classes()
        self._delegate = classes["delegate"].alloc().initWithOwner_(self)
        env = environment(self.host)
        config = WKWebViewConfiguration.alloc().init()
        config.setWebsiteDataStore_(env.store)
        config.setApplicationNameForUserAgent_("KoddiDesktop/0.2")
        self._controller = WKUserContentController.alloc().init()
        self._controller.addScriptMessageHandler_name_(self._delegate, "koddi")
        config.setUserContentController_(self._controller)
        config.preferences().setValue_forKey_(False, "javaScriptCanOpenWindowsAutomatically")
        # звук голосового ввода и воспроизведение без отдельного жеста пользователя не нужны
        view = classes["view"].alloc().initWithFrame_configuration_(NSMakeRect(0, 0, 400, 300), config)
        view.last_mouse_down = None
        view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        view.setValue_forKey_(False, "drawsBackground")  # страница лежит над нативным стеклом
        view.setNavigationDelegate_(self._delegate)
        view.setUIDelegate_(self._delegate)
        if os.getenv("KODDI_INSPECT") and hasattr(view, "setInspectable_"):
            view.setInspectable_(True)
        self.web = view
        from koddi.desktop.macos_tiles import _nsview

        container = _nsview(self)
        view.setFrame_(container.bounds())
        container.addSubview_(view)
        self._env = env

    # совместимость с QWebEngineView
    def page(self) -> MacPage:
        return self._page

    def url(self) -> QUrl:
        return _qurl(self.web.URL())

    def load(self, url: QUrl) -> None:
        from Foundation import NSURL, NSURLRequest

        self.requested = QUrl(url)
        request = NSURLRequest.requestWithURL_(NSURL.URLWithString_(url.toString()))
        self._env.when_ready(lambda: self.web.loadRequest_(request))

    def setZoomFactor(self, factor: float) -> None:  # noqa: N802
        self.web.setPageZoom_(float(factor))

    def triggerPageAction(self, action) -> None:  # noqa: N802
        from AppKit import NSApplication

        name = EDIT_ACTIONS.get(str(getattr(action, "name", action)).lower())
        if name is not None:
            NSApplication.sharedApplication().sendAction_to_from_(name, self.web, None)

    def focusProxy(self):  # noqa: N802
        return self

    # страница
    def run_javascript(self, code: str, callback=None) -> None:
        def done(result, error):
            if callback is not None:
                callback(None if error is not None else _plain(result))

        self.web.evaluateJavaScript_completionHandler_(code, done)

    def set_background(self, color: QColor) -> None:
        from AppKit import NSColor

        self.web.setUnderPageBackgroundColor_(
            NSColor.colorWithSRGBRed_green_blue_alpha_(color.redF(), color.greenF(), color.blueF(), color.alphaF())
        )

    def _loaded(self, ok: bool) -> None:
        if ok:
            window = self.web.window()
            if window is not None and self.role == "main":
                window.makeFirstResponder_(self.web)
        self.loadFinished.emit(ok)

    # подключение скриптов и моста
    def install(self, bridge, start_source: str, end_source: str) -> None:
        """start_source выполняется при создании документа, end_source - когда документ готов (обе только в главном кадре)."""
        from WebKit import WKUserScript

        self.bridge = bridge
        bridge.frameChanged.connect(lambda state: self.run_javascript(f"window.__koddiSignal?.('frameChanged', {json.dumps(state)})"))
        for source, moment in ((start_source, 0), (end_source, 1)):  # WKUserScriptInjectionTimeAtDocumentStart / End
            script = WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(source, moment, True)
            self._controller.addUserScript_(script)

    def _message(self, message) -> None:
        """Сообщение моста от страницы: только главный кадр своей страницы и только известные методы."""
        try:
            frame = message.frameInfo()
            if not frame.isMainFrame() or not same_origin(_qurl(frame.request().URL()), QUrl(self.host.api_base)):
                return
            body = message.body()
            name, args, reply = str(body["m"]), [_plain(v) for v in body["a"]], int(body["id"])
            if name not in BRIDGE_METHODS or self.bridge is None or len(args) > 4:
                return
            if name == "adjustWidth":
                args = [str(args[0]), int(args[1]), int(args[2])]
            elif name == "requestState":
                args = []
            else:
                args = [str(args[0])]
            result = getattr(self.bridge, name)(*args)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Ошибка вызова моста окна")
            return
        if reply:
            self.run_javascript(f"window.__koddiReply?.({reply}, {json.dumps(result)})")

    # окно
    def start_window_drag(self) -> bool:
        """Перетаскивание окна за верхнюю полосу страницы; нужно нажатие левой кнопки, которое сейчас удерживается."""
        from AppKit import NSEvent

        event = getattr(self.web, "last_mouse_down", None)
        window = self.web.window()
        if event is None or window is None or not (NSEvent.pressedMouseButtons() & 1):
            return False
        window.performWindowDragWithEvent_(event)
        return True


def _plain(value):
    """Значения Foundation (NSString, NSNumber, NSArray) - в обычные Python-типы."""
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value if not isinstance(value, str) else str(value)
    if hasattr(value, "keys"):
        return {str(k): _plain(value[k]) for k in value.keys()}
    try:
        return [_plain(item) for item in value]
    except TypeError:
        return str(value)

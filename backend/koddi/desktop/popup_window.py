"""Окно меню: выпадающие списки и контекстные меню поверх приложения, а не внутри него.

Прозрачное окно без рамки с видом той же страницы (роль popup): меню не обрезается окном приложения, не лежит под
плитками стекла и не должно обходить поле ввода. Содержимое и размер присылает страница, положение считается здесь.
"""

from __future__ import annotations

import json
import logging
import sys

from PySide6.QtCore import QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QVBoxLayout, QWidget

LOGGER = logging.getLogger("koddi.desktop")
IS_MAC = sys.platform == "darwin"
# поле вокруг панели для её тени (POPUP_MARGIN на странице)
MARGIN = 24
GAP = 6
EDGE = 8
# при показе окно успевает мигнуть неактивным: фокус потерян, только если не вернулся за это время
BLUR_CHECK_MS = 60
MAX_SIZE = 4000


def place(anchor: tuple[float, float, float, float], size: tuple[int, int], side: str, align: str,
          screen: tuple[int, int, int, int]) -> tuple[int, int]:
    """Левый верхний угол панели: прямо над или под кнопкой, у точки - от точки, и всегда в пределах экрана."""
    ax, ay, aw, ah = anchor
    width, height = size
    sx, sy, sw, sh = screen
    x = ax + aw - width if align == "end" else ax
    below, above = ay + ah + (GAP if ah else 0), ay - height - (GAP if ah else 0)
    fits_below, fits_above = below + height <= sy + sh - EDGE, above >= sy + EDGE
    if side == "above":
        y = above if fits_above or not fits_below else below
    else:
        y = below if fits_below or not fits_above else above
    x = max(sx + EDGE, min(x, sx + sw - width - EDGE))
    y = max(sy + EDGE, min(y, sy + sh - height - EDGE))
    return round(x), round(y)


def parse_anchor(payload: str) -> dict | None:
    """Запрос страницы; она своя, но вид может оказаться на чужом адресе, поэтому только известные числа."""
    try:
        data = json.loads(payload)
        request = {"id": int(data["id"]), "side": str(data.get("side") or "below"), "align": str(data.get("align") or "start")}
        for name in ("x", "y", "w", "h", "vw", "vh"):
            value = float(data[name])
            if not -100000 < value < 100000:
                return None
            request[name] = value
    except (ValueError, KeyError, TypeError):
        return None
    return request if request["vw"] >= 1 and request["vh"] >= 1 else None


def parse_size(payload: str) -> dict | None:
    try:
        data = json.loads(payload)
        request = {"id": int(data["id"]), "w": int(data["w"]), "h": int(data["h"])}
    except (ValueError, KeyError, TypeError):
        return None
    return request if 0 < request["w"] <= MAX_SIZE and 0 < request["h"] <= MAX_SIZE else None


def anchor_on_screen(view, request: dict) -> tuple[float, float, float, float]:
    """Рамка якоря в координатах экрана. Вид плитки лежит внутри стекла: Qt не знает, где он на самом деле."""
    if IS_MAC:
        from AppKit import NSScreen

        web = view.web
        bounds = web.bounds()
        scale_x, scale_y = bounds.size.width / request["vw"], bounds.size.height / request["vh"]
        corner = web.window().convertPointToScreen_(web.convertPoint_toView_((request["x"] * scale_x, request["y"] * scale_y), None))
        return corner.x, NSScreen.screens()[0].frame().size.height - corner.y, request["w"] * scale_x, request["h"] * scale_y
    scale_x, scale_y = view.width() / request["vw"], view.height() / request["vh"]
    corner = view.mapToGlobal(QPoint(round(request["x"] * scale_x), round(request["y"] * scale_y)))
    return corner.x(), corner.y(), request["w"] * scale_x, request["h"] * scale_y


class PopupWindow(QWidget):
    def __init__(self, host):
        flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
        super().__init__(host, flags)
        self.host = host
        self.request: dict | None = None
        self.got_focus = False
        self._key_observer = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if IS_MAC:
            from koddi.desktop.native_web import MacWebView

            self.view = MacWebView(host, self, "popup")
        else:
            from PySide6.QtWebEngineWidgets import QWebEngineView

            from koddi.desktop.web_host import KoddiPage

            self.view = QWebEngineView(self)
            # профиль приложения: без его заголовка сессии локальный сервер не отдаёт страницу
            self.view.setPage(KoddiPage(host.web_profile, host))
        layout.addWidget(self.view)
        self.bridge, self.channel = host._install_page(self.view, "popup")
        self.view.page().setBackgroundColor(QColor(0, 0, 0, 0))
        self.view.load(QUrl(f"{host.api_base}/ui/?role=popup"))
        if not IS_MAC:
            QGuiApplication.instance().focusWindowChanged.connect(self._focus_changed)

    def _dress(self) -> None:
        """Окно поверх всего в приложении, в том числе в полноэкранном режиме; с приложением оно и прячется."""
        if not IS_MAC:
            return
        from AppKit import (
            NSColor,
            NSPopUpMenuWindowLevel,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorMoveToActiveSpace,
            NSWindowStyleMaskResizable,
        )

        window = self.view.web.window()
        if window is None:
            return
        # Qt оставляет окну без рамки возможность менять размер: за невидимый край тянулось меню
        window.setStyleMask_(window.styleMask() & ~NSWindowStyleMaskResizable)
        window.setOpaque_(False)
        window.setBackgroundColor_(NSColor.clearColor())
        window.setHasShadow_(False)
        window.setHidesOnDeactivate_(True)
        window.setLevel_(NSPopUpMenuWindowLevel)
        window.setCollectionBehavior_(NSWindowCollectionBehaviorFullScreenAuxiliary | NSWindowCollectionBehaviorMoveToActiveSpace)
        if self._key_observer is None:
            # Qt не замечает ухода клавиатуры: в окне приложения первым ответчиком становится WKWebView, а не вид Qt
            from AppKit import NSWindowDidBecomeKeyNotification, NSWindowDidResignKeyNotification
            from Foundation import NSNotificationCenter, NSOperationQueue

            center, queue = NSNotificationCenter.defaultCenter(), NSOperationQueue.mainQueue()
            self._key_observer = [
                center.addObserverForName_object_queue_usingBlock_(name, window, queue, lambda _note: self._focus_changed())
                for name in (NSWindowDidBecomeKeyNotification, NSWindowDidResignKeyNotification)
            ]

    def open_at(self, view, payload: str) -> None:
        request = parse_anchor(payload)
        if request is None:
            return
        request["anchor"] = anchor_on_screen(view, request)
        self.request = request

    def resize_to(self, payload: str) -> None:
        """Страница измерила панель: окно встаёт на место и показывается только теперь, без мигания."""
        size = parse_size(payload)
        request = self.request
        if size is None or request is None or size["id"] != request["id"]:
            return
        ax, ay, aw, ah = request["anchor"]
        screen = QGuiApplication.screenAt(QPoint(round(ax + aw / 2), round(ay + ah / 2))) or QGuiApplication.primaryScreen()
        box = screen.availableGeometry()
        x, y = place(request["anchor"], (size["w"], size["h"]), request["side"], request["align"], (box.x(), box.y(), box.width(), box.height()))
        # размер задаёт только панель: на Windows и в Qt окно тоже нельзя растянуть
        self.setFixedSize(size["w"] + MARGIN * 2, size["h"] + MARGIN * 2)
        self.move(x - MARGIN, y - MARGIN)
        if self.isVisible():
            return
        self.got_focus = False
        self.show()
        self._dress()
        self.raise_()
        self.activateWindow()
        if IS_MAC:
            self.view.web.window().makeKeyWindow()
            self.view.web.window().makeFirstResponder_(self.view.web)
            # окно могло стать ключевым ещё до наблюдателя: тогда уведомления не будет
            self.got_focus = self._has_keyboard()
        else:
            self.view.setFocus()
        self.view.page().runJavaScript("window.koddiPopupShown?.()")

    def close_request(self, payload: str) -> None:
        """Меню закрыла страница (выбран пункт, Esc): клавиатура возвращается в окно приложения."""
        try:
            wanted = int(json.loads(payload)["id"])
        except (ValueError, KeyError, TypeError):
            return
        if self.request is None or self.request["id"] != wanted:
            return
        self.request = None
        if self.isVisible():
            self.hide()
            self.host.activateWindow()

    def _has_keyboard(self) -> bool:
        if IS_MAC:
            return bool(self.view.web.window().isKeyWindow())
        return QGuiApplication.focusWindow() is not None and QGuiApplication.focusWindow() == self.windowHandle()

    def _focus_changed(self, _window=None) -> None:
        if not self.isVisible():
            return
        if self._has_keyboard():
            self.got_focus = True
        elif self.got_focus:
            QTimer.singleShot(BLUR_CHECK_MS, self._check_blur)

    def _check_blur(self) -> None:
        """Щелчок мимо меню: в другое окно, по окну приложения или в другую программу."""
        if not self.isVisible() or self._has_keyboard():
            return
        self.hide()
        self.view.page().runJavaScript("window.koddiPopupBlur?.()")


def create(host) -> PopupWindow | None:
    """Без окна меню остаются на странице: приложение работает дальше."""
    try:
        return PopupWindow(host)
    except Exception:  # noqa: BLE001
        LOGGER.exception("Окно меню не создано")
        return None

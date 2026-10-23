from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
from ctypes import wintypes  # импортируется и на macOS: структуры нужны только Windows-коду и его тестам
from pathlib import Path

import uvicorn
from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QByteArray,
    QEasingCurve,
    QEvent,
    QFile,
    QIODevice,
    QObject,
    QPointF,
    QRect,
    QTimer,
    QUrl,
    QUrlQuery,
    QVariantAnimation,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QDesktopServices, QImage, QMouseEvent
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEnginePermission,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMainWindow,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from koddi.desktop import software_rendering
from koddi.desktop import server as desktop_server


LOGGER = logging.getLogger("koddi.desktop")
IS_MAC = sys.platform == "darwin"


class UrlHandler(QObject):
    """На macOS ссылка koddi:// открывает уже запущенную программу, а не второй процесс: Qt зовёт этот обработчик."""

    activation_requested = Signal()

    @Slot(QUrl)
    def handle(self, _url: QUrl) -> None:
        self.activation_requested.emit()


class DesktopApiServer(uvicorn.Server):
    def __init__(self, config):
        super().__init__(config)
        self.stop_requested = threading.Event()
        self._main_loop_entered = threading.Event()

    def request_shutdown(self):
        self.stop_requested.set()
        if self._main_loop_entered.is_set():
            self.should_exit = True

    async def main_loop(self):
        # uvicorn 0.30 пропускает lifespan.shutdown при should_exit во время startup.
        self._main_loop_entered.set()
        if self.stop_requested.is_set():
            self.should_exit = True
        await super().main_loop()


class DesktopBackend:
    def __init__(self, port: int):
        from koddi.desktop.transport import desktop_access

        desktop_access.enable()
        desktop_access.origin = f"http://127.0.0.1:{port}"
        self.server = DesktopApiServer(uvicorn.Config(
            desktop_server.app, host="127.0.0.1", port=port,
            log_level="warning", access_log=False, lifespan="on",
            # открытый SSE не должен бесконечно откладывать остановку очереди и runs.
            timeout_graceful_shutdown=5,
        ))
        self.failed = threading.Event()
        self.thread = threading.Thread(target=self._run, name="koddi-api", daemon=False)

    def _run(self):
        try:
            desktop_server.migrate_legacy_data()
            desktop_server.configure_logging()
            desktop_server.init_db()
            self.server.run()
        except BaseException:
            # пути, настройки и текст исключения не нужны в сообщении о сбое запуска.
            LOGGER.error("Локальный сервер завершился с ошибкой")
            self.failed.set()
        finally:
            if not self.server.stop_requested.is_set():
                self.failed.set()

    def start(self):
        self.thread.start()

    def request_shutdown(self):
        self.server.request_shutdown()

    def stop(self):
        self.request_shutdown()
        if self.thread.ident is None:
            return
        next_warning = time.monotonic() + 15
        while self.thread.is_alive():
            try:
                self.thread.join(timeout=0.2)
            except KeyboardInterrupt:
                # повторное прерывание не должно оборвать executor с активной записью.
                self.request_shutdown()
            if self.thread.is_alive() and time.monotonic() >= next_warning:
                LOGGER.warning("Ожидаем безопасного завершения локальных операций")
                next_warning = time.monotonic() + 15


class WindowsFrame:
    """Системное поведение окна; caption-глифы остаются резервной HTML-отрисовкой."""

    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.dwmapi = ctypes.WinDLL("dwmapi")
        pointer = ctypes.c_ssize_t
        suffix = "PtrW" if ctypes.sizeof(ctypes.c_void_p) == 8 else "W"
        self.get_style = getattr(self.user32, "GetWindowLong" + suffix)
        self.get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        self.get_style.restype = pointer
        self.set_style = getattr(self.user32, "SetWindowLong" + suffix)
        self.set_style.argtypes = [wintypes.HWND, ctypes.c_int, pointer]
        self.set_style.restype = pointer
        self.user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        self.user32.SetWindowPos.restype = wintypes.BOOL
        self.user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, ctypes.c_size_t, pointer,
        ]
        self.user32.DefWindowProcW.restype = pointer
        self.user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user32.GetClientRect.restype = wintypes.BOOL
        self.user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.user32.ClientToScreen.restype = wintypes.BOOL
        self.user32.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
        self.user32.IsChild.restype = wintypes.BOOL
        self.user32.TrackMouseEvent.argtypes = [ctypes.c_void_p]
        self.user32.TrackMouseEvent.restype = wintypes.BOOL
        self.user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, pointer]
        self.user32.PostMessageW.restype = wintypes.BOOL
        self.user32.IsZoomed.argtypes = [wintypes.HWND]
        self.user32.IsZoomed.restype = wintypes.BOOL
        self.user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        self.user32.GetDpiForWindow.restype = wintypes.UINT
        self.user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wintypes.UINT]
        self.user32.GetSystemMetricsForDpi.restype = ctypes.c_int
        self.dwmapi.DwmDefWindowProc.argtypes = [
            wintypes.HWND, wintypes.UINT, ctypes.c_size_t, pointer, ctypes.POINTER(pointer),
        ]
        self.dwmapi.DwmDefWindowProc.restype = wintypes.BOOL
        self.dwmapi.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ]
        self.dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long

    def deflate_maximized(self, hwnd, params_address):
        """Развёрнутое окно Windows выдвигает за экран на толщину рамки: возвращаем клиентскую область на место."""
        dpi = self.user32.GetDpiForWindow(hwnd) or 96
        border = (self.user32.GetSystemMetricsForDpi(32, dpi) + self.user32.GetSystemMetricsForDpi(92, dpi))
        rect = wintypes.RECT.from_address(params_address)
        rect.left += border
        rect.top += border
        rect.right -= border
        rect.bottom -= border

    SYSCOMMANDS = {"minimize": 0xF020, "maximize": 0xF030, "restore": 0xF120, "close": 0xF060}

    def system_command(self, hwnd, action):
        """Системная команда окна: сворачивание, разворачивание и закрытие идут штатным путём Windows, с анимацией."""
        return bool(self.user32.PostMessageW(hwnd, 0x0112, self.SYSCOMMANDS[action], 0))  # WM_SYSCOMMAND

    @staticmethod
    def caption_hit_test(x, y, width, scale, maximized=False):
        edge = 0 if maximized else 6 * scale
        if not (edge <= y < 42 * scale and width - 138 * scale <= x < width - edge):
            return None
        return (8, 9, 20)[min(2, int((x - width + 138 * scale) / (46 * scale)))]

    def hit_test(self, host, lparam):
        if (not host._caption_ready or not host.isVisible()
                or host.isFullScreen() or host.isMinimized()):
            return None
        hwnd = int(host.winId())
        origin, rect = wintypes.POINT(), wintypes.RECT()
        if not self.user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return None
        if not self.user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        # знаковые GET_X/Y_LPARAM нужны для мониторов левее и выше основного.
        x = ctypes.c_short(lparam & 0xFFFF).value - origin.x
        y = ctypes.c_short((lparam >> 16) & 0xFFFF).value - origin.y
        return self.caption_hit_test(
            x, y, rect.right, host.devicePixelRatioF() * host.view.zoomFactor(),
            host.isMaximized(),
        )

    def track_leave(self, hwnd):
        class TrackMouse(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("hwndTrack", wintypes.HWND), ("dwHoverTime", wintypes.DWORD),
            ]

        track = TrackMouse(ctypes.sizeof(TrackMouse), 0x12, hwnd, 0)  # LEAVE | NONCLIENT
        self.user32.TrackMouseEvent(ctypes.byref(track))


class CaptionChildFilter(QAbstractNativeEventFilter):
    def __init__(self, host):
        super().__init__()
        self.host = host

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if event_type != b"windows_generic_MSG":
            return False, 0
        msg = wintypes.MSG.from_address(int(message))
        if msg.message != 0x84:
            return False, 0
        frame = self.host._windows_frame
        hwnd = int(self.host.winId())
        if (msg.hWnd != hwnd
                and frame.user32.IsChild(hwnd, msg.hWnd)
                and frame.hit_test(self.host, msg.lParam) is not None):
            # дочерний HWND WebEngine должен пропускать caption-события владельцу окна.
            return True, -1  # HTTRANSPARENT
        return False, 0


class DesktopRequestInterceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self, host: "WebHost"):
        super().__init__(host)
        self.base = QUrl(host.api_base)
        self.key = host.session_key

    def interceptRequest(self, info):  # noqa: N802
        url, first = info.requestUrl(), info.firstPartyUrl()

        def origin(item):
            return item.scheme(), item.host(), item.port()

        if (
            self.key
            and origin(url) == origin(self.base)
            and (origin(first) == origin(self.base) or first.isEmpty())
        ):
            info.setHttpHeader(
                QByteArray(b"X-Koddi-Desktop-Session"), QByteArray(self.key.encode("ascii"))
            )


def _page_size(data: dict):
    """Размер страницы (vw, vh), при котором она измерила рамки блоков; без него стекло берёт размер окна."""
    try:
        size = (float(data["vw"]), float(data["vh"]))
    except (KeyError, TypeError, ValueError):
        return None
    return size if 1 <= size[0] <= 20000 and 1 <= size[1] <= 20000 else None


class WindowBridge(QObject):
    frameChanged = Signal(dict)

    def __init__(self, host: "WebHost", view=None, role: str = "main"):
        super().__init__(host)
        self.host = host
        self.view = view if view is not None else host.view
        self.role = role

    def _own_page(self) -> bool:
        base, current = QUrl(self.host.api_base), self.view.url()
        return (current.scheme(), current.host(), current.port()) == (base.scheme(), base.host(), base.port())

    @Slot(str)
    def relay(self, payload: str):
        """Сообщение одного вида приложения другим видам (зеркала состояния и вызовы действий)."""
        if self._own_page() and len(payload) <= 4 * 1024 * 1024:
            self.host.relay(self.role, payload)

    @Slot(str)
    def regions(self, payload: str):
        """Рамки блоков, под которыми плитка кладёт своё стекло (карточки правой панели)."""
        if self.role == "main" or not self._own_page() or len(payload) > 64 * 1024:
            return
        try:
            data = json.loads(payload)
        except ValueError:
            return
        if isinstance(data, dict):
            self.host.apply_tile_regions(self.role, data.get("regions", []), _page_size(data))

    @Slot(str)
    def appearance(self, payload: str):
        """Тема и цвет фона страницы: запоминаются для заставки следующего запуска (на всех системах)."""
        if self.role != "main" or not self._own_page() or len(payload) > 256:
            return
        try:
            data = json.loads(payload)
            theme, color = data["theme"], [float(v) for v in data["color"]]
        except (ValueError, KeyError, TypeError):
            return
        from koddi.desktop import splash

        glass = self.host.glass
        splash.save(theme, bool(glass is not None and glass.enabled), color)

    @Slot(str)
    def overlay(self, payload: str):
        """Главная страница открыла или закрыла модальное окно: плитки стекла уходят под страницу и возвращаются."""
        if self.role == "main" and self._own_page() and len(payload) <= 256:
            try:
                data = json.loads(payload)
            except ValueError:
                return
            if isinstance(data, dict):
                self.host.apply_overlay(bool(data.get("active")))

    @Slot(str)
    def panels(self, payload: str):
        """Главная страница сообщает, какие панели открыты и как их двигать: плитки стекла двигает AppKit."""
        if self.role == "main" and self._own_page() and len(payload) <= 4096:
            try:
                data = json.loads(payload)
            except ValueError:
                return
            if isinstance(data, dict):
                self.host.apply_panels(data)

    @Slot(str)
    def focusComposer(self, payload: str):  # noqa: N802
        """Главная страница просит вернуть клавиатуру в поле ввода: оно живёт в своём виде над лентой."""
        if self.role == "main" and self._own_page():
            self.host.focus_composer()

    @Slot(str, result=bool)
    def copyText(self, text: str):  # noqa: N802
        base, current = QUrl(self.host.api_base), self.view.url()
        if (
            (current.scheme(), current.host(), current.port())
            != (base.scheme(), base.host(), base.port())
            or not current.path().startswith("/ui/")
            or current.userInfo()
            or len(text) > 8 * 1024 * 1024
        ):
            return False
        try:
            clipboard = QApplication.clipboard()
            if clipboard is None:
                return False
            clipboard.setText(text)
            # ownsClipboard() на macOS всегда False: копия проходила, а страница показывала ошибку
            return clipboard.ownsClipboard() or clipboard.text() == text
        except Exception:
            return False

    @Slot(str, result=bool)
    def copyImage(self, data: str):  # noqa: N802
        """Картинка (PNG в base64) попадает в буфер обмена как изображение."""
        base, current = QUrl(self.host.api_base), self.view.url()
        if (
            (current.scheme(), current.host(), current.port())
            != (base.scheme(), base.host(), base.port())
            or not current.path().startswith("/ui/")
            or current.userInfo()
            or len(data) > 48 * 1024 * 1024
        ):
            return False
        try:
            image = QImage.fromData(base64.b64decode(data, validate=True))
            clipboard = QApplication.clipboard()
            if image.isNull() or clipboard is None:
                return False
            clipboard.setImage(image)
            return True
        except Exception:
            return False

    @Slot(str, result=bool)
    def pageAction(self, name: str):  # noqa: N802
        """Вырезать, копировать, вставить и выделить всё для своего контекстного меню."""
        actions = {
            "cut": QWebEnginePage.WebAction.Cut,
            "copy": QWebEnginePage.WebAction.Copy,
            "paste": QWebEnginePage.WebAction.Paste,
            "selectAll": QWebEnginePage.WebAction.SelectAll,
        }
        base, current = QUrl(self.host.api_base), self.view.url()
        if (
            name not in actions
            or (current.scheme(), current.host(), current.port())
            != (base.scheme(), base.host(), base.port())
            or not current.path().startswith("/ui/")
            or current.userInfo()
        ):
            return False
        self.view.triggerPageAction(actions[name])
        return True

    @Slot()
    def requestState(self):  # noqa: N802
        base, current = QUrl(self.host.api_base), self.view.url()
        if (current.scheme(), current.host(), current.port()) == (
            base.scheme(), base.host(), base.port(),
        ):
            self.host._caption_ready = True
            self.frameChanged.emit(self.host._window_frame_state())

    @Slot(str)
    def setGlass(self, payload: str):  # noqa: N802
        """Страница включает Liquid Glass и присылает прямоугольники блоков; только своя страница."""
        base, current = QUrl(self.host.api_base), self.view.url()
        if (current.scheme(), current.host(), current.port()) != (base.scheme(), base.host(), base.port()):
            return
        if self.role != "main" or len(payload) > 64 * 1024:
            return
        try:
            data = json.loads(payload)
            if isinstance(data, dict):
                self.host.apply_glass(bool(data.get("enabled")), str(data.get("theme", "dark")), data.get("color"), data.get("regions", []), _page_size(data))
        except (ValueError, TypeError):
            pass

    @Slot(str, int, int, result=str)
    def adjustWidth(self, side: str, delta: int, duration: int):  # noqa: N802
        base, current = QUrl(self.host.api_base), self.view.url()
        if (current.scheme(), current.host(), current.port()) != (
            base.scheme(),
            base.host(),
            base.port(),
        ):
            return "{}"
        return json.dumps(self.host.adjust_width(side, int(delta), int(duration)))

    @Slot(str)
    def command(self, action: str):
        base, current = QUrl(self.host.api_base), self.view.url()
        if (current.scheme(), current.host(), current.port()) != (
            base.scheme(),
            base.host(),
            base.port(),
        ):
            return
        if action in {"minimize", "maximize", "close"}:
            self.host.window_action(action)
        elif action == "move" and hasattr(self.view, "start_window_drag"):
            self.view.start_window_drag()
        elif action == "move" and QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
            handle = self.host.windowHandle()
            if handle:
                handle.startSystemMove()


class KoddiPage(QWebEnginePage):
    def __init__(self, profile: QWebEngineProfile, host: "WebHost") -> None:
        super().__init__(profile, host)
        self.host = host
        self.permissionRequested.connect(self._permission)

    def _permission(self, permission) -> None:
        """Микрофон нужен только голосовому вводу собственной страницы приложения; остальное по умолчанию запрещено."""
        origin = permission.origin()
        base = QUrl(self.host.api_base)
        own = (origin.scheme(), origin.host(), origin.port()) == (base.scheme(), base.host(), base.port())
        if own and permission.permissionType() == QWebEnginePermission.PermissionType.MediaAudioCapture:
            permission.grant()
        else:
            permission.deny()

    def acceptNavigationRequest(self, url: QUrl, navigation_type, is_main_frame: bool) -> bool:  # type: ignore[override]
        base = QUrl(self.host.api_base)
        if url.scheme() == "koddi":
            self.host.handle_app_url(url, self.url())
            return False
        internal = (url.scheme(), url.host(), url.port()) == (
            base.scheme(),
            base.host(),
            base.port(),
        )
        if is_main_frame and not internal:
            if (
                navigation_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
                and url.scheme() in {"http", "https"}
            ):
                QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, navigation_type, is_main_frame)


class ResizeHandle(QWidget):
    def __init__(self, host: "WebHost", edges: Qt.Edges, cursor: Qt.CursorShape) -> None:
        super().__init__(host)
        self.host = host
        self.edges = edges
        self.setCursor(cursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and not self.host.isMaximized():
            handle = self.host.windowHandle()
            if handle is not None and handle.startSystemResize(self.edges):
                event.accept()
                return
        super().mousePressEvent(event)


class WebHost(QMainWindow):
    project_opened = Signal(dict)
    project_failed = Signal(str)

    def __init__(self, api_port: int, session_key: str | None = None, backend: DesktopBackend | None = None) -> None:
        super().__init__()
        self.backend = backend
        self._initial_show_done = False
        self.api_base = f"http://127.0.0.1:{api_port}"
        self.session_key = session_key
        self.project_opened.connect(self._project_opened)
        self.project_failed.connect(self._project_failed)
        self.setWindowTitle("Докки")
        from koddi.desktop import macos_glass

        # Liquid Glass только на macOS 26+; окно сразу прозрачное, а обычный вид рисует страница сама
        self.glass = macos_glass.GlassLayer(self) if IS_MAC and macos_glass.supported() else None
        self.tiles: dict = {}
        self._last_regions = None
        self._final_width = None
        if self.glass:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._caption_ready = False
        self._caption_hover = ""
        self._caption_pressed = 0
        self._windows_frame = (
            WindowsFrame() if sys.platform == "win32"
            and QApplication.platformName() == "windows" else None
        )
        if IS_MAC:
            # системная рамка и кнопки остаются; заголовок делает прозрачным macos_frame, а страница
            # занимает всё окно. Флаги Qt ExpandedClientAreaHint добавляют отступ под заголовок, поэтому не нужны.
            self.setWindowFlags(Qt.WindowType.Window)
            # иначе Qt отступает от верха на высоту заголовка, и страница не заходит под него
            self.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
        else:
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        if self._windows_frame:
            self._caption_filter = CaptionChildFilter(self)
            QApplication.instance().installNativeEventFilter(self._caption_filter)
        self.setMinimumSize(960, 640)
        self._load_attempts = 0
        self._ui_loaded = False

        root = QWidget(self)
        root.setObjectName("windowRoot")
        root.setAutoFillBackground(True)
        root_palette = root.palette()
        root_palette.setColor(root.backgroundRole(), QColor("#030303"))
        root.setPalette(root_palette)
        root.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if IS_MAC:
            from koddi.desktop.native_web import MacWebView

            self.view = MacWebView(self, root, "main")
            self.web_profile = None
            self.window_bridge, self.channel = self._install_page(self.view, "main")
        else:
            self._create_engine_view(root)
        self.view.loadFinished.connect(self._on_load_finished)
        self.view.urlChanged.connect(self._reset_caption)
        from koddi.desktop import splash

        self._startup_look = splash.load()
        self.startup_label = splash.Splash(root, self._startup_look)
        layout.addWidget(self.view, 1)
        self.setCentralWidget(root)
        self._fit_startup_overlay()
        # на macOS размер меняет система
        self._resize_handles = {} if IS_MAC else {
            "left": ResizeHandle(self, Qt.Edge.LeftEdge, Qt.CursorShape.SizeHorCursor),
            "right": ResizeHandle(self, Qt.Edge.RightEdge, Qt.CursorShape.SizeHorCursor),
            "top": ResizeHandle(self, Qt.Edge.TopEdge, Qt.CursorShape.SizeVerCursor),
            "bottom": ResizeHandle(self, Qt.Edge.BottomEdge, Qt.CursorShape.SizeVerCursor),
            "top_left": ResizeHandle(
                self, Qt.Edge.TopEdge | Qt.Edge.LeftEdge, Qt.CursorShape.SizeFDiagCursor
            ),
            "top_right": ResizeHandle(
                self, Qt.Edge.TopEdge | Qt.Edge.RightEdge, Qt.CursorShape.SizeBDiagCursor
            ),
            "bottom_left": ResizeHandle(
                self, Qt.Edge.BottomEdge | Qt.Edge.LeftEdge, Qt.CursorShape.SizeBDiagCursor
            ),
            "bottom_right": ResizeHandle(
                self, Qt.Edge.BottomEdge | Qt.Edge.RightEdge, Qt.CursorShape.SizeFDiagCursor
            ),
        }

        self.setStyleSheet("#windowRoot { background: #101010; }")
        if IS_MAC:
            from koddi.desktop import macos_menu

            macos_menu.install(self)
        QTimer.singleShot(0, self._enable_native_frame)
        QTimer.singleShot(250, self._load_ui)

    def _create_engine_view(self, root) -> None:
        self.view = QWebEngineView(root)
        self.view.setStyleSheet("background: #080808;")
        if self.glass:
            self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # именованный профиль хранит localStorage и IndexedDB между запусками: режим, раскрытые проекты, кэш чатов
        from koddi.config import data_dir

        storage = data_dir() / "webengine"
        storage.mkdir(parents=True, exist_ok=True)
        self.web_profile = QWebEngineProfile("DokkyApp", self)
        self.web_profile.setPersistentStoragePath(str(storage / "storage"))
        self.web_profile.setCachePath(str(storage / "cache"))
        self.web_profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        self.view.setPage(KoddiPage(self.web_profile, self))
        profile = self.view.page().profile()
        self.interceptor = DesktopRequestInterceptor(self)
        profile.setUrlRequestInterceptor(self.interceptor)
        if "KoddiDesktop/" not in profile.httpUserAgent():
            agent = profile.httpUserAgent() + " KoddiDesktop/0.2"
            if software_rendering():
                agent += " KoddiRender/software"
            profile.setHttpUserAgent(agent)
        self.view.page().profile().downloadRequested.connect(self._save_download)
        self.window_bridge, self.channel = self._install_page(self.view, "main")
        self.view.page().setBackgroundColor(QColor("#080808"))

    def _chrome_source(self, channel_code: str, role: str) -> str:
        """Скрипт страницы: связь с окном и оформление; channel_code - реализация QWebChannel."""
        from koddi.desktop import splash

        # выбор стекла из startup.json: вызывается ещё из __init__, до self._startup_look
        glass_preferred = bool(splash.load()["glass"])
        return (
            channel_code
            + "\n"
            + """
          if(location.origin === %s) {
            // один WebChannel сохраняет обработчики оконных команд и записи буфера
            QWebChannel = class extends QWebChannel {
              constructor(transport, ready, converters) {
                super(transport, channel => {
                  window.koddiClipboardWrite = (text, event) => {
                    if (!(event instanceof MouseEvent) || !event.isTrusted || event.type !== 'click')
                      return Promise.resolve(false);
                    return new Promise(resolve => {
                      const timer = setTimeout(() => resolve(false), 2500);
                      channel.objects.koddiWindow.copyText(text, copied => {
                        clearTimeout(timer); resolve(copied === true);
                      });
                    });
                  };
                  window.koddiCopyImage = (data, event) => {
                    if (!(event instanceof MouseEvent) || !event.isTrusted || event.type !== 'click')
                      return Promise.resolve(false);
                    return new Promise(resolve => {
                      const timer = setTimeout(() => resolve(false), 5000);
                      channel.objects.koddiWindow.copyImage(data, copied => {
                        clearTimeout(timer); resolve(copied === true);
                      });
                    });
                  };
                  window.koddiPageAction = (name, event) => {
                    if (!(event instanceof MouseEvent) || !event.isTrusted || event.type !== 'click')
                      return Promise.resolve(false);
                    return new Promise(resolve => {
                      const timer = setTimeout(() => resolve(false), 2500);
                      channel.objects.koddiWindow.pageAction(name, done => {
                        clearTimeout(timer); resolve(done === true);
                      });
                    });
                  };
                  ready(channel);
                }, converters);
              }
            };
            document.documentElement.classList.add('desktop-window');
            %s
            const css=document.createElement('link');css.rel='stylesheet';css.href='/ui/desktop-window.css';document.head.append(css);
            const js=document.createElement('script');js.src='/ui/desktop-window.js';document.head.append(js);
          }
        """
            % (json.dumps(self.api_base), ("document.documentElement.classList.add('platform-mac', %s);" % json.dumps("role-" + role) + (" document.documentElement.classList.add('glass-capable'); window.koddiTiles = true; window.koddiGlassPreferred = %s;" % json.dumps(glass_preferred) if self.glass else "")) if IS_MAC else "document.documentElement.classList.add(%s);" % json.dumps(("platform-win " if sys.platform == "win32" else "") + "role-" + role).replace(" ", '", "'))
        )

    def _install_page(self, view, role: str):
        """Общая настройка страницы вида: мост окна, WebChannel и внедряемые скрипты; role - main, sidebar или inspector."""
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        if IS_MAC:
            from koddi.desktop.native_web import SHIM

            bridge = WindowBridge(self, view, role)
            view.install(bridge, "window.koddiDesktop = true;", self._chrome_source(SHIM, role))
            return bridge, None
        page = view.page()
        # без анимации колесо мыши прыгает по 100 px, как в старых браузерах
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
        channel = QWebChannel(page)
        bridge = WindowBridge(self, view, role)
        channel.registerObject("koddiWindow", bridge)
        page.setWebChannel(channel)
        desktop_bridge = QWebEngineScript()
        desktop_bridge.setName("koddi-desktop")
        desktop_bridge.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        desktop_bridge.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        desktop_bridge.setSourceCode("window.koddiDesktop = true;")
        page.scripts().insert(desktop_bridge)
        chrome = QWebEngineScript()
        chrome.setName("koddi-window-controls")
        chrome.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        chrome.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        channel_source = QFile(":/qtwebchannel/qwebchannel.js")
        if not channel_source.open(QIODevice.OpenModeFlag.ReadOnly):
            raise RuntimeError("Не удалось загрузить связь с окном")
        channel_code = bytes(channel_source.readAll()).decode("utf-8")
        channel_source.close()
        chrome.setSourceCode(self._chrome_source(channel_code, role))
        page.scripts().insert(chrome)
        return bridge, channel

    def handle_app_url(self, url: QUrl, current: QUrl) -> None:
        """Команды страницы через адрес koddi://...; принимаются только от своей страницы."""
        base = QUrl(self.api_base)
        if (current.scheme(), current.host(), current.port()) != (base.scheme(), base.host(), base.port()):
            return
        if url.host() == "open-folder":
            self.open_project_folder()
        elif url.host() == "product":
            mode = QUrlQuery(url).queryItemValue("mode")
            if mode in {"chat", "search", "development"}:
                self.setWindowTitle("Докки Code" if mode == "development" else "Докки")
        elif url.host() == "open-url":
            target = QUrl(QUrlQuery(url).queryItemValue("url", QUrl.ComponentFormattingOption.FullyDecoded))
            if (
                target.scheme() in {"http", "https"}
                and target.host()
                and not target.userName()
                and len(target.toString()) <= 4096
            ):
                QDesktopServices.openUrl(target)

    def decide_navigation(self, view, url: QUrl, is_main_frame: bool, link_clicked: bool) -> bool:
        """Правила переходов нативного вида: внутри приложения можно, наружу - только по ссылке и в системном браузере."""
        base = QUrl(self.api_base)
        if url.scheme() == "koddi":
            self.handle_app_url(url, view.url())
            return False
        internal = (url.scheme(), url.host(), url.port()) == (base.scheme(), base.host(), base.port())
        if is_main_frame and not internal:
            if link_clicked and url.scheme() in {"http", "https"}:
                QDesktopServices.openUrl(url)
            return False
        return True

    def _save_download(self, download) -> None:
        url = download.url()
        if url.scheme() == "blob":
            url = QUrl(url.toString()[5:])
        base = QUrl(self.api_base)
        if (url.scheme(), url.host(), url.port()) != (base.scheme(), base.host(), base.port()):
            download.cancel()
            return
        name = Path(download.downloadFileName()).name or "file"
        destination, _ = QFileDialog.getSaveFileName(
            self, "Сохранить файл", str(Path.home() / "Downloads" / name)
        )
        if not destination:
            download.cancel()
            return
        download.setDownloadDirectory(str(Path(destination).parent))
        download.setDownloadFileName(Path(destination).name)
        download.accept()

    def glass_content_view(self):
        """Корневой NSView окна: сюда кладутся плитки стекла."""
        return self.glass._objc(self)

    def _ensure_tiles(self) -> None:
        """Нативные плитки боковых панелей создаются один раз, когда запущен сервер и окно показано."""
        if self.tiles or self.glass is None or not self._initial_show_done:
            return
        from koddi.desktop.macos_tiles import ComposerTile, PanelTile

        for role, side in (("sidebar", "left"), ("inspector", "right")):
            tile = PanelTile(self, role, side)
            tile.attach()
            self.tiles[role] = tile
        self.tiles["composer"] = ComposerTile(self)
        # страница могла прислать рамку поля раньше, чем появился его вид
        if self._last_regions is not None:
            self._apply_composer(*self._last_regions)

    def relay(self, from_role: str, payload: str) -> None:
        """Пересылка сообщения остальным видам: каждый вид слушает window.koddiRelayIn."""
        views = {"main": self.view, **{role: tile.view for role, tile in self.tiles.items()}}
        literal = json.dumps(payload)
        for role, view in views.items():
            if role != from_role:
                view.page().runJavaScript(f"window.koddiRelayIn && window.koddiRelayIn({literal})")

    def live_resize(self, active: bool) -> None:
        """Начало и конец ресайза мышью: страницы переходят в лёгкий режим (без размытия, масок и переходов)."""
        if os.getenv("KODDI_NO_RESIZE_MODE"):
            return
        code = f"document.documentElement.classList.toggle('resizing', {'true' if active else 'false'})"
        for view in [self.view, *[tile.view for tile in self.tiles.values()]]:
            view.page().runJavaScript(code)

    def apply_tile_regions(self, role: str, regions, size=None) -> None:
        from koddi.desktop import macos_glass

        tile = self.tiles.get(role)
        if tile is not None and self.glass is not None and self.glass.enabled:
            tile.set_regions(macos_glass.clean_regions(regions), size)

    def refresh_glass(self) -> None:
        """Подобранный вид стекла (окно «Настройка стекла») применяется вживую."""
        if self.glass is None:
            return
        self.glass.refresh()
        for tile in self.tiles.values():
            tile.dress(self.glass.theme)
            tile.apply_radius()

    def open_glass_tuner(self) -> None:
        if self.glass is None:
            return
        from koddi.desktop.glass_tuner import GlassTuner

        if getattr(self, "_glass_tuner", None) is None:
            self._glass_tuner = GlassTuner(self)
        self._glass_tuner.show()
        self._glass_tuner.raise_()
        self._glass_tuner.activateWindow()

    def apply_overlay(self, active: bool) -> None:
        for tile in self.tiles.values():
            tile.set_overlay(active)

    def apply_panels(self, data: dict) -> None:
        """Главная страница сообщает состояние панелей; плитки двигает AppKit с тем же временем и кривой, что у страницы."""
        def number(name, low, high, default):
            try:
                value = float(data.get(name, default))
            except (TypeError, ValueError):
                return default
            return min(high, max(low, value)) if value == value else default

        ms = number("ms", 0, 2000, 0)
        curve = data.get("c")
        if not (isinstance(curve, list) and len(curve) == 4 and all(isinstance(v, (int, float)) and -2 <= v <= 3 for v in curve)):
            curve = None
        animate = bool(data.get("animate")) and ms > 0
        sidebar, inspector = self.tiles.get("sidebar"), self.tiles.get("inspector")
        if sidebar is not None:
            sidebar.layout(animate=animate, ms=ms, curve=curve, open_=bool(data.get("left")), width=number("lw", 120, 640, sidebar.width))
        if inspector is not None:
            inspector.layout(animate=animate, ms=ms, curve=curve, open_=bool(data.get("right")), width=number("rw", 120, 800, inspector.width))

    def apply_glass(self, enabled: bool, theme: str, color=None, regions=None, size=None) -> None:
        from koddi.desktop import macos_glass

        glass = self.glass
        if glass is None:
            return
        rgb = macos_glass.clean_color(color)
        if enabled != glass.enabled or theme != glass.theme or (rgb is not None and rgb != glass.color):
            glass.configure(enabled, theme, rgb)
            for tile in self.tiles.values():
                tile.dress(theme)
                tile.layout(animate=False)
            color = QColor(0, 0, 0, 0) if enabled else QColor("#080808")
            self.view.page().setBackgroundColor(color)
            self.view.setStyleSheet("background: transparent;" if enabled else "background: #080808;")
            # заливка окна Qt лежала бы поверх нативного стекла и закрывала его
            self.centralWidget().setAutoFillBackground(not enabled)
            self.setStyleSheet("#windowRoot { background: %s; }" % ("transparent" if enabled else "#101010"))
        cleaned = macos_glass.clean_regions(regions) if enabled else []
        self._last_regions = (cleaned, size)
        self._apply_composer(cleaned, size)
        glass.set_regions([item for item in cleaned if item["id"] != "composer"], size)
        from koddi.desktop import splash

        splash.save(glass.theme, enabled, glass.color)

    def _apply_composer(self, regions: list[dict], size=None) -> None:
        """Рамка поля ввода идёт его плитке: стекло над лентой; нет плитки - блок остаётся без стекла."""
        tile = self.tiles.get("composer")
        if tile is not None:
            tile.set_regions([item for item in regions if item["id"] == "composer"], size)

    def focus_composer(self) -> None:
        """Клавиатура переходит в поле ввода: в его собственный вид, если он стоит над лентой, иначе в главный."""
        tile = self.tiles.get("composer")
        shown = tile is not None and any(not view.isHidden() for view in tile.regions.views.values())
        (tile.view if shown else self.view).web.window().makeFirstResponder_((tile.view if shown else self.view).web)

    def menu_action(self, action: str) -> None:
        """Команда строки меню macOS выполняется страницей; чужой адрес команд не получает."""
        base, current = QUrl(self.api_base), self.view.url()
        if (current.scheme(), current.host(), current.port()) != (base.scheme(), base.host(), base.port()):
            return
        from koddi.desktop import macos_menu

        self.view.page().runJavaScript(macos_menu.dispatch_script(action))

    def toggle_fullscreen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def _enable_native_frame(self) -> None:
        if not self._initial_show_done:
            return
        if self._windows_frame is None:
            if IS_MAC:
                from koddi.desktop import macos_frame

                macos_frame.prepare(self)
                self.apply_lights()
            return
        hwnd = int(self.winId())
        frame = self._windows_frame
        dwmapi = frame.dwmapi
        style = frame.get_style(hwnd, -16)
        # WS_CAPTION нужен Windows для анимации сворачивания, разворачивания и закрытия; рамку и заголовок убирает WM_NCCALCSIZE
        wanted = style | 0x00C00000 | 0x00040000 | 0x00020000 | 0x00010000 | 0x00080000
        if style != wanted:
            ctypes.set_last_error(0)
            if not frame.set_style(hwnd, -16, wanted) and ctypes.get_last_error():
                return
            frame.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037)
        # эти атрибуты документированы только начиная с Windows 11.
        if sys.getwindowsversion().build < 22000:
            return
        dark_mode = ctypes.c_int(1)
        rounded = ctypes.c_int(2)  # DWMWCP_ROUND
        border_color = ctypes.c_uint(0x00181818)
        caption_color = ctypes.c_uint(0x00030303)
        text_color = ctypes.c_uint(0x00F0F0F0)
        dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark_mode), ctypes.sizeof(dark_mode))
        dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(rounded), ctypes.sizeof(rounded))
        dwmapi.DwmSetWindowAttribute(
            hwnd, 34, ctypes.byref(border_color), ctypes.sizeof(border_color)
        )
        dwmapi.DwmSetWindowAttribute(
            hwnd, 35, ctypes.byref(caption_color), ctypes.sizeof(caption_color)
        )
        dwmapi.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text_color), ctypes.sizeof(text_color))
        self._sync_window_frame()

    def nativeEvent(self, event_type, message):  # noqa: N802
        if event_type == b"windows_generic_MSG":
            power = wintypes.MSG.from_address(int(message))
            if power.message == 0x0218:  # WM_POWERBROADCAST: доступ с телефона сообщает о сне и пробуждении
                from koddi.remote_control import remote_control

                if power.wParam == 0x0004:  # PBT_APMSUSPEND
                    remote_control.sleep_notice()
                elif power.wParam in {0x0007, 0x0012}:  # PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC
                    remote_control.wake_notice()
        frame = getattr(self, "_windows_frame", None)
        if frame is None or event_type != b"windows_generic_MSG":
            return super().nativeEvent(event_type, message)
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == 0x0083 and msg.wParam:  # WM_NCCALCSIZE: клиентская область занимает всё окно
            # состояние Qt обновляется позже сообщения, поэтому спрашиваем у самой Windows
            if frame.user32.IsZoomed(msg.hWnd):
                frame.deflate_maximized(msg.hWnd, msg.lParam)
            return True, 0
        result = ctypes.c_ssize_t()
        dwm_handled = frame.dwmapi.DwmDefWindowProc(
            msg.hWnd, msg.message, msg.wParam, msg.lParam, ctypes.byref(result)
        )
        if msg.message == 0x02A2:  # WM_NCMOUSELEAVE
            self._caption_hover = ""
            self._sync_window_frame()
        if dwm_handled:
            return True, result.value
        if msg.message == 0x0084:  # WM_NCHITTEST / HTMAXBUTTON включает Snap hover на Windows 11
            hit = frame.hit_test(self, msg.lParam)
            if hit is not None:
                return True, hit
        elif msg.message == 0x00A0:  # WM_NCMOUSEMOVE
            hover = {8: "minimize", 9: "maximize", 20: "close"}.get(msg.wParam, "")
            if self._caption_hover != hover:
                self._caption_hover = hover
                self._sync_window_frame()
            frame.track_leave(msg.hWnd)
        if msg.message in {0x00A1, 0x00A2, 0x00A3} and msg.wParam in {8, 9, 20}:
            # окно без системного заголовка: стандартный обработчик не выполняет сворачивание
            # и разворачивание, поэтому команду запускаем сами, сразу при отпускании кнопки.
            if msg.message == 0x00A1:
                self._caption_pressed = msg.wParam
            elif msg.message == 0x00A2:
                pressed, self._caption_pressed = self._caption_pressed, 0
                if pressed == msg.wParam:
                    QTimer.singleShot(0, lambda action=pressed: self.run_caption_action(action))
            return True, 0
        if msg.message == 0x00A0 and msg.wParam in {8, 9, 20}:
            return True, frame.user32.DefWindowProcW(
                msg.hWnd, msg.message, msg.wParam, msg.lParam
            )
        if msg.message in {0x0006, 0x02E0, 0x031E}:  # ACTIVATE, DPICHANGED, DWMCOMPOSITIONCHANGED
            QTimer.singleShot(0, self._enable_native_frame)
        return super().nativeEvent(event_type, message)

    def run_caption_action(self, code: int) -> None:
        action = {8: "minimize", 9: "maximize", 20: "close"}.get(code)
        if action:
            self.window_action(action)

    def window_action(self, action: str) -> None:
        """Через WM_SYSCOMMAND окно сворачивается, разворачивается и закрывается нативно (с анимацией Windows)."""
        frame = self._windows_frame
        if frame is not None and self.isVisible():
            command = "restore" if action == "maximize" and (self.isMaximized() or self.isMinimized()) else action
            if frame.system_command(int(self.winId()), command):
                return
        if action == "minimize":
            self.showMinimized()
        elif action == "maximize":
            self.toggle_maximized()
        else:
            self.close()

    def _window_frame_state(self):
        return {
            "mode": "macos-native" if IS_MAC else "windows-caption-glyph",
            "maximized": self.isMaximized(),
            "hover": self._caption_hover,
        }

    def _reset_caption(self, _url):
        self._caption_ready = False
        self._caption_hover = ""

    def _sync_window_frame(self):
        if hasattr(self, "window_bridge"):
            self.window_bridge.frameChanged.emit(self._window_frame_state())

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._enable_native_frame)

    def show(self) -> None:
        super().show()
        # Cocoa может обработать zero-timer внутри show_sys; менять styleMask там нельзя
        self._initial_show_done = True
        self._enable_native_frame()
        if self.glass is not None and self._startup_look["glass"]:
            # вид окна как в прошлый раз: скругление, стекло и подложка готовы до загрузки страницы
            look = self._startup_look
            self.apply_glass(True, look["theme"], tuple(look["color"]))

    def _load_ui(self) -> None:
        if not self._initial_show_done:
            QTimer.singleShot(100, self._load_ui)
            return
        if self.backend is not None and not self.backend.server.started:
            if self.backend.failed.is_set():
                self.startup_label.setText("Не удалось запустить локальный сервер. Перезапустите Докки.")
                return
            QTimer.singleShot(100, self._load_ui)
            return
        self._load_attempts += 1
        try:
            self._ensure_tiles()
        except Exception:  # noqa: BLE001
            # без плиток окно остаётся рабочим: страница загрузится целиком в главном виде
            LOGGER.exception("Не удалось создать боковые плитки стекла")
        self.view.load(QUrl(f"{self.api_base}/ui/"))

    def _on_load_finished(self, ok: bool) -> None:
        if ok:
            self._ui_loaded = True
            self.startup_label.stop()
            self.apply_lights(self.view)
            if IS_MAC and os.getenv("KODDI_OPEN_GLASS_TUNER"):
                QTimer.singleShot(1500, self.open_glass_tuner)
            return
        requested = self.view.page().requestedUrl()
        if (
            not self._ui_loaded
            and self._load_attempts < 30
            and requested.toString().startswith(f"{self.api_base}/ui/")
        ):
            QTimer.singleShot(300, self._retry_initial_load)

    def _retry_initial_load(self) -> None:
        if not self._ui_loaded:
            self._load_ui()

    def _geometry_file(self) -> Path:
        from koddi.config import data_dir

        return data_dir() / "window.json"

    def restore_geometry(self) -> None:
        """Окно открывается там, где его оставили; в первый раз - крупным и по центру экрана."""
        screen = self.screen() or QApplication.primaryScreen()
        area = screen.availableGeometry()
        width = min(area.width(), max(1500, int(area.width() * 0.8)))
        height = min(area.height(), max(900, int(area.height() * 0.85)))
        target = QRect(area.x() + (area.width() - width) // 2, area.y() + (area.height() - height) // 2, width, height)
        maximized = False
        try:
            saved = json.loads(self._geometry_file().read_text(encoding="utf-8"))
            rect = QRect(int(saved["x"]), int(saved["y"]), int(saved["width"]), int(saved["height"]))
            visible = rect.intersected(area)
            if visible.width() >= 400 and visible.height() >= 300 and rect.width() >= self.minimumWidth() and rect.height() >= self.minimumHeight():
                target, maximized = rect, bool(saved.get("maximized"))
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.setGeometry(target)
        if maximized:
            self.setWindowState(Qt.WindowState.WindowMaximized)

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            rect = self.normalGeometry() if self.isMaximized() or self.isMinimized() else self.geometry()
            self._geometry_file().write_text(
                json.dumps({
                    "x": rect.x(), "y": rect.y(), "width": rect.width(), "height": rect.height(),
                    "maximized": self.isMaximized(),
                }),
                encoding="utf-8",
            )
        except OSError:
            pass
        if IS_MAC:
            from koddi.desktop import macos_frame

            macos_frame.detach(self)
            macos_frame.hide_dock_icon()
        super().closeEvent(event)

    def toggle_maximized(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def adjust_width(self, side: str, delta: int, duration: int = 0) -> dict:
        """Меняет ширину окна у выбранного края: панель раскрывается за счёт окна, а не чата.

        Возвращает, сколько пикселей реально добавлено или убрано слева и справа.
        При duration > 0 окно растёт плавно с той же кривой, что и панель в интерфейсе.
        """
        applied = {"left": 0, "right": 0}
        if (
            side not in applied
            or delta == 0
            or abs(delta) > 1200
            or self.isMaximized()
            or self.isFullScreen()
            or self.isMinimized()
        ):
            return applied
        screen = self.screen()
        if screen is None:
            return applied
        area = screen.availableGeometry()
        geometry = self.geometry()
        left, right = geometry.left(), geometry.right() + 1
        if delta > 0:
            # сначала нужный край, остаток уходит на противоположный, если там есть место
            order = [side, "right" if side == "left" else "left"]
            remaining = delta
            for edge in order:
                room = max(0, left - area.left()) if edge == "left" else max(0, area.right() + 1 - right)
                step = min(remaining, room)
                if edge == "left":
                    left -= step
                else:
                    right += step
                applied[edge] += step
                remaining -= step
        else:
            shrink = min(-delta, max(0, geometry.width() - self.minimumWidth()))
            if side == "left":
                left += shrink
            else:
                right -= shrink
            applied[side] -= shrink
        if left != geometry.left() or right != geometry.right() + 1:
            self._move_geometry(
                QRect(left, geometry.top(), right - left, geometry.height()), geometry, duration
            )
        return applied

    def _move_geometry(self, target: QRect, start: QRect, duration: int) -> None:
        animation = getattr(self, "_grow_animation", None)
        if animation is not None:
            animation.stop()
            self._grow_animation = None
        if duration <= 0:
            self.setGeometry(target)
            self._final_width = None
            return
        # итоговая ширина нужна нативным плиткам: правая плитка встаёт по ней, а не по ширине в начале движения
        self._final_width = target.width()
        animation = QVariantAnimation(self)
        animation.setDuration(min(duration, 800))
        curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
        curve.addCubicBezierSegment(QPointF(0.22, 1.0), QPointF(0.36, 1.0), QPointF(1.0, 1.0))
        animation.setEasingCurve(curve)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)

        def step(progress: float) -> None:
            self.setGeometry(
                start.x() + round((target.x() - start.x()) * progress),
                start.y(),
                start.width() + round((target.width() - start.width()) * progress),
                start.height(),
            )

        animation.valueChanged.connect(step)
        def done() -> None:
            self.setGeometry(target)
            self._final_width = None
            self._relayout_tiles()

        animation.finished.connect(done)
        self._grow_animation = animation
        animation.start()

    def _relayout_tiles(self) -> None:
        for tile in self.tiles.values():
            tile.on_window_resize()
        # стекло под блоками страницы едет вместе с окном, а не ждёт замера страницы
        if self.glass is not None:
            self.glass.on_resize()

    def _fit_startup_overlay(self) -> None:
        label = getattr(self, "startup_label", None)
        if label is not None and not self._ui_loaded:
            label.setGeometry(self.centralWidget().rect())
            label.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_startup_overlay()
        if not hasattr(self, "_resize_handles"):
            return
        if IS_MAC:
            self._sync_window_frame()
            self._relayout_tiles()
            return
        # боковые зоны узкие, чтобы не перекрывать полосу прокрутки ленты у правого края
        margin = 6
        side = 3
        corner = 16
        width, height = self.width(), self.height()
        geometries = {
            "left": (0, corner, side, max(0, height - 2 * corner)),
            "right": (width - side, corner, side, max(0, height - 2 * corner)),
            "top": (corner, 0, max(0, width - 2 * corner), margin),
            "bottom": (corner, height - margin, max(0, width - 2 * corner), margin),
            "top_left": (0, 0, corner, corner),
            "top_right": (width - corner, 0, corner, corner),
            "bottom_left": (0, height - corner, corner, corner),
            "bottom_right": (width - corner, height - corner, corner, corner),
        }
        for name, handle in self._resize_handles.items():
            handle.setGeometry(*geometries[name])
            handle.setVisible(not self.isMaximized())
            handle.raise_()
        self._sync_window_frame()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            if hasattr(self, "view"):
                self.view.page().runJavaScript(
                    f"window.koddiWindowState?.({str(self.isMaximized()).lower()})"
                )
            if hasattr(self, "_resize_handles"):
                for handle in self._resize_handles.values():
                    handle.setVisible(not self.isMaximized())
            self._sync_window_frame()
            if IS_MAC:
                # Qt восстанавливает styleMask после fullscreen; возвращаем full-size content следом
                self.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
                QTimer.singleShot(0, self._enable_native_frame)

    def apply_lights(self, view=None) -> None:
        """Реальные координаты системного светофора - в CSS-переменные страниц: верхняя строка выравнивается по ним."""
        if not IS_MAC or not self._initial_show_done:
            return
        from koddi.desktop import macos_frame

        geometry = macos_frame.lights_geometry(self)
        if geometry is None:
            return
        code = "(() => { const s = document.documentElement.style; %s })()" % " ".join(
            f"s.setProperty('--lights-{name}', '{geometry[name]:.2f}px');" for name in ("center", "left", "right")
        )
        views = [view] if view is not None else [self.view, *[tile.view for tile in self.tiles.values()]]
        for item in views:
            item.page().runJavaScript(code)

    def open_project_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку проекта")
        if not folder:
            return

        def register():
            payload = json.dumps({"path": folder}, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                f"{self.api_base}/api/projects",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    **({"X-Koddi-Desktop-Session": self.session_key} if self.session_key else {}),
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=8) as response:
                    project = json.loads(response.read().decode("utf-8"))
                self.project_opened.emit(project)
            except Exception as exc:
                self.project_failed.emit(str(exc))

        threading.Thread(target=register, name="koddi-project-open", daemon=True).start()

    @Slot(dict)
    def _project_opened(self, project: dict) -> None:
        self.view.page().runJavaScript(
            f"window.koddiProjectOpened({json.dumps(project, ensure_ascii=False)})"
        )

    @Slot(str)
    def _project_failed(self, error: str) -> None:
        self.view.page().runJavaScript(f"window.koddiProjectOpenFailed({json.dumps(error)})")


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _api_port() -> int:
    """Порт локального сервера остаётся прежним между запусками: localStorage и IndexedDB привязаны к адресу страницы,
    а с новым портом при каждом запуске режим, раскрытые проекты и кэш чатов терялись бы."""
    configured = os.getenv("KODDI_PORT")
    if configured:
        return int(configured)
    from koddi.config import data_dir

    remembered = data_dir() / "ui-port"
    try:
        saved = int(remembered.read_text(encoding="utf-8").strip())
        if 1024 <= saved <= 65535 and _port_free(saved):
            return saved
    except (OSError, ValueError):
        pass
    port = _available_port()
    try:
        remembered.write_text(str(port), encoding="utf-8")
    except OSError:
        pass
    return port


def main() -> None:
    from koddi.desktop.session import activation_server, register_protocol
    from koddi.config import data_dir

    if IS_MAC:
        from koddi.config import app_dir
        from koddi.desktop import macos_menu

        macos_menu.set_process_name()
    app = QApplication(sys.argv)
    app.setApplicationName("Докки")
    if IS_MAC:
        macos_menu.install_qt_translations([app_dir() / "qt_translations"])
    url_handler = UrlHandler()
    if IS_MAC:
        QDesktopServices.setUrlHandler("koddi", url_handler, "handle")
    broker = activation_server()
    if broker is None:
        return
    try:
        register_protocol()
    except OSError:
        pass
    api_port = _api_port()
    from koddi.desktop.transport import desktop_access

    session_key = desktop_access.enable()
    backend = None
    timer = QTimer(app)

    def request_shutdown():
        timer.stop()
        if backend is not None:
            backend.request_shutdown()

    app.aboutToQuit.connect(request_shutdown)
    try:
        backend = DesktopBackend(api_port)
        backend.start()
        host = WebHost(api_port, session_key, backend)
        host.restore_geometry()
        host.show()

        def activate():
            host.showNormal()
            host.raise_()
            host.activateWindow()

        def incoming():
            connection = broker.nextPendingConnection()
            if connection:
                connection.readyRead.connect(lambda: (activate(), connection.disconnectFromServer()))
                if connection.bytesAvailable():
                    activate()
                    connection.disconnectFromServer()
                connection.disconnected.connect(connection.deleteLater)

        broker.newConnection.connect(incoming)
        url_handler.activation_requested.connect(activate)
        marker = data_dir() / "dokky-login.signal"
        last_login = marker.stat().st_mtime_ns if marker.exists() else 0

        def check_backend_and_login():
            nonlocal last_login
            if backend.failed.is_set():
                app.exit(1)
                return
            current = marker.stat().st_mtime_ns if marker.exists() else 0
            if current != last_login:
                last_login = current
                activate()

        timer.timeout.connect(check_backend_and_login)
        timer.start(1000)
        exit_code = app.exec()
    finally:
        request_shutdown()
        try:
            if backend is not None:
                backend.stop()
        finally:
            # второй экземпляр не должен открыть тот же профиль до завершения записей.
            broker.close()
    raise SystemExit(exit_code or int(backend.failed.is_set()))


if __name__ == "__main__":
    main()

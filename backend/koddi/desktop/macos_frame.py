"""macOS: окно с прозрачным заголовком и нативным светофором поверх страницы.

Высоту заголовка и положение кнопок задаёт настоящий NSToolbar (стиль unified): AppKit сам держит их при ресайзах, полном экране
и смене оформления, вручную их не двигаем. Окну задан радиус скругления 32 pt.
Страница получает реальные координаты светофора (lights_geometry) и выравнивает по ним свою верхнюю строку.
"""

from __future__ import annotations

import ctypes

FULL_SIZE_CONTENT = 1 << 15
FULL_SCREEN = 1 << 14
TITLE_HIDDEN = 1
TOOLBAR_STYLE_UNIFIED = 3
TOOLBAR_ID = "koddi-main"


def _window(widget):
    import objc

    view = objc.objc_object(c_void_p=ctypes.c_void_p(int(widget.winId())))
    return view.window()


def prepare(widget) -> bool:
    """Страница занимает всё окно, заголовок прозрачный и без названия; пустой NSToolbar задаёт высоту строки светофора."""
    window = _window(widget)
    if window is None:
        return False
    # во время перехода в полный экран и в нём самом панель не нужна: иначе под светофором остаётся сплошная полоса
    if widget.isFullScreen() or window.styleMask() & FULL_SCREEN or getattr(widget, "_mac_fullscreen", False):
        return False
    if not window.titlebarAppearsTransparent():
        window.setTitlebarAppearsTransparent_(True)
    if window.titleVisibility() != TITLE_HIDDEN:
        window.setTitleVisibility_(TITLE_HIDDEN)
    if not window.styleMask() & FULL_SIZE_CONTENT:
        window.setStyleMask_(window.styleMask() | FULL_SIZE_CONTENT)
    if window.toolbar() is None:
        from AppKit import NSToolbar

        toolbar = NSToolbar.alloc().initWithIdentifier_(TOOLBAR_ID)
        toolbar.setAllowsUserCustomization_(False)
        window.setToolbar_(toolbar)
    if window.toolbarStyle() != TOOLBAR_STYLE_UNIFIED:
        window.setToolbarStyle_(TOOLBAR_STYLE_UNIFIED)
    keep_look(widget, window)
    _watch_live_resize(widget, window)
    _watch_fullscreen(widget, window)
    return True


def _ours(notification, widget) -> bool:
    """Уведомления ловим без привязки к объекту и отбираем своё окно по текущему окну виджета: Qt может пересоздать нативное окно."""
    try:
        current = _window(widget)
        return bool(notification.object() is not None and current is not None and notification.object().isEqual_(current))
    except Exception:  # noqa: BLE001
        return False


def _watch_fullscreen(widget, window) -> None:
    """В полном экране система рисовала бы под светофором сплошную полосу панели: панель снимаем и ставим заново при выходе."""
    if getattr(widget, "_mac_fullscreen_observers", None):
        return
    from AppKit import (
        NSNotificationCenter,
        NSWindowDidExitFullScreenNotification,
        NSWindowWillEnterFullScreenNotification,
    )

    def entering(_n):
        if not _ours(_n, widget):
            return
        widget._mac_fullscreen = True
        current = _window(widget)
        if current is not None:
            current.setToolbar_(None)

    def exited(_n):
        if not _ours(_n, widget):
            return
        # панель создаётся заново: prepare видит, что её нет
        widget._mac_fullscreen = False
        prepare(widget)
        widget.apply_lights()

    center = NSNotificationCenter.defaultCenter()
    widget._mac_fullscreen_observers = [
        center.addObserverForName_object_queue_usingBlock_(NSWindowWillEnterFullScreenNotification, None, None, entering),
        center.addObserverForName_object_queue_usingBlock_(NSWindowDidExitFullScreenNotification, None, None, exited),
    ]


def _watch_live_resize(widget, window) -> None:
    """Пока окно тянут мышью, страницам нужен лёгкий режим: хост сообщает им о начале и конце ресайза."""
    if getattr(widget, "_mac_live_observers", None):
        return
    from AppKit import NSNotificationCenter, NSWindowDidEndLiveResizeNotification, NSWindowWillStartLiveResizeNotification

    center = NSNotificationCenter.defaultCenter()
    widget._mac_live_observers = [
        center.addObserverForName_object_queue_usingBlock_(NSWindowWillStartLiveResizeNotification, None, None, lambda n: widget.live_resize(True) if _ours(n, widget) else None),
        center.addObserverForName_object_queue_usingBlock_(NSWindowDidEndLiveResizeNotification, None, None, lambda n: widget.live_resize(False) if _ours(n, widget) else None),
    ]


def keep_look(widget, window=None) -> None:
    """Радиус скругления окна. Положение светофора не трогаем: его держит NSToolbar, любая ручная правка AppKit перекладывает обратно."""
    window = window or _window(widget)
    if window is None or window.styleMask() & FULL_SCREEN:
        return
    try:
        from koddi.desktop import macos_glass

        radius = float(macos_glass.SETTINGS["radius"])
        if abs(float(window._cornerRadius()) - radius) > 0.01:
            window._setCornerRadius_(radius)
            window.invalidateShadow()
    except Exception:  # noqa: BLE001 - закрытый метод
        pass


def anchor_in(content, view):
    """Прямой подвид content, внутри которого лежит view: относительно него ставятся слои под страницей и над ней."""
    node = view
    while node is not None:
        parent = node.superview()
        if parent is None:
            return view
        if parent.isEqual_(content):
            return node
        node = parent
    return view


def lights_geometry(widget) -> dict | None:
    """Где системный светофор: расстояния от верхнего левого угла окна в точках и системный радиус скругления окна."""
    from AppKit import NSWindowCloseButton, NSWindowZoomButton

    window = _window(widget)
    if window is None or window.styleMask() & FULL_SCREEN:
        return None
    close, zoom = window.standardWindowButton_(NSWindowCloseButton), window.standardWindowButton_(NSWindowZoomButton)
    if close is None or zoom is None:
        return None
    first = close.convertRect_toView_(close.bounds(), None)
    last = zoom.convertRect_toView_(zoom.bounds(), None)
    height = float(window.frame().size.height)
    center = height - (first.origin.y + first.size.height / 2)
    try:
        radius = float(window._cornerRadius())
    except Exception:  # noqa: BLE001 - закрытый метод
        radius = 26.0
    return {
        "left": float(first.origin.x),
        "right": float(last.origin.x + last.size.width),
        "center": float(center),
        "radius": radius,
    }


def detach(widget) -> None:
    from AppKit import NSNotificationCenter

    for observer in (getattr(widget, "_mac_live_observers", None) or []) + (getattr(widget, "_mac_fullscreen_observers", None) or []):
        NSNotificationCenter.defaultCenter().removeObserver_(observer)
    widget._mac_live_observers = None
    widget._mac_fullscreen_observers = None


def hide_dock_icon() -> None:
    """Закрытие окна завершает программу, но остановка локального сервера занимает время: значок убираем сразу."""
    from AppKit import NSApplication

    NSApplication.sharedApplication().setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory

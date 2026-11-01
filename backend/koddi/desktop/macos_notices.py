"""Системные уведомления macOS (UserNotifications) и число на значке в Dock.

Центр уведомлений работает только у собранного .app с идентификатором бандла: у процесса Python без бандла
он падает с исключением ObjC, поэтому вне бандла остаются значок в Dock и подпрыгивание (QApplication.alert)."""

from __future__ import annotations

import logging
from collections.abc import Callable

import objc

from koddi.desktop.notices import Notice

LOG = logging.getLogger(__name__)
_classes: dict = {}


def bundled() -> bool:
    try:
        from Foundation import NSBundle

        bundle = NSBundle.mainBundle()
        return bool(bundle.bundleIdentifier()) and str(bundle.bundlePath()).endswith(".app")
    except Exception:  # noqa: BLE001 - без бандла уведомлений просто нет
        return False


def set_badge(count: int) -> None:
    """Число непрочитанных уведомлений на значке в Dock; 0 убирает его."""
    try:
        from AppKit import NSApp

        NSApp.dockTile().setBadgeLabel_(str(count) if count > 0 else None)
    except Exception:  # noqa: BLE001
        LOG.debug("dock badge failed", exc_info=True)


def _delegate_class():
    """Класс ObjC создаётся один раз: определить его повторно нельзя."""
    if _classes:
        return _classes["delegate"]
    import UserNotifications as UN
    from Foundation import NSObject
    from PyObjCTools import AppHelper

    protocol = objc.protocolNamed("UNUserNotificationCenterDelegate")

    class NoticeDelegate(NSObject, protocols=[protocol]):
        def initWithOwner_(self, owner):  # noqa: N802
            self = objc.super(NoticeDelegate, self).init()
            if self is None:
                return None
            self.owner = owner
            return self

        # окно в фоне, но программа активна (другое окно Докки или меню): баннер всё равно нужен
        def userNotificationCenter_willPresentNotification_withCompletionHandler_(self, _center, _notification, handler):  # noqa: N802
            handler(UN.UNNotificationPresentationOptionBanner | UN.UNNotificationPresentationOptionList | UN.UNNotificationPresentationOptionSound)

        def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(self, _center, response, handler):  # noqa: N802
            try:
                info = response.notification().request().content().userInfo()
                chat_id = int(info["chat_id"]) if info is not None and "chat_id" in info else 0
            except (TypeError, ValueError, KeyError):
                chat_id = 0
            # ответ приходит не обязательно в главном потоке, а окно Qt трогаем только из него
            if chat_id > 0:
                AppHelper.callAfter(self.owner.open_chat, chat_id)
            handler()

    _classes["delegate"] = NoticeDelegate
    return NoticeDelegate


class MacNotices:
    def __init__(self, open_chat: Callable[[int], None]) -> None:
        import UserNotifications as UN

        self.UN = UN
        self.open_chat = open_chat
        self.center = UN.UNUserNotificationCenter.currentNotificationCenter()
        self.delegate = _delegate_class().alloc().initWithOwner_(self)
        self.center.setDelegate_(self.delegate)
        self.allowed: bool | None = None

    def post(self, notice: Notice) -> None:
        """Разрешение спрашивается при первом уведомлении, а не при запуске: так понятно, зачем оно."""
        if self.allowed is False:
            return
        if self.allowed is None:
            options = self.UN.UNAuthorizationOptionAlert | self.UN.UNAuthorizationOptionSound | self.UN.UNAuthorizationOptionBadge

            def answered(granted, error):
                self.allowed = bool(granted)
                if granted:
                    self._add(notice)
                elif error is not None:
                    LOG.info("notifications not allowed: %s", error)

            self.center.requestAuthorizationWithOptions_completionHandler_(options, answered)
            return
        self._add(notice)

    def _add(self, notice: Notice) -> None:
        UN = self.UN
        content = UN.UNMutableNotificationContent.alloc().init()
        content.setTitle_(notice.title)
        content.setBody_(notice.body)
        content.setUserInfo_({"chat_id": notice.chat_id})
        # звук - только когда агент ждёт человека: конец работы не повод звенеть
        if notice.kind == "question":
            content.setSound_(UN.UNNotificationSound.defaultSound())
        # одно уведомление на чат: новое заменяет прежнее, а не копится стопкой
        request = UN.UNNotificationRequest.requestWithIdentifier_content_trigger_(f"chat-{notice.chat_id}", content, None)
        self.center.addNotificationRequest_withCompletionHandler_(request, None)

    def clear(self) -> None:
        self.center.removeAllDeliveredNotifications()


def install(open_chat: Callable[[int], None]) -> MacNotices | None:
    if not bundled():
        return None
    try:
        return MacNotices(open_chat)
    except Exception:  # noqa: BLE001 - без уведомлений окно продолжает работать
        LOG.warning("notification center unavailable", exc_info=True)
        return None

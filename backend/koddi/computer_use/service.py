from __future__ import annotations

import base64
import io
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from koddi.config import app_dir, is_packaged_build
from koddi.policy.account import local_owner, require_chat
from koddi.storage.db import db, get_setting, set_setting


_device_lock = threading.Lock()
_revoked = threading.Event()
NATIVE = Path(__file__).with_name("native")
# действие -> (обязательные поля, необязательные)
ACTIONS = {
    "launch": ({"application"}, {"arguments", "working_directory"}),
    "list_windows": (set(), {"query"}),
    "observe": ({"pid"}, {"window_id"}),
    "focus": ({"pid", "snapshot_id"}, set()),
    "screenshot": ({"pid", "snapshot_id", "window_id"}, set()),
    "press": ({"pid", "snapshot_id", "element_id"}, set()),
    "click_element": ({"pid", "snapshot_id", "element_id"}, {"button", "clicks"}),
    "focus_window": ({"pid", "snapshot_id", "element_id"}, set()),
    "type_element": ({"pid", "snapshot_id", "element_id", "text"}, set()),
    "type": ({"pid", "snapshot_id", "text"}, set()),
    "hotkey": ({"pid", "snapshot_id", "keys"}, set()),
    "click": ({"pid", "snapshot_id", "x", "y"}, {"button", "clicks"}),
    "move": ({"pid", "snapshot_id", "x", "y"}, set()),
    "scroll": ({"pid", "snapshot_id", "x", "y", "delta_y"}, set()),
}
# коды помощника -> подсказка модели: что случилось и что делать дальше
ERROR_HINTS = {
    "needs_elevation": "Приложение запущено с правами администратора, а Докки - без них. Попросите пользователя нажать «Перезапустить от администратора» в Настройки - Система - Управление приложениями.",
    "protected_process": "Это защищённый системный процесс, управление им недоступно.",
    "launch_requires_elevation": "Программа требует прав администратора. Попросите пользователя перезапустить Докки от администратора (Настройки - Система - Управление приложениями) и повторите запуск.",
    "application_exited": "Программа завершилась или открыла окно в уже запущенном процессе. Вызовите list_windows и возьмите pid нужного окна.",
    "application_unavailable": "У процесса нет главного окна. Вызовите list_windows и найдите окно по заголовку.",
    "stale_application": "Приложение перезапустилось. Вызовите observe заново.",
    "stale_element": "Интерфейс изменился. Вызовите observe и возьмите свежий element_id.",
    "stale_window": "Окно закрылось или заменилось. Вызовите observe или list_windows.",
    "target_not_foreground": "Окно не удалось вынести вперёд (Windows не отдала фокус). Вызовите focus и повторите, либо используйте press/type_element: им фокус не нужен.",
    "focus_failed": "Windows не отдала фокус окну. Закройте перекрывающие окна или повторите focus.",
    "protected_input": "Поле защищено (пароль): вводить в него нельзя, попросите пользователя ввести значение самому.",
    "position_outside_target": "Точка вне окна выбранного приложения: другое окно перекрывает его или координаты неверны. Сделайте screenshot и пересчитайте.",
    "capture_unavailable": "Окно не отдаёт изображение (оно свёрнуто или защищено от захвата).",
    "input_blocked": "Windows заблокировала ввод: окно с повышенными правами или защищённый рабочий стол (UAC).",
}


def consent_enabled() -> bool:
    return get_setting("computer_use:" + local_owner(), True) is True


def set_consent(enabled: bool) -> None:
    if type(enabled) is not bool:
        raise ValueError("Ожидалось логическое значение")
    # уже начатый вызов не оживает, если пользователь сразу включил управление снова
    _revoked.set()
    set_setting("computer_use:" + local_owner(), enabled)


def native_command() -> list[str]:
    if sys.platform == "darwin":
        if tuple(int(part) for part in platform.mac_ver()[0].split(".")[:2]) < (14, 0):
            raise ValueError("Computer Use требует macOS 14 или новее")
        helper = (
            app_dir() / "koddi" / "computer_use" / "native" / "dokky-computer-helper"
            if is_packaged_build()
            else NATIVE / "dokky-computer-helper"
        )
        if not helper.is_file() or helper.is_symlink():
            raise ValueError(
                "Сначала соберите нативный помощник: python scripts/build_computer_helper.py"
            )
        return [str(helper)]
    if sys.platform == "win32":
        script = (
            app_dir() / "koddi" / "computer_use" / "native" / "windows.ps1"
            if is_packaged_build()
            else NATIVE / "windows.ps1"
        )
        if not script.is_file() or script.is_symlink():
            raise ValueError("Нативный помощник Windows не найден")
        shell = (
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        # execution policy не отключается; запрос передаётся данными, без интерполяции в скрипт
        return [str(shell), "-NoProfile", "-NonInteractive", "-File", str(script)]
    raise ValueError("Computer Use пока поддерживает macOS и Windows")


def invoke_native(
    request: dict, cancel_event: threading.Event, *, owner: str | None = None
) -> dict:
    # ASCII-escape: кодировка stdin у PowerShell не должна влиять на кириллицу
    encoded = json.dumps(request).encode("ascii")
    if len(encoded) > 20000:
        raise ValueError("Слишком большой запрос управления")
    command = native_command()
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=output, stderr=errors)
        try:
            process.stdin.write(encoded)
            process.stdin.close()
            deadline = time.monotonic() + 12
            while process.poll() is None:
                if cancel_event.is_set() or (
                    owner is not None
                    and (_revoked.is_set() or local_owner() != owner or not consent_enabled())
                ):
                    raise ValueError("Управление остановлено")
                if (
                    time.monotonic() > deadline
                    or output.tell() > 2_100_000
                    or errors.tell() > 65536
                ):
                    raise ValueError("Нативный помощник превысил лимит времени или вывода")
                cancel_event.wait(0.05)
            output.seek(0)
            data = output.read(2_100_001)
            if len(data) > 2_100_000:
                raise ValueError("Слишком большой ответ помощника")
            result = json.loads(data.decode("utf-8-sig"))
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise ValueError("Некорректный ответ помощника")
            if process.returncode and result.get("ok"):
                raise ValueError("Помощник завершился с ошибкой")
            return result
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            if process.stdin and not process.stdin.closed:
                process.stdin.close()


def status() -> dict:
    result = {
        "enabled": consent_enabled(),
        "platform": sys.platform,
        "vision_transport": "not_confirmed",
    }
    try:
        result.update(invoke_native({"action": "status"}, threading.Event()))
    except (OSError, ValueError):
        result.update(ok=False, native_available=False)
    result["can_restart_elevated"] = bool(
        sys.platform == "win32" and is_packaged_build() and result.get("elevated") is False
    )
    return result


def restart_elevated() -> None:
    """Перезапускает Докки с правами администратора (запрос UAC показывает система), чтобы помощник управлял и такими окнами."""
    if not (sys.platform == "win32" and is_packaged_build()):
        raise ValueError("Перезапуск от администратора доступен только в установленной версии для Windows")
    if not consent_enabled():
        raise ValueError("Сначала включите управление приложениями")
    import ctypes

    code = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, "--after-elevation", None, 1)
    if code <= 32:
        raise ValueError("Запрос прав администратора отклонён")
    # новая копия ждёт, пока эта закроется; закрываем окно штатно
    try:
        from PySide6.QtCore import QMetaObject, Qt
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            QMetaObject.invokeMethod(app, "quit", Qt.ConnectionType.QueuedConnection)
    except ImportError:
        pass


class ComputerSession:
    def __init__(self, turn_id: int | None, project_id: int | None, cancel_event: threading.Event):
        self.turn_id, self.project_id, self.cancel_event = turn_id, project_id, cancel_event
        self.owner = local_owner()
        self.snapshots: dict[str, dict] = {}

    def _check(self) -> int:
        if self.cancel_event.is_set() or local_owner() != self.owner or not consent_enabled():
            raise ValueError("Управление выключено или владелец изменился")
        with db() as conn:
            turn = conn.execute(
                "SELECT t.chat_id,t.status,c.project_id FROM turns t JOIN chats c ON c.id=t.chat_id WHERE t.id=?",
                (self.turn_id,),
            ).fetchone()
        if not turn or turn["status"] != "running" or turn["project_id"] != self.project_id:
            raise ValueError("Управление доступно только в активной задаче выбранного проекта")
        require_chat(turn["chat_id"])
        return turn["chat_id"]

    def run(self, args: dict) -> dict:
        chat_id = self._check()
        action = args.get("action")
        spec = ACTIONS.get(action)
        given = set(args) - {"action"}
        if spec is None or not spec[0] <= given or given - spec[0] - spec[1]:
            raise ValueError("Передайте только параметры выбранного действия")
        if "arguments" in args and (
            not isinstance(args["arguments"], list)
            or len(args["arguments"]) > 20
            or any(not isinstance(item, str) or len(item) > 500 or "\x00" in item for item in args["arguments"])
        ):
            raise ValueError("arguments: список до 20 строк по 500 символов")
        if "working_directory" in args and (
            not isinstance(args["working_directory"], str)
            or not Path(args["working_directory"]).is_absolute()
            or not Path(args["working_directory"]).is_dir()
        ):
            raise ValueError("working_directory: существующая папка по полному пути")
        if "query" in args and (not isinstance(args["query"], str) or not 1 <= len(args["query"]) <= 100):
            raise ValueError("query: строка до 100 символов")
        if "button" in args and args["button"] not in {"left", "right"}:
            raise ValueError("button: left или right")
        if "clicks" in args and (type(args["clicks"]) is not int or args["clicks"] not in {1, 2}):
            raise ValueError("clicks: 1 или 2")
        if "pid" in args and (type(args["pid"]) is not int or not 1 <= args["pid"] <= 2**31 - 1):
            raise ValueError("Некорректный pid")
        if "text" in args and (
            not isinstance(args["text"], str) or len(args["text"].encode("utf-16-le")) > 4000
        ):
            raise ValueError("Текст должен быть не длиннее 2000 UTF-16 символов")
        if "keys" in args and (
            not isinstance(args["keys"], list)
            or not 1 <= len(args["keys"]) <= 5
            or any(
                key
                not in {
                    "cmd",
                    "ctrl",
                    "alt",
                    "shift",
                    "enter",
                    "tab",
                    "escape",
                    "backspace",
                    "space",
                    "left",
                    "right",
                    "up",
                    "down",
                    *"abcdefghijklmnopqrstuvwxyz",
                }
                for key in args["keys"]
            )
        ):
            raise ValueError("Недопустимые клавиши")
        for key in ("x", "y", "delta_y", "window_id"):
            if key in args and (
                type(args[key]) is not int
                or abs(args[key]) > (2**53 if key == "window_id" else 20000)
            ):
                raise ValueError("Недопустимые координаты")
        if "delta_y" in args and abs(args["delta_y"]) > 2000:
            raise ValueError("Слишком большая прокрутка")
        if action == "launch":
            if not isinstance(args["application"], str):
                raise ValueError("Выберите существующее приложение по полному пути")
            path = Path(args["application"])
            if not path.is_absolute() or not path.exists() or path.is_symlink():
                raise ValueError("Выберите существующее приложение по полному пути")
        request = dict(args)
        request.pop("snapshot_id", None)
        request.pop("element_id", None)
        if action not in {"launch", "observe", "list_windows"}:
            snapshot = self.snapshots.get(args["snapshot_id"])
            if (
                not snapshot
                or time.monotonic() - snapshot["at"] > 30
                or snapshot["pid"] != args["pid"]
            ):
                raise ValueError("Снимок устарел; вызовите observe")
            request["identity"] = snapshot["identity"]
            if "element_id" in args:
                element = snapshot["elements"].get(args["element_id"])
                if not element or element.get("protected"):
                    raise ValueError("Элемент недоступен; вызовите observe")
                request["element_path"] = element["path"]
                if action == "click_element":
                    # центр элемента из снимка: модели не нужно считать координаты самой
                    frame = element.get("frame") or [0, 0, 0, 0]
                    if len(frame) != 4 or frame[2] < 1 or frame[3] < 1:
                        raise ValueError("У элемента нет видимой области; используйте press или screenshot")
                    request["action"] = "click"
                    request["x"] = int(frame[0] + frame[2] / 2)
                    request["y"] = int(frame[1] + frame[3] / 2)
                if "frame" in element:
                    request["expected_frame"] = element["frame"]
                request["expected"] = {
                    key: value
                    for key, value in element.items()
                    if key not in {"path", "frame", "id", "value"}
                }
        if not _device_lock.acquire(blocking=False):
            raise ValueError("Устройство уже используется другой задачей")
        try:
            self._check()
            _revoked.clear()
            result = invoke_native(request, self.cancel_event, owner=self.owner)
            self._check()
        finally:
            _device_lock.release()
        if action not in {"observe", "screenshot", "list_windows"}:
            self.snapshots.clear()
        if not result.get("ok") and result.get("error_code") in ERROR_HINTS:
            result["hint"] = ERROR_HINTS[result["error_code"]]
        if result.get("ok") and action == "observe":
            identifier = uuid.uuid4().hex
            elements = {}
            for node in result.get("elements", [])[:160]:
                node["id"] = uuid.uuid4().hex
                elements[node["id"]] = dict(node)
            self.snapshots = {
                identifier: {
                    "at": time.monotonic(),
                    "pid": args["pid"],
                    "identity": result.pop("identity"),
                    "elements": elements,
                }
            }
            result["snapshot_id"] = identifier
        if result.get("ok") and action == "screenshot":
            from koddi.attachments.service import store_attachment
            from PIL import Image

            raw = base64.b64decode(result.pop("png_base64"), validate=True)
            if len(raw) > 1_500_000:
                raise ValueError("Слишком большой скриншот")
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != "PNG" or image.width * image.height > 4_000_000:
                    raise ValueError("Некорректный скриншот")
                image.verify()
            attachment = store_attachment(chat_id, "computer-observation.png", io.BytesIO(raw))
            result.update(
                attachment_id=attachment["id"],
                image_url=f"/api/attachments/{attachment['id']}/content",
                vision_transport="not_confirmed",
            )
        return result

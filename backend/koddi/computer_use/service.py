"""Адаптер к agent-desktop: согласие, одно действие за раз, белый список команд, сессии и вложения."""

from __future__ import annotations

import atexit
import io
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from koddi.config import app_dir, data_dir, is_packaged_build
from koddi.policy.account import local_owner, require_chat
from koddi.storage.db import db, get_setting, set_setting


_device_lock = threading.Lock()
_revoked = threading.Event()
NATIVE = Path(__file__).with_name("native")
# не больше 6 слов (ограничение agent-desktop); фраза случайная и иногда меняется по ходу работы
CURSOR_LABELS = (
    "Докки за работой",
    "Сейчас всё сделаю",
    "Минутку, я тут кликаю",
    "Аккуратно жму кнопки",
    "Докки разбирается",
    "Не мешайте, я печатаю",
    "Хожу по окнам",
    "Докки ведёт мышку",
    "Почти готово",
    "Одну секунду",
)
CURSOR_LABEL_EVERY = 30.0
# свой курсор обязателен: без --image agent-desktop при включении пишет своё английское приветствие
CURSOR_IMAGE_NAME = "cursor.png"
CURSOR_HOTSPOT = "2,2"
# --accent из frontend/src/styles/tokens.css
CURSOR_ACCENT = "#438dff"
MAX_OUTPUT = 4_000_000
MAX_STDERR = 65536
# agent-desktop читает из --stdin не больше 10 000 байт UTF-8, кириллица - по 2 байта на букву
MAX_STDIN_BYTES = 10_000
MAX_STDIN = MAX_STDIN_BYTES
GUIDE_TOPICS = {"commands-observation", "commands-interaction", "commands-system", "workflows"}
SETTINGS_LINKS = {
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "screen_recording": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
}

# команды без побочных действий над чужими приложениями
READ_COMMANDS = frozenset({"status", "list-apps", "list-windows", "list-displays", "guide"})
PROPERTIES = {
    "get": frozenset({"text", "value", "title", "bounds", "role", "states"}),
    "is": frozenset({"visible", "enabled", "checked", "focused", "expanded", "selected"}),
}

REF_RE = re.compile(r"^@([A-Za-z0-9_-]{1,64}):e\d{1,9}$")
WINDOW_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
XY_RE = re.compile(r"^-?\d{1,6},-?\d{1,6}$")


def normalize_app(name: str) -> str:
    """Имя или bundle id без регистра, расширения .app и знаков: для сверки со SENSITIVE_APPS."""
    text = str(name).casefold().strip()
    if text.endswith(".app") or text.endswith(".exe"):
        text = text[:-4]
    return re.sub(r"[^0-9a-zа-яё]+", "", text)


# нормализованные имена и bundle id: терминалы, менеджеры паролей, настройки системы,
# связка ключей, банки и крипто-кошельки, сама Докки
SENSITIVE_APPS = frozenset(
    normalize_app(item)
    for item in (
        # терминалы и консоли
        "Terminal", "com.apple.Terminal", "iTerm", "iTerm2", "com.googlecode.iterm2", "Warp",
        "dev.warp.Warp-Stable", "Ghostty", "com.mitchellh.ghostty", "Alacritty", "kitty", "Hyper",
        "WezTerm", "Tabby", "Termius", "cmd", "powershell", "pwsh", "Windows Terminal",
        "WindowsTerminal", "conhost", "OpenConsole", "Командная строка", "mintty",
        # менеджеры паролей
        "1Password", "com.1password.1password", "Bitwarden", "com.bitwarden.desktop", "LastPass",
        "Dashlane", "KeePassXC", "org.keepassxc.keepassxc", "KeePass", "Enpass", "NordPass",
        "Keeper", "RoboForm", "Proton Pass", "Passwords", "com.apple.Passwords",
        # система, ключи, администрирование
        "System Settings", "System Preferences", "com.apple.systempreferences", "Keychain Access",
        "com.apple.keychainaccess", "Настройки", "Параметры", "SystemSettings", "ImmersiveControlPanel",
        "Панель управления", "Task Manager", "Taskmgr", "Диспетчер задач", "regedit", "mmc",
        "Редактор реестра", "secpol", "gpedit", "Activity Monitor", "com.apple.ActivityMonitor",
        "Disk Utility", "com.apple.DiskUtility", "Console", "com.apple.Console",
        # банки и крипто-кошельки
        "Exodus", "Ledger Live", "Trezor Suite", "Electrum", "Atomic Wallet", "Trust Wallet",
        "Coinbase", "Coinbase Wallet", "Binance", "Bitcoin Core", "Sparrow", "Wasabi Wallet",
        "MetaMask", "Phantom", "Tinkoff", "Т-Банк", "Сбербанк", "СберБанк Онлайн", "Альфа-Банк",
        "ВТБ", "Raiffeisen", "PayPal", "Wise",
        # сама Докки
        "Dokky", "DokkyApp", "Докки", "koddi",
    )
)


def sensitive_app(name: str | None) -> bool:
    return bool(name) and normalize_app(name) in SENSITIVE_APPS


# код agent-desktop -> подсказка модели: что случилось и что делать дальше
ERROR_HINTS = {
    "STALE_REF": "Интерфейс изменился, ссылка устарела. Сделайте snapshot заново и возьмите свежий ref.",
    "AMBIGUOUS_TARGET": "Под запрос подходит несколько окон или элементов. Вызовите list-windows и повторите с window_id (или уточните запрос).",
    "PERM_DENIED": "Нет разрешения системы. Не ищите приложения и не открывайте настройки через shell. Попросите пользователя включить Докки в Настройках: Конфиденциальность и безопасность - Универсальный доступ (и Запись экрана для снимков). Если Докки там уже включена, её нужно выключить и включить снова или убрать минусом и добавить заново: после обновления macOS иногда не узнаёт новую версию. Затем повторите команду.",
    "POLICY_DENIED": "Действие запретила политика безопасности. Не обходите её: объясните пользователю, что нужно сделать самому.",
    "APP_NOT_FOUND": "Приложение не найдено или не запущено. Вызовите list-apps, при необходимости launch.",
    "APP_UNRESPONSIVE": "Приложение не отвечает. Подождите (wait) и повторите, либо сообщите пользователю.",
    "TIMEOUT": "Время ожидания вышло. Проверьте состояние через snapshot и повторите с меньшей областью.",
    "ACTION_NOT_SUPPORTED": "Элемент не поддерживает это действие. Посмотрите available_actions в snapshot и выберите другое.",
    "ACTION_FAILED": "Действие не удалось. Сделайте snapshot, проверьте элемент и окно, затем повторите или выберите другой способ.",
    "SNAPSHOT_NOT_FOUND": "Снимок не найден или устарел. Сделайте snapshot заново.",
    "ELEMENT_NOT_FOUND": "Элемент не найден. Сделайте snapshot или find с другими role и name.",
    "INVALID_ARGS": "Неверные параметры команды. Проверьте обязательные поля.",
    "WINDOW_NOT_FOUND": "Окно не найдено. Вызовите list-windows и возьмите актуальный window_id.",
    "PLATFORM_NOT_SUPPORTED": "Команда не поддерживается на этой системе.",
    "NOTIFICATION_NOT_FOUND": "Подходящего уведомления нет. Вызовите list-notifications.",
    "INTERNAL": "Внутренняя ошибка agent-desktop. Повторите один раз, затем сообщите пользователю.",
}
ELEVATION_HINT = (
    "Окно приложения работает с правами администратора, а Докки - без них. Попросите пользователя "
    "нажать «Перезапустить от администратора» в Настройки - Система - Управление приложениями."
)
ELEVATION_RE = re.compile(r"integrity level|UIPI|elevat|administrator|access is denied", re.I)


@dataclass(frozen=True)
class Spec:
    pos: tuple[str, ...] = ()
    # поле модели -> флаг agent-desktop
    flags: dict = field(default_factory=dict)
    # доп. флаги из options: имя -> тип (int, signed, str, bool, list, xy, enum:a|b)
    opts: dict = field(default_factory=dict)
    stdin: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    timeout: int = 20
    headed: bool = False  # команда требует физического ввода


_REF = Spec(pos=("ref",), required=("ref",), opts={"timeout_ms": "int"})
_WIN = {"app": "app", "window_id": "window-id"}
COMMANDS: dict[str, Spec] = {
    "status": Spec(),
    "list-apps": Spec(flags={"app": "app"}, opts={"timeout_ms": "int"}),
    "list-windows": Spec(flags={"app": "app"}, opts={"timeout_ms": "int"}),
    "list-displays": Spec(),
    "snapshot": Spec(
        flags=_WIN,
        timeout=35,
        opts={
            "max_depth": "int", "include_bounds": "bool", "interactive_only": "bool", "compact": "bool",
            "skeleton": "bool", "surface": "str", "root": "str", "timeout_ms": "int",
            "force_electron_a11y": "bool",
        },
    ),
    "find": Spec(
        flags={**_WIN, "role": "role", "name": "name", "query": "text"},
        timeout=35,
        opts={
            "value": "str", "description": "str", "native_id": "str", "exact": "bool", "root": "str",
            "surface": "str", "state": "str", "timeout_ms": "int", "count": "int", "first": "bool",
            "last": "bool", "nth": "int", "limit": "int",
        },
    ),
    "get": Spec(pos=("ref",), flags={"value": "property"}, required=("ref",)),
    "is": Spec(pos=("ref",), flags={"value": "property"}, required=("ref",)),
    "screenshot": Spec(flags=_WIN, opts={"screen": "int"}, timeout=30),
    "list-surfaces": Spec(flags={"app": "app"}),
    "click": _REF,
    "double-click": _REF,
    "right-click": _REF,
    "clear": _REF,
    "focus": _REF,
    "toggle": _REF,
    "check": _REF,
    "uncheck": _REF,
    "expand": _REF,
    "collapse": _REF,
    "scroll-to": _REF,
    "type": Spec(pos=("ref",), required=("ref", "text"), stdin=("text",), opts={"timeout_ms": "int", "interval_ms": "int"}, timeout=35),
    "set-value": Spec(pos=("ref",), required=("ref",), stdin=("value", "text"), opts={"timeout_ms": "int"}, timeout=30),
    "select": Spec(pos=("ref", "value"), required=("ref", "value"), opts={"timeout_ms": "int"}),
    "scroll": Spec(pos=("ref",), flags={"direction": "direction", "amount": "amount"}, required=("ref",), opts={"timeout_ms": "int"}),
    "press": Spec(pos=("combo",), flags=_WIN, required=("combo",), opts={"force": "bool"}),
    "hover": Spec(pos=("ref",), opts={"timeout_ms": "int"}, headed=True),
    "drag": Spec(
        flags={"ref": "from", "to_ref": "to"},
        opts={"from_xy": "xy", "to_xy": "xy", "duration": "int", "drop_delay": "int", "timeout_ms": "int"},
        timeout=35,
        headed=True,
    ),
    "mouse-click": Spec(
        opts={"button": "enum:left|right|middle", "count": "int", "modifiers": "list"},
        required=("x", "y"), headed=True,
    ),
    "mouse-move": Spec(required=("x", "y"), headed=True),
    "mouse-wheel": Spec(
        flags={"x": "x", "y": "y"}, required=("x", "y"), opts={"dy": "signed", "dx": "signed", "modifiers": "list"}, headed=True,
    ),
    "launch": Spec(
        pos=("app",), required=("app",), timeout=40,
        opts={"timeout": "int", "arg": "list", "no_attach": "bool", "activate": "bool", "cwd": "dir"},
    ),
    "close-app": Spec(pos=("app",), required=("app",), opts={"force": "bool"}),
    "focus-window": Spec(flags=_WIN, opts={"title": "str"}),
    "resize-window": Spec(flags=_WIN, opts={"width": "int", "height": "int"}),
    "move-window": Spec(flags={**_WIN, "x": "x", "y": "y"}, required=("x", "y")),
    "minimize": Spec(flags=_WIN),
    "maximize": Spec(flags=_WIN),
    "restore": Spec(flags=_WIN),
    "wait": Spec(
        pos=("amount",), flags={"app": "app", "window_id": "window-id", "query": "text", "value": "value"}, timeout=40,
        opts={
            "element": "str", "window": "str", "menu": "bool", "menu_closed": "bool", "notification": "bool",
            "event": "str", "predicate": "str", "action": "str", "count": "int", "timeout": "int",
        },
    ),
    "clipboard-get": Spec(opts={"format": "str"}),
    "clipboard-set": Spec(required=("text",), stdin=("text",)),
    "list-notifications": Spec(flags={"app": "app", "query": "text"}, opts={"limit": "int"}),
    "batch": Spec(timeout=60, opts={"stop_on_error": "bool", "timeout_ms": "int"}),
}
COMMAND_NAMES = ("guide", *COMMANDS)
# команды batch: serde-имена полей agent-desktop (ref -> ref_id); команд с текстом здесь нет,
# чтобы текст не попадал в argv
BATCH_COMMANDS = {
    **{name: {"ref": "ref_id", "timeout_ms": "timeout_ms"} for name in (
        "click", "double-click", "right-click", "clear", "focus", "toggle", "check", "uncheck",
        "expand", "collapse", "scroll-to",
    )},
    "select": {"ref": "ref_id", "value": "value", "timeout_ms": "timeout_ms"},
    "scroll": {"ref": "ref_id", "direction": "direction", "amount": "amount", "timeout_ms": "timeout_ms"},
    "press": {"combo": "combo", "app": "app", "window_id": "window_id"},
}
FIELDS = {
    "command", "app", "ref", "text", "combo", "window_id", "query", "role", "name", "direction", "amount",
    "x", "y", "to_ref", "value", "headed", "options", "items",
}
DIRECTIONS = {"up", "down", "left", "right"}


@dataclass
class Invocation:
    command: str
    argv: list
    stdin: str | None
    timeout: int
    screenshot_path: Path | None = None


def _text(value, name: str, limit: int = 2000) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or "\x00" in value:
        raise ValueError(f"{name}: непустая строка до {limit} символов")
    return value


def _int(value, name: str, *, signed: bool = False, high: int = 10_000_000) -> int:
    if type(value) is not int or abs(value) > high or (value < 0 and not signed):
        raise ValueError(f"{name}: целое число" + ("" if signed else " не меньше нуля"))
    return value


def _positional(value, name: str) -> str:
    text = _text(value, name, 500)
    if text.startswith("-"):
        raise ValueError(f"{name}: значение не может начинаться с «-»")
    return text


def _checked_field(name: str, value):
    if name in {"ref", "to_ref"}:
        if not isinstance(value, str) or not REF_RE.match(value):
            raise ValueError(f"{name}: ссылка вида @snapshot:eN из snapshot или find")
        return value
    if name == "window_id":
        if not isinstance(value, str) or not WINDOW_RE.match(value):
            raise ValueError("window_id: идентификатор из list-windows")
        return value
    if name == "direction":
        if value not in DIRECTIONS:
            raise ValueError("direction: up, down, left или right")
        return value
    if name == "amount":
        return _int(value, "amount", high=60000)
    if name in {"x", "y"}:
        return _int(value, name, signed=True, high=100000)
    if name in {"app", "combo"}:
        return _positional(value, name)
    return _text(value, name, 500)


def _option(kind: str, name: str, value):
    if kind == "int":
        return _int(value, name)
    if kind == "signed":
        return _int(value, name, signed=True, high=100000)
    if kind == "str":
        return _text(value, name, 500)
    if kind == "xy":
        if not isinstance(value, str) or not XY_RE.match(value):
            raise ValueError(f"{name}: координаты вида x,y")
        return value
    if kind.startswith("enum:"):
        if value not in kind[5:].split("|"):
            raise ValueError(f"{name}: допустимо {', '.join(kind[5:].split('|'))}")
        return value
    raise AssertionError(kind)


def _apply_options(spec: Spec, options, argv: list) -> None:
    if options is None:
        return
    if not isinstance(options, dict):
        raise ValueError("options: объект с дополнительными флагами")
    for name, value in options.items():
        kind = spec.opts.get(name)
        if kind is None:
            raise ValueError(f"options.{name}: флаг не разрешён для этой команды")
        flag = "--" + name.replace("_", "-")
        if kind == "bool":
            if type(value) is not bool:
                raise ValueError(f"options.{name}: true или false")
            if value:
                argv.append(flag)
        elif kind == "list":
            if not isinstance(value, list) or len(value) > 20:
                raise ValueError(f"options.{name}: список до 20 элементов")
            for item in value:
                argv.append(f"{flag}={_text(item, 'options.' + name, 500)}")
        elif kind == "dir":
            path = Path(_text(value, "options." + name, 1000))
            if not path.is_absolute() or not path.is_dir():
                raise ValueError(f"options.{name}: существующая папка по полному пути")
            argv.append(f"{flag}={path}")
        else:
            argv.append(f"{flag}={_option(kind, 'options.' + name, value)}")


def _batch_items(items) -> str:
    if not isinstance(items, list) or not 1 <= len(items) <= 64:
        raise ValueError("items: список от 1 до 64 команд")
    records = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict) or item.get("command") not in BATCH_COMMANDS:
            raise ValueError(f"items[{index}].command: одна из {', '.join(BATCH_COMMANDS)}")
        mapping = BATCH_COMMANDS[item["command"]]
        record = {}
        for key, value in item.items():
            if key == "command":
                continue
            if key == "options":
                if not isinstance(value, dict) or set(value) - {"timeout_ms"}:
                    raise ValueError(f"items[{index}].options: только timeout_ms")
                if "timeout_ms" in value:
                    record["timeout_ms"] = _int(value["timeout_ms"], "timeout_ms")
                continue
            if key not in mapping or key == "timeout_ms":
                raise ValueError(f"items[{index}].{key}: поле не разрешено для {item['command']}")
            record[mapping[key]] = _checked_field(key, value)
        records.append({"command": item["command"], "args": record})
    return json.dumps(records, ensure_ascii=True, separators=(",", ":"))


def build_invocation(args: dict) -> Invocation:
    """Разбирает аргументы инструмента в безопасную командную строку agent-desktop."""
    if not isinstance(args, dict):
        raise ValueError("Ожидался объект с аргументами")
    unknown = set(args) - FIELDS
    if unknown:
        raise ValueError("Лишние параметры: " + ", ".join(sorted(map(str, unknown))))
    command = args.get("command")
    if command not in COMMAND_NAMES:
        raise ValueError("command: одна из " + ", ".join(COMMAND_NAMES))
    if command == "guide":
        extra = set(args) - {"command", "query"}
        topic = args.get("query")
        if extra or (topic is not None and topic not in GUIDE_TOPICS):
            raise ValueError("guide: необязательный query из " + ", ".join(sorted(GUIDE_TOPICS)))
        return Invocation("guide", [], None, 20)
    spec = COMMANDS[command]
    given = set(args) - {"command", "headed", "options"}
    if command == "batch":
        allowed = {"items"}
    else:
        allowed = set(spec.pos) | set(spec.flags) | set(spec.stdin) | set(spec.required) | (
            {"x", "y"} if command in {"mouse-click", "mouse-move", "hover"} else set()
        )
    if given - allowed:
        raise ValueError(f"{command}: лишние параметры: " + ", ".join(sorted(given - allowed)))
    for name in spec.required:
        if name not in args and not (command == "set-value" and "text" in args):
            raise ValueError(f"{command}: нужен параметр {name}")
    if command == "set-value" and "value" not in args and "text" not in args:
        raise ValueError("set-value: нужен параметр value")
    if "headed" in args and type(args["headed"]) is not bool:
        raise ValueError("headed: true или false")
    argv: list = []
    stdin = None
    screenshot_path = None
    if command == "batch":
        argv = ["batch", _batch_items(args.get("items"))]
        _apply_options(spec, args.get("options"), argv)
        return Invocation(command, argv, None, spec.timeout)
    argv = [command]
    if command == "wait" and "amount" in args:
        argv.append(str(_int(args["amount"], "amount", high=60000)))
    elif command != "wait":
        for name in spec.pos:
            if name in args:
                argv.append(_positional(_checked_field(name, args[name]), name))
    for name, flag in spec.flags.items():
        if name in args and name not in spec.pos:
            value = _checked_field(name, args[name])
            if name == "value" and command in PROPERTIES:
                value = value.strip().casefold()
                if value not in PROPERTIES[command]:
                    raise ValueError(f"value: допустимо {', '.join(sorted(PROPERTIES[command]))}")
            argv.append(f"--{flag}={value}")
    if command in {"mouse-click", "mouse-move", "hover"} and ("x" in args or "y" in args):
        if "x" not in args or "y" not in args:
            raise ValueError("Нужны обе координаты x и y")
        argv.append(f"--xy={_checked_field('x', args['x'])},{_checked_field('y', args['y'])}")
    if command == "mouse-move" and not any(item.startswith("--xy") for item in argv):
        raise ValueError("mouse-move: нужны x и y")
    if command == "move-window" and ("x" not in args or "y" not in args):
        raise ValueError("move-window: нужны x и y")
    for name in spec.stdin:
        if name in args:
            stdin = _text(args[name], name, MAX_STDIN)
            if len(stdin.encode("utf-8")) > MAX_STDIN_BYTES:
                raise ValueError(f"{name}: не больше {MAX_STDIN_BYTES} байт UTF-8 (около 5000 русских букв); длинный текст вводите частями")
            argv.append("--stdin")
            break
    if command == "screenshot":
        directory = Path(tempfile.mkdtemp(prefix="dokky-shot-"))
        screenshot_path = directory / "screen.png"
        argv.append(str(screenshot_path))
    _apply_options(spec, args.get("options"), argv)
    if spec.headed or args.get("headed"):
        argv.insert(0, "--headed")
    return Invocation(command, argv, stdin, spec.timeout, screenshot_path)


# --- процесс ---------------------------------------------------------------


def binary_path() -> Path:
    if sys.platform not in {"darwin", "win32"}:
        raise ValueError("Computer Use пока поддерживает macOS и Windows")
    name = "agent-desktop.exe" if sys.platform == "win32" else "agent-desktop"
    base = (
        app_dir() / "koddi" / "computer_use" / "native" / "agent-desktop"
        if is_packaged_build()
        else NATIVE / "agent-desktop"
    )
    path = base / name
    if not path.is_file() or path.is_symlink():
        raise ValueError("Сначала скачайте agent-desktop: python scripts/fetch_agent_desktop.py")
    return path


def state_home() -> Path:
    home = (data_dir() / "agent-desktop").resolve()
    if sys.platform == "win32":
        # отдельная папка помогает и после прежней попытки с наследованным DACL
        return home / "windows-private"
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(home, 0o700)
    return home


def _environment() -> dict:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("AGENT_DESKTOP_")}
    env["AGENT_DESKTOP_HOME"] = str(state_home())
    return env


def run_process(
    argv: list,
    *,
    stdin: str | None = None,
    timeout: int = 20,
    cancel_event: threading.Event | None = None,
    owner: str | None = None,
) -> dict:
    """Запускает agent-desktop и возвращает его JSON-конверт; без shell, с лимитами времени и вывода."""
    cancel_event = cancel_event or threading.Event()
    encoded = stdin.encode("utf-8") if stdin is not None else b""
    if len(encoded) > MAX_STDIN_BYTES:
        raise ValueError("Слишком большой текст")
    command = [str(binary_path()), *argv]
    options = {}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=errors,
            env=_environment(),
            shell=False,
            **options,
        )
        writer = threading.Thread(target=_feed, args=(process, encoded), daemon=True)
        writer.start()
        try:
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if cancel_event.is_set() or (
                    owner is not None
                    and (_revoked.is_set() or local_owner() != owner or not consent_enabled())
                ):
                    raise ValueError("Управление остановлено")
                if time.monotonic() > deadline:
                    raise ValueError("agent-desktop не ответил за отведённое время")
                if output.tell() > MAX_OUTPUT or errors.tell() > MAX_STDERR:
                    raise ValueError("agent-desktop превысил лимит вывода")
                cancel_event.wait(0.05)
            output.seek(0)
            data = output.read(MAX_OUTPUT + 1)
            if len(data) > MAX_OUTPUT:
                raise ValueError("Слишком большой ответ agent-desktop")
            try:
                result = json.loads(data.decode("utf-8", "replace").lstrip("﻿"))
            except ValueError:
                raise ValueError("Некорректный ответ agent-desktop") from None
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise ValueError("Некорректный ответ agent-desktop")
            if process.returncode and result.get("ok"):
                raise ValueError("agent-desktop завершился с ошибкой")
            return result
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            writer.join(timeout=1)


def _feed(process: subprocess.Popen, data: bytes) -> None:
    try:
        if data:
            process.stdin.write(data)
        process.stdin.close()
    except OSError:
        pass


# --- согласие ----------------------------------------------------------------


def consent_enabled() -> bool:
    return get_setting("computer_use:" + local_owner(), True) is True


def set_consent(enabled: bool) -> None:
    if type(enabled) is not bool:
        raise ValueError("Ожидалось логическое значение")
    # уже начатый вызов не оживает, если пользователь сразу включил управление снова
    _revoked.set()
    set_setting("computer_use:" + local_owner(), enabled)
    if not enabled:
        end_sessions()


# --- сессии agent-desktop и память о целях --------------------------------------

_state_lock = threading.Lock()
_sessions: dict[int, str] = {}
# фраза у курсора сессии и когда её поставили
_cursor_labels: dict[str, tuple[str, float]] = {}
_snapshot_apps: dict[int, dict[str, str]] = {}
_window_apps: dict[int, dict[str, str]] = {}
# разные имена одного приложения в чате (bundle id, имя из launch, имя окна) -> одно имя для разрешения
_app_aliases: dict[int, dict[str, str]] = {}
_MEMORY = 64


def _remember(table: dict, chat_id: int, key: str, app: str) -> None:
    with _state_lock:
        items = table.setdefault(chat_id, {})
        items.pop(key, None)
        items[key] = app
        while len(items) > _MEMORY:
            items.pop(next(iter(items)))


def _recall(table: dict, chat_id: int | None, key: str) -> str | None:
    with _state_lock:
        return table.get(chat_id or 0, {}).get(key)


def _link_apps(chat_id: int, *names) -> None:
    """Запоминает, что эти имена - одно приложение: «да» на одно покрывает остальные."""
    keys = [normalize_app(name) for name in names if isinstance(name, str) and name.strip()]
    keys = [key for key in dict.fromkeys(keys) if key]
    # системные и опасные приложения не склеиваем ни с чем: для них вопрос на каждое действие
    if len(keys) < 2 or any(key in SENSITIVE_APPS for key in keys):
        return
    with _state_lock:
        if chat_id not in _app_aliases and len(_app_aliases) >= _MEMORY:
            _app_aliases.pop(next(iter(_app_aliases)))
        table = _app_aliases.setdefault(chat_id, {})
        canonical = next((table[key] for key in keys if key in table), keys[0])
        for key in keys:
            table.pop(key, None)
            table[key] = canonical
        while len(table) > _MEMORY * 4:
            table.pop(next(iter(table)))


def same_app(asked, got) -> bool:
    """launch нашёл то, что просили: то же имя, одно внутри другого или хвост bundle id (com.apple.TextEdit)."""
    if not isinstance(asked, str) or not isinstance(got, str):
        return False
    tail = asked.rsplit(".", 1)[-1] if asked.count(".") >= 2 else asked
    left, right, short = normalize_app(asked), normalize_app(got), normalize_app(tail)
    if not left or not right:
        return False
    return left == right or short == right or (min(len(left), len(right)) >= 4 and (left in right or right in left))


def app_key(name: str, chat_id: int | None) -> str:
    """Ключ разрешения на приложение с учётом его других имён в этом чате."""
    key = normalize_app(name)
    if chat_id is None:
        return key
    with _state_lock:
        return _app_aliases.get(chat_id, {}).get(key, key)


def end_sessions() -> None:
    """Закрывает сессии agent-desktop (с ними исчезает и курсор агента)."""
    with _state_lock:
        ids = list(_sessions.values())
        _sessions.clear()
        _cursor_labels.clear()
        _snapshot_apps.clear()
        _window_apps.clear()
        _app_aliases.clear()
    for session_id in ids:
        try:
            run_process(["session", "end", session_id], timeout=10)
        except (OSError, ValueError):
            pass


atexit.register(end_sessions)


PERMISSION_GUIDE_EVERY = 120.0
_guide_opened_at = [0.0]


def _open_permission_guide(cancel_event: threading.Event, owner: str) -> bool:
    """Открывает Настройки на нужном разрешении с подсказкой agent-desktop, не чаще раза в 2 минуты."""
    with _state_lock:
        now = time.monotonic()
        if _guide_opened_at[0] and now - _guide_opened_at[0] < PERMISSION_GUIDE_EVERY:
            return False
        _guide_opened_at[0] = now
    try:
        reply = run_process(["permissions", "--guide"], timeout=15, cancel_event=cancel_event, owner=owner)
    except (OSError, ValueError):
        return False
    return bool(reply.get("ok")) and (reply.get("data") or {}).get("guide") in {"started", "already_running"}


def cursor_image() -> Path:
    base = app_dir() / "koddi" / "computer_use" / "native" if is_packaged_build() else NATIVE
    return base / CURSOR_IMAGE_NAME


def _cursor_label(previous: str | None = None) -> str:
    choices = [label for label in CURSOR_LABELS if label != previous] or list(CURSOR_LABELS)
    return random.choice(choices)


def _show_cursor(session_id: str, cancel_event: threading.Event, owner: str) -> None:
    """Включает курсор со своей картинкой и новой фразой; сбой оформления не мешает работе."""
    image = cursor_image()
    if not image.is_file() or image.is_symlink():
        # без картинки курсор выйдет с английским приветствием - лучше без курсора
        return
    with _state_lock:
        label = _cursor_label(_cursor_labels.get(session_id, (None, 0.0))[0])
        _cursor_labels[session_id] = (label, time.monotonic())
    try:
        run_process(
            ["--session", session_id, "cursor-overlay", "enable", f"--label={label}",
             f"--accent={CURSOR_ACCENT}", f"--image={image}", f"--hotspot={CURSOR_HOTSPOT}"],
            timeout=10, cancel_event=cancel_event, owner=owner,
        )
    except (OSError, ValueError):
        pass


def _refresh_cursor(session_id: str, cancel_event: threading.Event, owner: str) -> None:
    with _state_lock:
        shown = _cursor_labels.get(session_id)
    if shown is None or time.monotonic() - shown[1] >= CURSOR_LABEL_EVERY:
        _show_cursor(session_id, cancel_event, owner)


def _ensure_session(chat_id: int, cancel_event: threading.Event, owner: str) -> str | None:
    with _state_lock:
        known = _sessions.get(chat_id)
    if known:
        return known
    try:
        started = run_process(
            # без --cursor: он включает курсор с английским приветствием agent-desktop
            ["session", "start", f"--name=dokky-chat-{chat_id}"],
            timeout=15, cancel_event=cancel_event, owner=owner,
        )
    except ValueError:
        return None
    session_id = (started.get("data") or {}).get("session_id") if started.get("ok") else None
    if not isinstance(session_id, str) or not session_id:
        return None
    _show_cursor(session_id, cancel_event, owner)
    with _state_lock:
        _sessions[chat_id] = session_id
    return session_id


# --- цель команды (для политики) -----------------------------------------------


def _single_target(args: dict, chat_id: int | None) -> str | None:
    app = args.get("app")
    if isinstance(app, str) and app:
        return app
    ref = args.get("ref")
    if isinstance(ref, str):
        match = REF_RE.match(ref)
        if match:
            return _recall(_snapshot_apps, chat_id, match.group(1))
    window = args.get("window_id")
    if isinstance(window, str):
        return _recall(_window_apps, chat_id, window)
    return None


def target_app(args: dict, chat_id: int | None) -> str | None:
    """Приложение, на которое действует команда; None - определить нельзя."""
    if not isinstance(args, dict):
        return None
    if args.get("command") == "batch":
        items = args.get("items")
        if not isinstance(items, list) or not items:
            return None
        targets = {
            _single_target(item, chat_id) if isinstance(item, dict) else None for item in items
        }
        if len(targets) == 1:
            return targets.pop()
        return None
    return _single_target(args, chat_id)


# --- состояние и права ---------------------------------------------------------


def _is_admin() -> bool | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return None


def status() -> dict:
    result = {
        "enabled": consent_enabled(),
        "platform": sys.platform,
        "vision_transport": "not_confirmed",
        "ok": False,
        "native_available": False,
        "accessibility": False,
        "screen_capture": False,
        "permissions": {},
    }
    try:
        reply = run_process(["status"], timeout=12)
    except (OSError, ValueError) as error:
        result["error"] = str(error)
    else:
        data = reply.get("data") if reply.get("ok") else None
        if isinstance(data, dict):
            permissions = data.get("permissions") if isinstance(data.get("permissions"), dict) else {}
            states = {
                key: (value.get("state") if isinstance(value, dict) else None)
                for key, value in permissions.items()
            }
            result.update(
                native_available=True,
                version=data.get("version"),
                permissions=states,
                accessibility=states.get("accessibility") == "granted",
                screen_capture=states.get("screen_recording") == "granted",
            )
            # на Windows системных разрешений нет: годна сама доступность agent-desktop
            result["ok"] = (
                sys.platform == "win32" or (result["accessibility"] and result["screen_capture"])
            )
        elif not reply.get("ok"):
            result["error"] = str((reply.get("error") or {}).get("message", ""))[:300]
    if sys.platform == "darwin":
        result["settings_links"] = SETTINGS_LINKS
    elevated = _is_admin()
    result["elevated"] = elevated
    result["can_restart_elevated"] = bool(
        sys.platform == "win32" and is_packaged_build() and elevated is False
    )
    return result


def restart_elevated() -> None:
    """Перезапускает Докки с правами администратора (запрос UAC показывает система), чтобы управлять и такими окнами."""
    if not (sys.platform == "win32" and is_packaged_build()):
        raise ValueError("Перезапуск от администратора доступен только в установленной версии для Windows")
    if not consent_enabled():
        raise ValueError("Сначала включите управление приложениями")
    import ctypes

    end_sessions()
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


# --- сессия инструмента ------------------------------------------------------------

_guide_cache: dict[str, str] = {}


class ComputerSession:
    def __init__(self, turn_id: int | None, project_id: int | None, cancel_event: threading.Event):
        self.turn_id, self.project_id, self.cancel_event = turn_id, project_id, cancel_event
        self.owner = local_owner()

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

    def _guide(self, topic: str | None) -> dict:
        key = topic or ""
        if key not in _guide_cache:
            parts = []
            for target in (["desktop"] + ([topic] if topic else []), ["platform"]):
                reply = run_process(["skills", "get", *target], timeout=15, cancel_event=self.cancel_event, owner=self.owner)
                content = (reply.get("data") or {}).get("content") if reply.get("ok") else None
                if not isinstance(content, str):
                    raise ValueError("Руководство agent-desktop недоступно")
                parts.append(content)
            _guide_cache[key] = "\n\n".join(parts)
        return {"ok": True, "command": "guide", "data": {"guide": _guide_cache[key]}}

    def run(self, args: dict) -> dict:
        chat_id = self._check()
        invocation = build_invocation(args)
        try:
            if invocation.command == "guide":
                _revoked.clear()
                return self._guide(args.get("query"))
            if not _device_lock.acquire(blocking=False):
                raise ValueError("Устройство уже используется другой задачей")
            try:
                self._check()
                _revoked.clear()
                argv = list(invocation.argv)
                if invocation.command != "status":
                    session_id = _ensure_session(chat_id, self.cancel_event, self.owner)
                    if session_id:
                        if invocation.command not in READ_COMMANDS:
                            _refresh_cursor(session_id, self.cancel_event, self.owner)
                        argv = ["--session", session_id, *argv]
                reply = run_process(
                    argv,
                    stdin=invocation.stdin,
                    timeout=invocation.timeout,
                    cancel_event=self.cancel_event,
                    owner=self.owner,
                )
                self._check()
            finally:
                _device_lock.release()
            return self._result(invocation, reply, chat_id, args)
        finally:
            if invocation.screenshot_path is not None:
                _cleanup(invocation.screenshot_path)

    def _result(self, invocation: Invocation, reply: dict, chat_id: int, args: dict) -> dict:
        command = invocation.command
        if not reply.get("ok"):
            error = reply.get("error") if isinstance(reply.get("error"), dict) else {}
            code = str(error.get("code") or "INTERNAL")[:40]
            message = str(error.get("message") or "")[:500]
            result = {"ok": False, "command": command, "error_code": code, "error": message}
            hint = ERROR_HINTS.get(code)
            if sys.platform == "win32" and (
                ELEVATION_RE.search(message) or ELEVATION_RE.search(str(error.get("suggestion") or ""))
            ):
                hint, result["needs_elevation"] = ELEVATION_HINT, True
            if code == "PERM_DENIED" and sys.platform == "darwin" and _open_permission_guide(self.cancel_event, self.owner):
                result["settings_opened"] = True
                hint = (hint or "") + " Докки уже открыла нужный раздел Настроек с подсказкой, повторно открывать не нужно."
            if hint:
                result["hint"] = hint
            if error.get("suggestion"):
                result["suggestion"] = str(error["suggestion"])[:500]
            for key in ("recovery", "details"):
                if error.get(key) is not None:
                    result[key] = error[key]
            disposition = error.get("disposition")
            if isinstance(disposition, dict) and isinstance(disposition.get("retry"), (str, bool)):
                result["retry"] = disposition["retry"]
            return result
        data = reply.get("data")
        if command in {"type", "set-value"} and isinstance(data, dict):
            details = data.get("details")
            if isinstance(details, dict) and details.get("verification_reason") == "postcondition_mismatch":
                return {
                    "ok": False,
                    "command": command,
                    "error_code": "ACTION_UNVERIFIED",
                    "error": "Текст мог быть введён не полностью.",
                    "hint": "Прочитайте текущее значение поля и исправьте только недостающую часть; не повторяйте ввод целиком.",
                    "retry": "never",
                    "data": data,
                }
        result = {"ok": True, "command": command, "data": data}
        if isinstance(data, (dict, list)):
            self._learn(command, data, args, chat_id)
        if command == "screenshot":
            result["data"] = self._attach_screenshot(invocation, data, chat_id)
        return result

    @staticmethod
    def _learn(command: str, data, args: dict, chat_id: int) -> None:
        if isinstance(data, dict):
            snapshot_id = data.get("snapshot_id")
            app = data.get("app") if isinstance(data.get("app"), str) else args.get("app")
            if command in {"snapshot", "find"} and isinstance(snapshot_id, str) and isinstance(app, str) and app:
                _remember(_snapshot_apps, chat_id, snapshot_id, app)
        if command == "launch" and isinstance(data, dict):
            window = data.get("window") if isinstance(data.get("window"), dict) else {}
            found = [name for name in (data.get("app"), window.get("app_name")) if isinstance(name, str)]
            # запустилось что-то другое (нечёткое совпадение имени) - разрешение на него не переносится
            if found and all(same_app(args.get("app"), name) for name in found):
                _link_apps(chat_id, args.get("app"), *found)
        if command == "list-apps" and isinstance(data, dict) and isinstance(data.get("apps"), list):
            for item in data["apps"][:500]:
                if isinstance(item, dict):
                    _link_apps(chat_id, item.get("name"), item.get("bundle_id"))
        if command == "list-windows" and isinstance(data, list):
            for window in data:
                if isinstance(window, dict) and isinstance(window.get("id"), str) and isinstance(window.get("app_name"), str):
                    _remember(_window_apps, chat_id, window["id"], window["app_name"])

    @staticmethod
    def _attach_screenshot(invocation: Invocation, data, chat_id: int) -> dict:
        from koddi.attachments.service import store_attachment
        from PIL import Image

        path = invocation.screenshot_path
        if path is None or not path.is_file() or path.is_symlink():
            raise ValueError("agent-desktop не сохранил скриншот")
        if path.stat().st_size > 40_000_000:
            raise ValueError("Слишком большой скриншот")
        with Image.open(path) as image:
            if image.format != "PNG" or image.width * image.height > 80_000_000:
                raise ValueError("Некорректный скриншот")
            image.load()
            width, height = image.width, image.height
            raw = path.read_bytes()
            # крупные экраны сжимаем, чтобы вложение оставалось лёгким
            if len(raw) > 3_000_000 or width * height > 8_000_000:
                scaled = image.copy()
                scaled.thumbnail((2000, 2000))
                buffer = io.BytesIO()
                scaled.save(buffer, format="PNG", optimize=True)
                raw = buffer.getvalue()
                if len(raw) > 8_000_000:
                    raise ValueError("Слишком большой скриншот")
        attachment = store_attachment(chat_id, "computer-observation.png", io.BytesIO(raw))
        result = {
            key: value for key, value in (data if isinstance(data, dict) else {}).items() if key not in {"path", "base64", "png_base64"}
        }
        result.update(
            attachment_id=attachment["id"],
            image_url=f"/api/attachments/{attachment['id']}/content",
            width=width,
            height=height,
            vision_transport="not_confirmed",
        )
        return result


def _cleanup(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
        path.parent.rmdir()
    except OSError:
        pass

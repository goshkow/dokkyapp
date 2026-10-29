"""Автообновление Докки по зеркалу dokky.goshkow.com.

Сервер раздаёт `latest.json` (версия, имя, размер и SHA-256 установочного файла каждой платформы) и сами файлы
со своего домена; файлы он сверяет с SHA256SUMS релиза. Приложение:

1. проверяет `latest.json` через минуту после запуска, затем раз в 6 часов и по кнопке;
2. качает установочный файл с докачкой, сверяет размер и SHA-256 и только после этого считает его готовым;
3. готовит новую версию рядом с текущей, пока приложение работает (macOS: пакет из DMG, проверенный codesign
   и Info.plist; Windows portable: папка app-<версия> из архива);
4. по кнопке «Перезапустить и обновить» запускает скрытого помощника и выходит. Помощник ждёт завершения
   процесса, переключает версию, запускает новую и ждёт от неё отметки готовности; без отметки за две минуты
   возвращает прежнюю версию и запоминает неудачную, чтобы не предлагать её снова.

Обращения идут только к ORIGIN; адрес не берётся ни из ответа сервера, ни из настроек.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import plistlib
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from koddi import __version__
from koddi.config import app_dir, data_dir, is_packaged_build, launcher_path
from koddi.dokky.client import ORIGIN
from koddi.storage.db import get_setting, set_setting

LOGGER = logging.getLogger("koddi.updates")

LATEST_PATH = "/api/v1/updates/latest.json"
DOWNLOAD_PREFIX = "/api/v1/updates/download/"
BUNDLE_ID = "com.goshkow.dokky"
VERSION_RE = re.compile(r"^(\d{1,4})\.(\d{1,4})\.(\d{1,6})(?:-beta\.(\d{1,4}))?$")
NAME_RE = re.compile(r"^Dokky-\d+\.\d+\.\d+(?:-beta\.\d+)?-[A-Za-z0-9_-]+\.(?:dmg|exe|zip)$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_SIZE = 4 * 1024**3
EXTENSIONS = {"dmg": ".dmg", "setup": ".exe", "portable": ".zip"}
FIRST_CHECK = (1, 4)
CHECK_EVERY = 6 * 3600
MANUAL_GAP = 10
READY_SECONDS = 120
CHUNK = 1024 * 1024


def parse_version(value: str) -> tuple[int, int, int, int] | None:
    """Бета меньше релиза той же версии: 0.3.0-beta.2 < 0.3.0."""
    match = VERSION_RE.fullmatch(value or "")
    if not match:
        return None
    major, minor, patch, beta = match.groups()
    return int(major), int(minor), int(patch), int(beta) if beta is not None else 1_000_000


def is_newer(candidate: str, current: str) -> bool:
    left, right = parse_version(candidate), parse_version(current)
    return bool(left and right and left > right)


def platform_id() -> str | None:
    if sys.platform == "darwin":
        # Intel-сборок нет: обновлять нечем
        return "macos-arm64" if platform.machine() == "arm64" else None
    if os.name == "nt":
        return "windows-x86_64"
    return None


def _windows_root() -> Path | None:
    launcher = launcher_path()
    return launcher.parent if launcher else None


def _program_files(path: Path) -> bool:
    folders = [os.environ.get(name) for name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")]
    full = str(path.resolve()).lower().rstrip("\\") + "\\"
    return any(folder and full.startswith(folder.lower().rstrip("\\") + "\\") for folder in folders)


def macos_bundle() -> Path | None:
    if sys.platform != "darwin" or not is_packaged_build():
        return None
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


def install_kind() -> tuple[str | None, str]:
    """(вид файла на сервере, способ установки) или (None, причина)."""
    if not is_packaged_build():
        return None, "dev"
    if sys.platform == "darwin":
        bundle = macos_bundle()
        if bundle is None:
            return None, "unsupported"
        text = str(bundle)
        if text.startswith("/Volumes/") or "/AppTranslocation/" in text:
            return "dmg", "move_to_applications"
        if not os.access(bundle.parent, os.W_OK):
            return "dmg", "manual"
        return "dmg", "bundle"
    if os.name == "nt":
        root = _windows_root()
        if root is None:
            return "setup", "manual"
        if (root / "portable.dokky").is_file():
            return ("portable", "portable") if os.access(root, os.W_OK) else ("portable", "manual")
        if _program_files(root) or not os.access(root, os.W_OK):
            return "setup", "setup_elevated"
        return "setup", "setup"
    return None, "unsupported"


def updates_dir() -> Path:
    path = data_dir() / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ready_file() -> Path:
    return updates_dir() / "ready.json"


def _failed_file() -> Path:
    return updates_dir() / "failed.json"


def _hidden_flags() -> int:
    # помощник переживает выход приложения и не показывает окна: CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    return 0x08000000 | 0x00000200


class UpdateError(Exception):
    def __init__(self, message: str, code: str = "failed"):
        super().__init__(message)
        self.code = code


class Updater:
    transport: httpx.AsyncBaseTransport | None = None

    def __init__(self) -> None:
        self.state: dict[str, Any] = {"status": "idle", "progress": None, "error": "", "available": None}
        self._task: asyncio.Task | None = None
        self._download: asyncio.Task | None = None
        self._last_manual = 0.0
        self._lock = asyncio.Lock()
        # подготовка идёт в потоке и не отменяется: вторая не должна писать в ту же папку
        self._prepare_lock = threading.Lock()
        self._install_lock = threading.Lock()
        self.prepared: dict[str, Any] | None = None

    # настройки и состояние
    def prefs(self) -> dict:
        value = get_setting("updates", {})
        return value if isinstance(value, dict) else {}

    def _save(self, **values: Any) -> None:
        set_setting("updates", {**self.prefs(), **values})

    def status(self) -> dict:
        kind, method = install_kind()
        prefs = self.prefs()
        disabled = {"status": "disabled", "reason": method} if kind is None else {}
        return {
            "current": __version__,
            "platform": platform_id(),
            "kind": kind,
            "method": method,
            "auto_download": prefs.get("auto_download") is True,
            "checked_at": prefs.get("checked_at"),
            "skipped": prefs.get("skipped"),
            **self.state,
            **disabled,
        }

    async def _publish(self) -> None:
        from koddi.events import event_bus

        await event_bus.publish("update.state", self.status())

    async def _set(self, **values: Any) -> None:
        self.state.update(values)
        await self._publish()

    def _client(self) -> httpx.AsyncClient:
        headers = {"User-Agent": f"Dokky/{__version__} ({platform_id() or 'unknown'})"}
        return httpx.AsyncClient(
            base_url=ORIGIN, transport=self.transport, headers=headers,
            timeout=httpx.Timeout(20, read=60), follow_redirects=False,
        )

    # жизненный цикл
    async def start(self) -> None:
        # сначала отметка готовности: помощник обновления ждёт её, чтобы не откатывать новую версию
        self.mark_ready()
        self.cleanup()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="dokky-updates")

    async def stop(self) -> None:
        for task in (self._task, self._download):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (self._task, self._download) if task), return_exceptions=True)

    async def _loop(self) -> None:
        if install_kind()[1] == "dev" or platform_id() is None:
            return
        await asyncio.sleep(secrets.randbelow(FIRST_CHECK[1] - FIRST_CHECK[0]) + FIRST_CHECK[0])
        delay = 300
        while True:
            try:
                await self.check()
                delay = 300
                await asyncio.sleep(CHECK_EVERY + secrets.randbelow(1800))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - фоновая проверка не показывает ошибки, только повторяет позже
                await asyncio.sleep(delay)
                delay = min(delay * 3, CHECK_EVERY)

    def mark_ready(self) -> None:
        """Новая версия запустилась: помощник видит это и не откатывает её."""
        if not is_packaged_build():
            return
        try:
            _ready_file().write_text(json.dumps({"version": __version__, "pid": os.getpid(), "at": time.time()}), encoding="utf-8")
        except OSError:
            pass

    def cleanup(self) -> None:
        """Убирает скачанное и подготовленное для версий не новее текущей и брошенные копии пакета."""
        if not is_packaged_build():
            return
        try:
            folder = updates_dir()
        except OSError:
            return
        for item in folder.iterdir():
            match = re.match(r"^Dokky-(\d+\.\d+\.\d+(?:-beta\.\d+)?)-", item.name)
            if match and not is_newer(match.group(1), __version__):
                shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink(missing_ok=True)
        bundle = macos_bundle()
        if bundle is not None:
            for item in bundle.parent.glob(".Dokky-*.app"):
                if item.name.startswith(".Dokky-previous-"):
                    # прежнюю версию убирает помощник после успешного запуска; здесь только брошенные надолго
                    try:
                        stale = time.time() - item.lstat().st_ctime > 86400
                    except OSError:
                        stale = False
                    if stale:
                        shutil.rmtree(item, ignore_errors=True)
                    continue
                version = item.name.removeprefix(".Dokky-update-").removesuffix(".app")
                if not is_newer(version, __version__):
                    shutil.rmtree(item, ignore_errors=True)
        root = _windows_root()
        if root is not None:
            keep = {app_dir().name}
            try:
                previous = json.loads((root / "current.json").read_text(encoding="utf-8")).get("previous")
            except (OSError, ValueError, AttributeError):
                previous = None
            # лаунчер возвращается к previous, если новая версия не стартует: эту папку не трогаем
            if isinstance(previous, str):
                keep.add(previous)
            for item in root.glob("app-*.update"):
                shutil.rmtree(item, ignore_errors=True)
            for item in root.glob("app-*"):
                version = item.name.removeprefix("app-")
                if item.is_dir() and item.name not in keep and parse_version(version) and not is_newer(version, __version__):
                    shutil.rmtree(item, ignore_errors=True)

    # проверка
    def _entry(self, payload: Any, kind: str) -> dict:
        try:
            entry = payload["stable"][platform_id()][kind]
        except (KeyError, TypeError) as exc:
            raise UpdateError("Сервер обновлений пока не знает о сборке для этой системы", "no_build") from exc
        if not isinstance(entry, dict):
            raise UpdateError("Сервер обновлений ответил непонятно", "bad_response")
        version, name, size, digest, url = (entry.get(key) for key in ("version", "name", "size", "sha256", "url"))
        if not (
            isinstance(version, str) and parse_version(version)
            and isinstance(name, str) and NAME_RE.fullmatch(name) and name.startswith(f"Dokky-{version}-")
            and name.lower().endswith(EXTENSIONS.get(kind, "\0"))
            and type(size) is int and 0 < size <= MAX_SIZE
            and isinstance(digest, str) and SHA_RE.fullmatch(digest)
            and isinstance(url, str) and url.startswith(DOWNLOAD_PREFIX) and "//" not in url and ".." not in url
        ):
            raise UpdateError("Сервер обновлений ответил непонятно", "bad_response")
        return {"version": version, "name": name, "size": size, "sha256": digest, "url": url}

    async def check(self, *, manual: bool = False) -> dict:
        kind, method = install_kind()
        if kind is None:
            if manual:
                raise UpdateError(
                    "Обновления проверяются только у установленной программы" if method == "dev"
                    else "Для этой системы обновлений нет",
                    method,
                )
            return self.status()
        if manual:
            if time.monotonic() - self._last_manual < MANUAL_GAP:
                return self.status()
            self._last_manual = time.monotonic()
        if self.state["status"] in {"downloading", "preparing", "installing"}:
            return self.status()
        previous = self.state["status"]
        await self._set(status="checking", error="")
        try:
            async with self._client() as client:
                response = await client.get(LATEST_PATH)
            if response.status_code != 200:
                raise UpdateError("Сервер обновлений недоступен, попробуйте позже", "unavailable")
            entry = self._entry(response.json(), kind)
        except (httpx.HTTPError, ValueError) as exc:
            await self._set(status=previous if previous not in {"checking"} else "idle", error="Нет связи с сервером обновлений" if manual else "")
            raise UpdateError("Нет связи с сервером обновлений", "network") from exc
        except UpdateError as exc:
            # сбой проверки не должен прятать уже найденное или скачанное обновление
            kept = previous if previous in {"available", "skipped", "ready", "up_to_date"} else ("error" if manual else "idle")
            await self._set(status=kept, error=str(exc) if manual else "")
            raise
        self._save(checked_at=time.time())
        failed = self._failed()
        prefs = self.prefs()
        if not is_newer(entry["version"], __version__):
            await self._set(status="up_to_date", available=None)
            return self.status()
        if entry["version"] == failed and not manual:
            await self._set(status="up_to_date", available=None)
            return self.status()
        if prefs.get("skipped") == entry["version"] and not manual:
            await self._set(status="skipped", available=entry)
            return self.status()
        ready = self.prepared and self.prepared.get("version") == entry["version"]
        await self._set(status="ready" if ready else "available", available=entry)
        if not ready and method in {"bundle", "portable", "setup", "setup_elevated"} and prefs.get("auto_download") is True:
            self.start_download()
        return self.status()

    def _failed(self) -> str | None:
        try:
            value = json.loads(_failed_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value.get("version") if isinstance(value, dict) else None

    # загрузка
    def start_download(self) -> None:
        if self._download and not self._download.done():
            return
        self._download = asyncio.create_task(self._run_download(), name="dokky-update-download")

    async def cancel_download(self) -> None:
        # подготовку и установку не прерываем: поток подготовки всё равно доработает, а установка уже пошла
        if self.state.get("status") != "downloading":
            return
        if self._download and not self._download.done():
            self._download.cancel()
            await asyncio.gather(self._download, return_exceptions=True)
        await self._set(status="available" if self.state.get("available") else "idle", progress=None)

    async def _run_download(self) -> None:
        entry = self.state.get("available")
        if not entry:
            return
        try:
            await self._set(status="downloading", error="", progress={"done": 0, "total": entry["size"]})
            path = await self.download(entry)
            await self._set(status="preparing", progress=None)
            self.prepared = await asyncio.to_thread(self.prepare, entry, path)
            await self._set(status="ready", progress=None)
            from koddi.events import event_bus

            await event_bus.publish("update.ready", {"version": entry["version"]})
        except asyncio.CancelledError:
            raise
        except UpdateError as exc:
            await self._set(status="error", error=str(exc), progress=None)
        except OSError as exc:
            LOGGER.warning("Обновление не подготовлено: %s", type(exc).__name__)
            message = "Не хватает места на диске" if getattr(exc, "errno", None) == 28 else "Не удалось сохранить обновление"
            await self._set(status="error", error=message, progress=None)
        except httpx.HTTPError:
            await self._set(status="error", error="Загрузка прервалась. Повторите, она продолжится с того же места", progress=None)
        except Exception as exc:  # noqa: BLE001 - иначе состояние навсегда застрянет на «Подготовка»
            LOGGER.warning("Обновление не подготовлено: %s", type(exc).__name__)
            await self._set(status="error", error="Не удалось подготовить обновление. Попробуйте ещё раз", progress=None)

    async def download(self, entry: dict) -> Path:
        folder = updates_dir()
        target = folder / entry["name"]
        if target.is_file() and target.stat().st_size == entry["size"] and await asyncio.to_thread(_sha256, target) == entry["sha256"]:
            return target
        free = shutil.disk_usage(folder).free
        if free < entry["size"] * 3:
            need = (entry["size"] * 3 - free) // (1024 * 1024) + 1
            raise UpdateError(f"Не хватает места на диске: освободите ещё {need} МБ", "no_space")
        partial = folder / (entry["name"] + ".part")
        offset = partial.stat().st_size if partial.is_file() else 0
        if offset > entry["size"]:
            partial.unlink()
            offset = 0
        url = entry["url"]
        async with self._client() as client:
            for _redirect in range(3):
                if offset == entry["size"]:
                    break
                headers = {"Range": f"bytes={offset}-"} if offset else {}
                async with client.stream("GET", url, headers=headers) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location", "")
                        target_url = urlsplit(location)
                        # перенаправление только в пределах своего сервера
                        if target_url.netloc and target_url.netloc != urlsplit(ORIGIN).netloc:
                            raise UpdateError("Сервер обновлений перенаправил на чужой адрес", "bad_redirect")
                        url = target_url.path + (f"?{target_url.query}" if target_url.query else "")
                        continue
                    if response.status_code == 416:
                        partial.unlink(missing_ok=True)
                        offset = 0
                        continue
                    if response.status_code == 200:
                        offset = 0
                    elif response.status_code != 206:
                        raise UpdateError("Сервер обновлений не отдал файл, попробуйте позже", "unavailable")
                    with partial.open("ab" if offset else "wb") as sink:
                        done = offset
                        last = 0.0
                        async for chunk in response.aiter_bytes(CHUNK):
                            done += len(chunk)
                            if done > entry["size"]:
                                raise UpdateError("Файл обновления больше заявленного", "size_mismatch")
                            sink.write(chunk)
                            if time.monotonic() - last > 0.25:
                                last = time.monotonic()
                                await self._set(progress={"done": done, "total": entry["size"]})
                    break
            else:
                raise UpdateError("Сервер обновлений перенаправляет слишком много раз", "bad_redirect")
        if partial.stat().st_size != entry["size"]:
            raise UpdateError("Загрузка прервалась. Повторите, она продолжится с того же места", "incomplete")
        if await asyncio.to_thread(_sha256, partial) != entry["sha256"]:
            partial.unlink(missing_ok=True)
            raise UpdateError("Файл обновления повреждён или подменён и удалён. Повторите загрузку", "hash_mismatch")
        os.replace(partial, target)
        return target

    # подготовка новой версии рядом с текущей
    def prepare(self, entry: dict, path: Path) -> dict:
        with self._prepare_lock:
            return self._prepare(entry, path)

    def _prepare(self, entry: dict, path: Path) -> dict:
        kind, method = install_kind()
        if method == "bundle":
            return {"version": entry["version"], "method": method, "stage": str(_prepare_bundle(path, entry["version"])), "file": str(path)}
        if method == "portable":
            return {"version": entry["version"], "method": method, "stage": str(_prepare_portable(path, entry["version"])), "file": str(path)}
        return {"version": entry["version"], "method": method, "file": str(path)}

    # установка
    def install(self, *, busy: bool = False) -> dict:
        # два быстрых нажатия не должны запустить два помощника
        if not self._install_lock.acquire(blocking=False):
            raise UpdateError("Обновление уже устанавливается", "installing")
        try:
            prepared = self.prepared
            if not prepared or self.state.get("status") != "ready":
                raise UpdateError("Обновление ещё не готово", "not_ready")
            self.state["status"] = "installing"
            try:
                self._verify_prepared(prepared)
                self._launch(prepared)
            except BaseException:
                # подготовленное оказалось негодным: предлагаем скачать заново, а не перезапуск
                self.state["status"] = "ready" if self.prepared else "available"
                raise
            _quit_signal().write_text(str(time.time()), encoding="utf-8")
            return {"ok": True, "quit": True}
        finally:
            self._install_lock.release()

    def _verify_prepared(self, prepared: dict) -> None:
        """Перед запуском ещё раз проверяем то, что запустим: файл мог смениться после загрузки."""
        entry = self.state.get("available") or {}
        if prepared["method"] == "bundle":
            if _run(["/usr/bin/codesign", "--verify", "--deep", "--strict", *_REQUIREMENT, prepared["stage"]]).returncode != 0:
                self.prepared = None
                raise UpdateError("Подготовленная версия изменилась. Скачайте обновление заново", "bad_signature")
        elif prepared["method"] == "portable":
            if not (Path(prepared["stage"]) / "DokkyApp.exe").is_file():
                self.prepared = None
                raise UpdateError("Подготовленная версия пропала. Скачайте обновление заново", "missing")
        elif entry.get("version") != prepared["version"] or _sha256(Path(prepared["file"])) != entry.get("sha256"):
            self.prepared = None
            raise UpdateError("Установщик изменился после загрузки. Скачайте обновление заново", "hash_mismatch")

    @staticmethod
    def _launch(prepared: dict) -> None:
        method = prepared["method"]
        log = updates_dir() / "install.log"
        if method == "bundle":
            _spawn_posix(_macos_script(Path(prepared["stage"]), prepared["version"], log))
        elif method == "portable":
            _spawn_windows(_windows_portable_script(Path(prepared["stage"]), prepared["version"], log))
        elif method in {"setup", "setup_elevated"}:
            _spawn_windows(_windows_setup_script(Path(prepared["file"]), prepared["version"], log, silent=method == "setup"))
        else:
            raise UpdateError("Эту версию нужно обновить вручную: скачайте установщик на сайте", "manual")


# подпись пока ad-hoc, без Team ID: требуем хотя бы свой идентификатор подписи
_REQUIREMENT = (f'-R=identifier "{BUNDLE_ID}"',)


def _quit_signal() -> Path:
    return data_dir() / "dokky-quit.signal"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(command: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


def _prepare_bundle(dmg: Path, version: str) -> Path:
    """Пакет из DMG проверяется (идентификатор, версия, подпись) и копируется рядом с текущим."""
    bundle = macos_bundle()
    if bundle is None:
        raise UpdateError("Не найден пакет приложения", "unsupported")
    mount = Path(tempfile.mkdtemp(prefix="dokky-update-"))
    attached = False
    try:
        result = _run(["/usr/bin/hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", str(mount), str(dmg)])
        if result.returncode != 0:
            raise UpdateError("Не удалось открыть образ обновления", "bad_image")
        attached = True
        candidates = [item for item in mount.iterdir() if item.suffix == ".app" and not item.is_symlink()]
        source = None
        for item in candidates:
            try:
                info = plistlib.loads((item / "Contents" / "Info.plist").read_bytes())
            except (OSError, ValueError, plistlib.InvalidFileException):
                continue
            if info.get("CFBundleIdentifier") == BUNDLE_ID and info.get("CFBundleShortVersionString") == version:
                source = item
                break
        if source is None:
            raise UpdateError("В образе нет Докки нужной версии", "bad_image")
        if _run(["/usr/bin/codesign", "--verify", "--deep", "--strict", *_REQUIREMENT, str(source)]).returncode != 0:
            raise UpdateError("Подпись новой версии не прошла проверку", "bad_signature")
        stage = bundle.parent / f".Dokky-update-{version}.app"
        if stage.exists():
            shutil.rmtree(stage)
        if _run(["/usr/bin/ditto", str(source), str(stage)], timeout=900).returncode != 0:
            shutil.rmtree(stage, ignore_errors=True)
            raise UpdateError("Не удалось подготовить новую версию: проверьте место на диске", "copy_failed")
        _run(["/usr/bin/xattr", "-dr", "com.apple.quarantine", str(stage)])
        if _run(["/usr/bin/codesign", "--verify", "--deep", "--strict", *_REQUIREMENT, str(stage)]).returncode != 0:
            shutil.rmtree(stage, ignore_errors=True)
            raise UpdateError("Новая версия повредилась при копировании", "bad_signature")
        return stage
    finally:
        if attached:
            _run(["/usr/bin/hdiutil", "detach", str(mount), "-force"], timeout=60)
        shutil.rmtree(mount, ignore_errors=True)


def _prepare_portable(archive: Path, version: str) -> Path:
    """Папка app-<версия> из архива переносной версии кладётся рядом с текущей, current.json пока не трогаем."""
    root = _windows_root()
    if root is None:
        raise UpdateError("Не найдена папка переносной версии", "unsupported")
    name = f"app-{version}"
    final = root / name
    if final.is_dir() and (final / "DokkyApp.exe").is_file():
        return final
    stage = root / f"{name}.update"
    shutil.rmtree(stage, ignore_errors=True)
    prefix = f"Dokky/{name}/"
    with zipfile.ZipFile(archive) as source:
        members = [item for item in source.infolist() if item.filename.startswith(prefix)]
        if not any(item.filename == prefix + "DokkyApp.exe" for item in members):
            raise UpdateError("В архиве нет новой версии", "bad_archive")
        for item in members:
            relative = item.filename[len(prefix):]
            if not relative:
                continue
            target = (stage / relative).resolve()
            if stage.resolve() not in target.parents:
                raise UpdateError("Архив обновления повреждён", "bad_archive")
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(item) as reader, target.open("wb") as writer:
                shutil.copyfileobj(reader, writer, CHUNK)
    shutil.rmtree(final, ignore_errors=True)
    os.replace(stage, final)
    return final


def _macos_script(stage: Path, version: str, log: Path, *, pid: int | None = None, opener: str = "/usr/bin/open",
                  limit: int = READY_SECONDS) -> str:
    bundle = macos_bundle()
    assert bundle is not None
    previous = bundle.parent / f".Dokky-previous-{secrets.token_hex(4)}.app"
    values = {
        "PID": str(pid or os.getpid()), "OPEN": opener, "APP": str(bundle), "STAGE": str(stage), "OLD": str(previous),
        "VERSION": version, "READY": str(_ready_file()), "FAILED": str(_failed_file()), "LOG": str(log),
        "LIMIT": str(limit),
    }
    header = "\n".join(f"{key}={shlex.quote(value)}" for key, value in values.items())
    return header + r'''
exec >>"$LOG" 2>&1
echo "$(date) обновление до $VERSION"
i=0
while kill -0 "$PID" 2>/dev/null; do
  sleep 0.5; i=$((i+1))
  [ "$i" -gt 1200 ] && { echo "приложение не закрылось"; exit 1; }
done
rm -f "$READY"
mv "$APP" "$OLD" || { echo "не удалось убрать прежнюю версию"; "$OPEN" "$APP"; exit 1; }
if ! mv "$STAGE" "$APP"; then mv "$OLD" "$APP"; "$OPEN" "$APP"; echo "не удалось поставить новую версию"; exit 1; fi
"$OPEN" "$APP"
i=0
while [ "$i" -lt "$LIMIT" ]; do
  if grep -q "\"version\": \"$VERSION\"" "$READY" 2>/dev/null; then
    rm -rf "$OLD"; echo "готово"; exit 0
  fi
  sleep 1; i=$((i+1))
done
echo "новая версия не запустилась, возвращаю прежнюю"
# путь ищем как строку, а не как шаблон: в имени папки бывают скобки и плюсы
ps -axo pid=,command= | grep -F "$APP/Contents/MacOS/" | grep -v grep | awk '{print $1}' | xargs kill 2>/dev/null
sleep 2
[ -d "$OLD" ] || { echo "прежней версии нет, оставляю новую"; printf '{"version": "%s"}' "$VERSION" >"$FAILED"; exit 1; }
rm -rf "$APP" && mv "$OLD" "$APP"
printf '{"version": "%s"}' "$VERSION" >"$FAILED"
"$OPEN" "$APP"
'''


def _cmd_quote(value: str) -> str:
    if '"' in value or "%" in value or "\n" in value:
        raise UpdateError("Путь установки содержит недопустимые символы", "bad_path")
    return f'"{value}"'


def _windows_wait(pid: int) -> str:
    # tasklist и ping работают в скрытой консоли; timeout без ввода с клавиатуры не работает
    return (
        ":wait\n"
        f'tasklist /FI "PID eq {pid}" /NH | find "{pid}" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)\n'
    )


def _windows_portable_script(stage: Path, version: str, log: Path) -> str:
    root = _windows_root()
    assert root is not None
    current = json.loads((root / "current.json").read_text(encoding="utf-8"))
    payload = json.dumps({"v": 1, "version": version, "dir": f"app-{version}", "previous": current.get("dir")})
    (root / "current.json.update").write_text(payload + "\n", encoding="utf-8")
    return (
        "@echo off\n"
        + _windows_wait(os.getpid())
        + f"move /Y {_cmd_quote(str(root / 'current.json.update'))} {_cmd_quote(str(root / 'current.json'))} >>{_cmd_quote(str(log))} 2>&1\n"
        + f"start \"\" {_cmd_quote(str(root / 'Dokky.exe'))}\n"
    )


def _windows_setup_script(setup: Path, version: str, log: Path, *, silent: bool) -> str:
    root = _windows_root()
    launcher = root / "Dokky.exe" if root else None
    lines = ["@echo off", _windows_wait(os.getpid()).rstrip("\n")]
    if silent:
        lines.append(f"{_cmd_quote(str(setup))} --silent --log {_cmd_quote(str(log))}")
        lines.append(f"if errorlevel 1 (echo {{\"version\": \"{version}\"}}>{_cmd_quote(str(_failed_file()))})")
        if launcher:
            lines.append(f"start \"\" {_cmd_quote(str(launcher))}")
    else:
        # установка для всех пользователей: окно установщика само попросит права администратора
        lines.append(f"start \"\" {_cmd_quote(str(setup))}")
    return "\n".join(lines) + "\n"


def _spawn_posix(script: str) -> None:
    path = Path(tempfile.mkdtemp(prefix="dokky-updater-")) / "update.sh"
    path.write_text(script, encoding="utf-8")
    subprocess.Popen(
        ["/bin/sh", str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, close_fds=True,
    )


def _spawn_windows(script: str) -> None:
    path = Path(tempfile.mkdtemp(prefix="dokky-updater-")) / "update.cmd"
    # кодовую страницу меняет сам скрипт до строк с путями; /s снимает только внешние кавычки,
    # поэтому скобки и амперсанды в имени папки пользователя не ломают запуск
    path.write_bytes(script.replace("@echo off\n", "@echo off\nchcp 65001 >nul\n", 1).encode("utf-8"))
    command = f'cmd.exe /d /s /c "{_cmd_quote(str(path))}"'
    try:
        # помощник не должен закрыться вместе с заданием, в котором живёт приложение
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=_hidden_flags() | 0x01000000, close_fds=True)
    except OSError:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=_hidden_flags(), close_fds=True)


updater = Updater()

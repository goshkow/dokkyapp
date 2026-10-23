"""Мост с сервером Докки: регистрация, heartbeat, долгий опрос команд, отправка outbox и исполнение команд.

Компьютер нигде не слушает порт: только исходящие HTTPS-запросы с токеном аккаунта. Регистрация - при запуске
приложения и только тогда (сервер считает её перезапуском и закрывает оборванные ходы), поэтому сбои сети
переживаются повтором с паузой, а не новой регистрацией.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import random
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from functools import wraps
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import HTTPException

from koddi.logs import log_event
from koddi.remote_control import api, outbox, power
from koddi.remote_control.api import RemoteFailure
from koddi.remote_control.mirror import Mirror
from koddi.remote_control.redact import MESSAGE_CHARS, clean_text
from koddi.storage.db import db

HEARTBEAT_SECONDS = 15
POLL_WAIT = 25
SEND_DELAY = 0.15
SEND_NOW = 50
SHARE_LIMIT = 150
HISTORY_LIMIT = 400
# дальше в очереди без сети остаются только итоги: сообщения, карточки, план, запросы
NEW_SESSIONS_PER_HOUR = 20
PREVENT_SLEEP = {"off", "working", "on_ac"}
NAME_LIMIT = 60


class Revoked(Exception):
    """Компьютер отключён в вебе (410 host_revoked)."""


class LoginRequired(Exception):
    """Нет входа в Докки или сессия отозвана."""


class Fatal(Exception):
    """Сервер не принял компьютер (например, 409 too_many_hosts): повтор не поможет."""


@dataclass
class Hooks:
    start_turn: Callable[[int, str], Awaitable[None]]
    cancel_turn: Callable[[int], Awaitable[bool]]
    respond: Callable[[int, dict], dict]
    create_chat: Callable[[int | None, str], Awaitable[dict]]


def _best_effort(function):
    """Хук окна: сбой моста пишется в журнал и не ломает создание, переименование или удаление чата."""

    @wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except LoginRequired:
            return None
        except Exception as exc:  # noqa: BLE001
            log_event("WARNING", "remote.hook", type(exc).__name__, {"hook": function.__name__})
            return None

    return wrapper


def _owner() -> str:
    from koddi.policy.account import local_owner

    try:
        return local_owner()
    except HTTPException as exc:
        raise LoginRequired(str(exc.detail)) from exc


def _computer_name() -> str:
    name = ""
    if sys.platform == "darwin":
        try:
            name = subprocess.run(["/usr/sbin/scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=3).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            name = ""
    if not name:
        name = os.environ.get("COMPUTERNAME", "") if os.name == "nt" else ""
    if not name:
        name = platform.node().split(".", 1)[0]
    name = " ".join(name.split())[:NAME_LIMIT]
    return name or "Компьютер"


def _platform() -> str:
    return "macos" if sys.platform == "darwin" else "windows" if os.name == "nt" else "linux"


def _app_version() -> str:
    try:
        from koddi.installer.service import BUILD_NUMBER

        return str(BUILD_NUMBER)[:40]
    except Exception:  # noqa: BLE001 - версия для показа, без неё регистрация не ломается
        from koddi import __version__

        return __version__


def _iso(value: Any) -> str | None:
    """SQLite хранит «YYYY-MM-DD HH:MM:SS» в UTC; сервер ждёт ISO 8601."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.isoformat().replace("+00:00", "Z")


def _meta(value: Any) -> dict:
    try:
        meta = json.loads(value or "{}")
    except ValueError:
        return {}
    return meta if isinstance(meta, dict) else {}


class RemoteControl:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.hooks: Hooks | None = None
        self.supervisor: asyncio.Task | None = None
        self.state = "off"
        self.message = ""
        self.host: dict = {}
        self.registered_for = ""
        self.commands: dict[int, dict] = {}
        self._created: list[float] = []
        self._tasks: set[asyncio.Task] = set()
        self.ack_pauses: tuple[float, ...] = (1, 3, 8)
        self.mirrors: dict[int, asyncio.Task] = {}
        self.guard = power.SleepGuard()
        self.sleeping = False
        self.power = "unknown"
        self._send = asyncio.Event()
        self._beat = asyncio.Event()
        self._poll: asyncio.Task | None = None
        self._sleep_watch: object | None = None
        self._enabled = False

    # настройки
    def config(self) -> dict:
        with db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO remote_control(id, install_id) VALUES (1, ?)",
                (secrets.token_urlsafe(24),),
            )
            row = conn.execute("SELECT * FROM remote_control WHERE id=1").fetchone()
        return dict(row)

    def _update(self, **values: Any) -> None:
        self.config()
        with db() as conn:
            conn.execute(
                "UPDATE remote_control SET " + ", ".join(f"{key}=?" for key in values) + " WHERE id=1",
                tuple(values.values()),
            )

    def bind(self, hooks: Hooks) -> None:
        self.hooks = hooks

    def status(self) -> dict:
        config = self.config()
        with db() as conn:
            rows = conn.execute("SELECT chat_id, shared FROM remote_sessions").fetchall()
        return {
            "enabled": bool(config["enabled"]),
            "state": self.state if config["enabled"] else "off",
            "message": self.message if config["enabled"] else "",
            "name": self.host.get("name") or config["name"] or _computer_name(),
            "prevent_sleep": config["prevent_sleep"],
            "prevent_sleep_active": self.guard.active,
            "power": self.power,
            "pending": outbox.count(),
            "new_session_project_id": config["new_session_project_id"],
            "shared": {str(row["chat_id"]): bool(row["shared"]) for row in rows},
            "site": api.dokky_origin(),
        }

    async def configure(self, payload: dict) -> dict:
        if "prevent_sleep" in payload:
            if payload["prevent_sleep"] not in PREVENT_SLEEP:
                raise HTTPException(422, "Неизвестный режим запрета сна")
            self._update(prevent_sleep=payload["prevent_sleep"])
            self._beat.set()
        if "new_session_project_id" in payload:
            value = payload["new_session_project_id"]
            if value is not None and (type(value) is not int or not self._project(value)):
                raise HTTPException(404, "Проект не найден")
            self._update(new_session_project_id=value)
        if "enabled" in payload:
            if not isinstance(payload["enabled"], bool):
                raise HTTPException(422, "enabled: true или false")
            if payload["enabled"]:
                _owner()
                self._update(enabled=1)
                self._enabled = True
                self.launch()
            else:
                self._update(enabled=0)
                self._enabled = False
                await self.halt()
        return self.status()

    async def reconnect(self) -> dict:
        if not self.config()["enabled"]:
            raise HTTPException(409, "Сначала включите доступ с телефона")
        await self.halt()
        # повторная регистрация с тем же install_id возвращает компьютер с теми же чатами
        self.registered_for = ""
        self.launch()
        return self.status()

    # жизненный цикл
    async def start(self) -> None:
        from koddi.agents.runs import agent_runs

        self.loop = asyncio.get_running_loop()
        self._send = asyncio.Event()
        self._beat = asyncio.Event()
        if self.on_run not in agent_runs.listeners:
            agent_runs.listeners.append(self.on_run)
        self._enabled = bool(self.config()["enabled"])
        if self._enabled:
            self.launch()

    def launch(self) -> None:
        if self.supervisor and not self.supervisor.done():
            return
        self.state, self.message = "connecting", ""
        self.supervisor = asyncio.get_running_loop().create_task(self.supervise(), name="dokky-remote-control")

    async def halt(self) -> None:
        task, self.supervisor = self.supervisor, None
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.guard.hold(False)
        power.unwatch_macos_sleep(self._sleep_watch)
        self._sleep_watch = None

    async def shutdown(self) -> None:
        from koddi.agents.runs import agent_runs

        if self.on_run in agent_runs.listeners:
            agent_runs.listeners.remove(self.on_run)
        mirrors = [task for task in self.mirrors.values() if not task.done()]
        if mirrors:
            await asyncio.wait(mirrors, timeout=2)
        if self.state == "online" and self.host.get("id"):
            # последнее (конец хода после закрытия окна) - коротко, не задерживая выход
            try:
                await asyncio.wait_for(self.drain(), timeout=3)
            except Exception:  # noqa: BLE001
                pass
        await self.halt()

    async def supervise(self) -> None:
        backoff = 1.0
        while True:
            try:
                owner = _owner()
                self.adopt_account(owner)
                if self.registered_for != owner:
                    await self.register()
                    self.registered_for = owner
                self.state, self.message = "online", ""
                backoff = 1.0
                await self.publish_status()
                await self.sync_sessions()
                if self._sleep_watch is None:
                    self._sleep_watch = power.watch_macos_sleep(self.sleep_notice, self.wake_notice)
                await self.run_loops()
            except asyncio.CancelledError:
                raise
            except Revoked:
                self.state, self.message = "revoked", "Доступ с телефона отключён в вебе. Подключить снова?"
                self.registered_for = ""
                self.guard.hold(False)
                await self.publish_status()
                return
            except Fatal as exc:
                self.state, self.message = "error", str(exc)
                self.guard.hold(False)
                await self.publish_status()
                return
            except LoginRequired:
                self.state, self.message = "login", "Войдите в Докки, чтобы чаты были видны на телефоне"
                self.guard.hold(False)
                await self.publish_status()
                await asyncio.sleep(30)
            except RemoteFailure as exc:
                self.state, self.message = "offline", "Нет связи с Докки, повторю"
                await self.publish_status()
                await asyncio.sleep(max(exc.retry_after, backoff * random.uniform(0.7, 1.3)))
                backoff = min(30.0, backoff * 2)
            except Exception as exc:  # noqa: BLE001 - мост не должен падать молча
                log_event("ERROR", "remote.supervise", type(exc).__name__, {})
                self.state, self.message = "offline", "Сбой связи с телефоном, повторю"
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2)

    def adopt_account(self, owner: str) -> None:
        config = self.config()
        if config["account_id"] == owner:
            return
        # другой аккаунт: прежний компьютер и чаты принадлежат ему, начинаем с чистого листа
        with db() as conn:
            conn.execute("DELETE FROM remote_sessions")
            conn.execute("DELETE FROM remote_outbox")
            conn.execute("DELETE FROM remote_commands_seen")
            conn.execute("UPDATE remote_control SET account_id=?, host_id=NULL WHERE id=1", (owner,))
        self.registered_for = ""
        self.host = {}

    def fail(self, exc: RemoteFailure) -> None:
        """Ошибки, после которых продолжать нельзя; остальное повторяется на месте."""
        if exc.status == 410 and exc.code == "host_revoked":
            raise Revoked from exc
        if exc.status == 401 or exc.code in {"invalid_token", "session_revoked", "session_changed"}:
            raise LoginRequired(str(exc)) from exc
        if exc.status == 403:
            raise Fatal(exc.message or "Нет доступа к удалённому управлению") from exc

    async def register(self) -> None:
        config = self.config()
        body = {
            "install_id": config["install_id"],
            "name": config["name"] or _computer_name(),
            "platform": _platform(),
            "app_version": _app_version(),
            "state": await self.state_payload(),
        }
        try:
            value = await api.register(body)
        except RemoteFailure as exc:
            self.fail(exc)
            if exc.status in {409, 422}:
                raise Fatal(exc.message) from exc
            raise
        host = value.get("host") if isinstance(value.get("host"), dict) else {}
        if not isinstance(host.get("id"), str):
            raise RemoteFailure(502, "upstream_unavailable", "Сервер вернул некорректный ответ")
        self.host = host
        self._update(host_id=host["id"], name=str(host.get("name") or body["name"])[:120])

    async def run_loops(self) -> None:
        tasks = [
            asyncio.create_task(self.heartbeat_loop(), name="dokky-remote-heartbeat"),
            asyncio.create_task(self.poll_loop(), name="dokky-remote-poll"),
            asyncio.create_task(self.sender_loop(), name="dokky-remote-send"),
            asyncio.create_task(self.clock_loop(), name="dokky-remote-clock"),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    # состояние и сон
    async def state_payload(self) -> dict:
        from koddi.agents.runs import agent_runs

        mode = self.config()["prevent_sleep"]
        self.power = await asyncio.to_thread(power.power_source)
        busy = bool(agent_runs.active_ids())
        want = self._enabled and mode != "off" and not self.sleeping and (busy or (mode == "on_ac" and self.power == "ac"))
        held = self.guard.hold(want)
        return {
            "prevent_sleep_enabled": mode != "off",
            "prevent_sleep_active": held,
            "power": self.power,
            "sleeping": self.sleeping,
        }

    async def heartbeat_loop(self) -> None:
        while True:
            try:
                await api.heartbeat(self.host["id"], await self.state_payload())
                self.online()
            except RemoteFailure as exc:
                self.fail(exc)
                if exc.status == 404:
                    # компьютера на сервере больше нет (удалён целиком): регистрируемся заново
                    self.registered_for = ""
                    raise
                self.offline()
            self._beat.clear()
            try:
                await asyncio.wait_for(self._beat.wait(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def poll_loop(self) -> None:
        backoff = 1.0
        while True:
            self._poll = asyncio.create_task(api.commands(self.host["id"], POLL_WAIT))
            try:
                items = await self._poll
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():  # type: ignore[union-attr]
                    raise
                continue  # старый опрос после пробуждения отменён: сразу новый
            except RemoteFailure as exc:
                self.fail(exc)
                if exc.status == 404:
                    self.registered_for = ""
                    raise
                self.offline()
                await asyncio.sleep(max(exc.retry_after, backoff * random.uniform(0.7, 1.3)))
                backoff = min(30.0, backoff * 2)
                continue
            finally:
                self._poll = None
            backoff = 1.0
            self.online()
            for item in items:
                await self.execute(item)

    async def clock_loop(self) -> None:
        # процесс стоит, пока компьютер спит: скачок часов означает пробуждение
        last = time.time()
        tidy = 0.0
        while True:
            await asyncio.sleep(5)
            now = time.time()
            if now - last > 30:
                self.wake_notice()
            last = now
            if now - tidy > 600:
                tidy = now
                self.tidy()

    @staticmethod
    def tidy() -> None:
        """Раз в 10 минут: старые отметки команд прочь; долго без сети очередь не растёт без конца."""
        outbox.cleanup()
        outbox.bound()

    def sleep_notice(self) -> None:
        self._threadsafe(self._sleeping, True)

    def wake_notice(self) -> None:
        self._threadsafe(self._sleeping, False)

    def _sleeping(self, value: bool) -> None:
        self.sleeping = value
        self._beat.set()
        if not value and self._poll and not self._poll.done():
            self._poll.cancel()

    def online(self) -> None:
        if self.state != "online":
            self.state, self.message = "online", ""
            self._spawn(self.publish_status())

    def offline(self) -> None:
        if self.state != "offline":
            self.state, self.message = "offline", "Нет связи с Докки, повторю"
            self._spawn(self.publish_status())

    async def publish_status(self) -> None:
        from koddi.events import event_bus

        await event_bus.publish("remote.status", {"state": self.state})

    def _spawn(self, coroutine) -> None:
        task = asyncio.get_running_loop().create_task(coroutine)
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)

    def _threadsafe(self, function, *args) -> None:
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            function(*args)
        else:
            loop.call_soon_threadsafe(function, *args)

    def wake(self) -> None:
        self._threadsafe(self._send.set)

    # отправка
    async def sender_loop(self) -> None:
        backoff = 1.0
        self._send.set()
        while True:
            try:
                await asyncio.wait_for(self._send.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
            self._send.clear()
            if outbox.count() < SEND_NOW:
                await asyncio.sleep(SEND_DELAY)
            try:
                await self.drain()
                backoff = 1.0
            except RemoteFailure as exc:
                self.fail(exc)
                self.offline()
                await asyncio.sleep(max(exc.retry_after, backoff * random.uniform(0.7, 1.3)))
                backoff = min(30.0, backoff * 2)
                self._send.set()

    async def drain(self) -> None:
        host_id = self.host.get("id")
        if not host_id:
            return
        while rows := outbox.pending():
            for group in outbox.batches(rows):
                await self.send(host_id, group)
            self.online()

    async def send(self, host_id: str, group: list[dict]) -> None:
        first = group[0]
        key, kind = first["key"], first["kind"]
        ids = [row["id"] for row in group]
        try:
            if kind == "event":
                await api.events(host_id, key, [row["body"] for row in group])
            elif kind == "message":
                await api.message(host_id, key, first["body"])
            elif kind == "session":
                value = await api.put_session(host_id, key, first["body"])
                self.session_saved(key, value)
            elif kind == "close":
                try:
                    await api.close_session(host_id, key)
                except RemoteFailure as exc:
                    if exc.status not in {404, 410}:
                        raise
                if first["body"].get("forget"):
                    with db() as conn:
                        conn.execute("DELETE FROM remote_sessions WHERE session_key=? AND shared=0", (key,))
            outbox.done(ids)
        except RemoteFailure as exc:
            self.fail(exc)
            if exc.network or (exc.status == 429 and exc.code != "events_limit"):
                raise
            if (exc.status == 410 and exc.code == "session_deleted") or exc.code == "too_many_chats":
                # чат удалён в вебе или на компьютере слишком много чатов: больше не слать
                self.unshare_local(key, exc.message if exc.code == "too_many_chats" else "")
                return
            if exc.status == 404 and kind in {"event", "message"}:
                # сервер не знает сессию (например, база сервера чистилась): регистрируем её снова
                await self.reregister(host_id, key)
                return
            if exc.code == "events_limit":
                if not outbox.trim_noise(key):
                    outbox.done(ids)
                log_event("WARNING", "remote.events_limit", key, {})
                return
            if exc.status == 422 and len(group) > 1:
                for row in group:
                    await self.send(host_id, [row])
                return
            # неверное тело или конфликт не исправится повтором: строка снимается, остальное идёт дальше
            log_event("WARNING", "remote.send.dropped", exc.code, {"kind": kind, "status": exc.status})
            outbox.done(ids)

    def session_saved(self, key: str, value: dict) -> None:
        public = value.get("chat_id")
        with db() as conn:
            if type(public) is int:
                conn.execute("UPDATE remote_sessions SET public_chat_id=? WHERE session_key=?", (public, key))
        if type(value.get("last_client_seq")) is int:
            outbox.raise_seq(key, value["last_client_seq"])

    async def reregister(self, host_id: str, key: str) -> None:
        row = outbox.session_by_key(key)
        if not row:
            outbox.drop_session(key)
            return
        value = await api.put_session(host_id, key, self.session_body(row["chat_id"], reshare=False))
        self.session_saved(key, value)

    def unshare_local(self, key: str, message: str) -> None:
        with db() as conn:
            conn.execute("UPDATE remote_sessions SET shared=0 WHERE session_key=?", (key,))
        outbox.drop_session(key, keep_close=True)
        if message:
            self.message = message

    # чаты
    @staticmethod
    def _project(project_id: int | None) -> dict | None:
        if project_id is None:
            return None
        from koddi.projects import get_project

        return get_project(project_id)

    def _chat(self, chat_id: int) -> dict | None:
        from koddi.policy.account import owns

        owner = _owner()
        with db() as conn:
            row = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        return dict(row) if row and owns(row["owner_id"], owner) else None

    def workspace(self, chat: dict) -> Path | None:
        project = self._project(chat.get("project_id"))
        return Path(project["path"]) if project and project.get("path") else None

    def session_body(self, chat_id: int, *, reshare: bool) -> dict:
        from koddi.agents.runs import agent_runs

        chat = self._chat(chat_id) or {}
        project = self._project(chat.get("project_id"))
        label = Path(str(project["path"])).name if project and project.get("path") else ""
        body: dict[str, Any] = {
            "title": clean_text(chat.get("title") or "Новый чат", None, 300),
            "folder_label": clean_text(project.get("name") if project and project.get("name") else label, None, 120),
            "status": "working" if agent_runs.active(chat_id) else "idle",
        }
        if reshare:
            body["reshare"] = True
        return body

    def share(self, chat_id: int, *, reshare: bool = False) -> bool:
        chat = self._chat(chat_id)
        if not chat:
            return False
        row = outbox.ensure_session(chat_id)
        if reshare and not row["shared"]:
            with db() as conn:
                conn.execute("UPDATE remote_sessions SET shared=1 WHERE chat_id=?", (chat_id,))
        elif not row["shared"]:
            return False
        outbox.put(row["session_key"], "session", self.session_body(chat_id, reshare=reshare))
        self.upload_history(chat_id, row["session_key"], self.workspace(chat), int(row["history_synced"] or 0))
        self.wake()
        return True

    def upload_history(self, chat_id: int, key: str, workspace: Path | None, after: int) -> None:
        """Сообщения пользователя и итоговые ответы после уже загруженных (`history_synced` - номер последнего).

        Журнал работы и вложения не нужны. Сервер не дублирует сообщение с тем же client_id, поэтому
        повтор после сбоя безопасен; ходы, прошедшие при выключенном доступе, догружаются при включении.
        """
        with db() as conn:
            rows = conn.execute(
                "SELECT id, role, content, metadata_json, created_at FROM messages WHERE chat_id=? AND id>? "
                "AND role IN ('user', 'assistant') ORDER BY id DESC LIMIT ?",
                (chat_id, after, HISTORY_LIMIT),
            ).fetchall()[::-1]
        mark = after
        for row in rows:
            meta = _meta(row["metadata_json"])
            if meta.get("status") == "running":
                # недописанный ответ дошлёт ход; отметка не уходит дальше него
                break
            mark = row["id"]
            if not row["content"]:
                continue
            if row["role"] == "assistant" and (meta.get("channel") not in {None, "", "final"} or meta.get("kind") == "activity"):
                continue
            if row["role"] == "user" and meta.get("origin") == "remote":
                # сообщение с телефона сервер уже хранит
                continue
            self.upload_message(key, f"m{row['id']}", row["role"], row["content"], row["created_at"], workspace)
        if mark > after:
            with db() as conn:
                conn.execute("UPDATE remote_sessions SET history_synced=MAX(history_synced, ?) WHERE chat_id=?", (mark, chat_id))

    def upload_message(self, key: str, client_id: str, role: str, content: str, created_at: Any, workspace: Path | None = None) -> bool:
        text = clean_text(content, workspace, MESSAGE_CHARS).strip()
        if not text:
            return False
        body: dict[str, Any] = {"client_id": client_id, "role": role, "content": text}
        when = _iso(created_at)
        if when:
            body["created_at"] = when
        return outbox.put(key, "message", body)

    async def sync_sessions(self) -> None:
        """После запуска: новые чаты - на телефон, у знакомых - актуальное название и статус."""
        owner = _owner()
        with db() as conn:
            rows = conn.execute(
                "SELECT c.id, c.owner_id, s.shared FROM chats c LEFT JOIN remote_sessions s ON s.chat_id=c.id "
                "ORDER BY c.updated_at DESC LIMIT ?",
                (SHARE_LIMIT,),
            ).fetchall()
        from koddi.policy.account import owns

        for row in rows:
            if owns(row["owner_id"], owner) and row["shared"] != 0:
                self.share(row["id"])

    def retitle(self, chat_id: int, title: str) -> None:
        key = outbox.shared_key(chat_id)
        if key and outbox.put(key, "session", {"title": clean_text(" ".join(title.split()), None, 300)}):
            self.wake()

    # хуки окна приложения: вызываются из потоков FastAPI, поэтому только запись в SQLite и пробуждение моста
    @_best_effort
    def chat_created(self, chat_id: int) -> None:
        if self._enabled:
            self.share(chat_id)

    @_best_effort
    def chat_renamed(self, chat_id: int, title: str) -> None:
        self.retitle(chat_id, title)

    @_best_effort
    def chat_deleted(self, chat_id: int) -> None:
        row = outbox.session(chat_id)
        if not row:
            return
        with db() as conn:
            conn.execute("UPDATE remote_sessions SET shared=0 WHERE chat_id=?", (chat_id,))
        outbox.drop_session(row["session_key"])
        outbox.put(row["session_key"], "close", {"forget": True})
        self.wake()

    def set_shared(self, chat_id: int, shared: bool) -> dict:
        if not self._chat(chat_id):
            raise HTTPException(404, "Чат не найден")
        if shared:
            self.share(chat_id, reshare=True)
        else:
            row = outbox.ensure_session(chat_id)
            if row["shared"]:
                self.unshare_local(row["session_key"], "")
                outbox.put(row["session_key"], "close", {})
                self.wake()
        row = outbox.session(chat_id) or {}
        return {"chat_id": chat_id, "shared": bool(row.get("shared"))}

    @_best_effort
    def resolved(self, request_id: int, kind: str, payload: dict) -> None:
        """Ответ на запрос (в окне или с телефона): карточка в вебе закрывается сразу."""
        with db() as conn:
            row = conn.execute(
                "SELECT t.chat_id FROM agent_requests r JOIN turns t ON t.id=r.turn_id WHERE r.id=?", (request_id,)
            ).fetchone()
        key = outbox.shared_key(row["chat_id"]) if row else None
        if not key:
            return
        if kind == "approval":
            data = {"request_id": f"ar-{request_id}", "decision": "allow" if payload.get("approved") is True else "deny"}
            outbox.event(key, "approval_resolved", data=data)
        else:
            answer = clean_text(payload.get("answer") or "", None, 500)
            outbox.event(key, "question_resolved", data={"request_id": f"ar-{request_id}", "answer": answer})
        self.wake()
        # ответили с телефона: открытое окно убирает карточку запроса, не дожидаясь следующего события хода
        self._threadsafe(self._publish, "agent.request.resolved", {"chat_id": row["chat_id"], "request_id": request_id})

    def _publish(self, kind: str, data: dict) -> None:
        from koddi.events import event_bus

        task = asyncio.get_running_loop().create_task(event_bus.publish(kind, data))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ходы
    def on_run(self, run) -> None:
        """Слушатель `agent_runs.start`: ход видимого чата повторяется на телефоне."""
        if not self._enabled or self.loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            return
        # команда с телефона достаётся ходу, запущенному по ней: зеркало прошлого хода её уже не тронет
        command = self.commands.pop(run.chat_id, None)
        previous = self.mirrors.get(run.chat_id)
        if previous and not previous.done():
            previous.cancel()
        self.mirrors[run.chat_id] = running.create_task(self.follow(run, command), name=f"dokky-remote-mirror-{run.chat_id}")

    async def follow(self, run, command: dict | None = None) -> None:
        try:
            chat = self._chat(run.chat_id)
            if not chat:
                return
            row = outbox.session(run.chat_id)
            if row is None:
                self.share(run.chat_id)
            elif not row["shared"]:
                return
            key = outbox.session_key(run.chat_id)
            self._beat.set()
            mirror = Mirror(self, run, key, self.workspace(chat), command)
            await mirror.follow()
            if mirror.ended:
                # сообщения хода уже в outbox: при следующей сверке истории их повторять не нужно
                with db() as conn:
                    conn.execute(
                        "UPDATE remote_sessions SET history_synced=MAX(history_synced, "
                        "(SELECT COALESCE(MAX(id), 0) FROM messages WHERE chat_id=?)) WHERE chat_id=?",
                        (run.chat_id, run.chat_id),
                    )
        except LoginRequired:
            return
        except Exception as exc:  # noqa: BLE001
            log_event("WARNING", "remote.mirror", type(exc).__name__, {"chat_id": run.chat_id})
        finally:
            self._beat.set()
            if self.mirrors.get(run.chat_id) is asyncio.current_task():
                self.mirrors.pop(run.chat_id, None)

    # команды с телефона
    async def execute(self, item: dict) -> None:
        command_id = str(item.get("id") or "")
        if not re.match(r"^[A-Za-z0-9_.:-]{1,80}$", command_id):
            return
        previous = outbox.seen(command_id)
        if previous and previous["status"] != "pending":
            # сервер выдаёт команду снова, пока нет accepted: повторяем прежний ответ, но не исполняем
            await self.ack(command_id, previous["status"], previous["error"])
            return
        # pending до ответа серверу: если процесс упадёт до итога, повторная выдача исполнит команду, а не потеряет
        outbox.mark_seen(command_id, "pending")
        if not await self.ack(command_id, "accepted"):
            outbox.mark_seen(command_id, "rejected", "command_expired")
            return
        try:
            status, error = await self.run_command(item)
        except HTTPException as exc:
            status, error = "rejected", str(exc.detail if isinstance(exc.detail, str) else "Не удалось выполнить команду")
        except LoginRequired:
            status, error = "rejected", "На компьютере нет входа в Докки"
        except Exception as exc:  # noqa: BLE001
            log_event("ERROR", "remote.command", type(exc).__name__, {"kind": item.get("kind")})
            status, error = "rejected", "Не удалось выполнить команду на компьютере"
        outbox.mark_seen(command_id, status, error)
        if status != "accepted":
            # после accepted сервер команду больше не выдаёт: потерянный итог иначе оставит её висеть на телефоне
            for pause in self.ack_pauses:
                if await self.ack(command_id, status, error, retry=True):
                    break
                await asyncio.sleep(pause)

    async def ack(self, command_id: str, status: str, error: str = "", *, retry: bool = False) -> bool:
        """Без `retry`: можно ли исполнять команду. С `retry`: можно ли больше не повторять (дошло или повтор не поможет)."""
        try:
            await api.ack(self.host["id"], command_id, status, error)
            return True
        except RemoteFailure as exc:
            self.fail(exc)
            if exc.network:
                # без retry это accepted: сервер выдаст команду снова, ответ повторится из remote_commands_seen
                return not retry and status == "accepted"
            # 409 command_expired и прочие отказы: исполнять не нужно, повторять бессмысленно
            return retry

    async def run_command(self, item: dict) -> tuple[str, str]:
        kind = item.get("kind")
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if kind == "new_session":
            return await self.new_session(payload)
        key = str(item.get("session_key") or payload.get("session_key") or "")
        row = outbox.session_by_key(key)
        chat = self._chat(row["chat_id"]) if row and row["shared"] else None
        if not chat:
            return "rejected", "Этот чат больше не открыт для телефона"
        assert self.hooks is not None
        chat_id = int(chat["id"])
        if kind == "message":
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                return "rejected", "Пустое сообщение"
            from koddi.agents.runs import agent_runs

            if agent_runs.active(chat_id):
                return "rejected", "Агент уже работает в этом чате"
            # ход прочитает команду в turn.started: по ней сервер свяжет ход с сообщением с телефона
            previous = self.commands.get(chat_id)
            self.commands[chat_id] = {"id": item["id"], "request_uuid": payload.get("request_uuid")}
            try:
                await self.hooks.start_turn(chat_id, text)
            except BaseException as exc:
                if self.commands.get(chat_id, {}).get("id") == item["id"]:
                    if previous:
                        self.commands[chat_id] = previous
                    else:
                        self.commands.pop(chat_id, None)
                if not isinstance(exc, HTTPException):
                    raise
                if exc.status_code == 409:
                    return "rejected", "Агент уже работает в этом чате"
                detail = exc.detail.get("message") if isinstance(exc.detail, dict) else exc.detail
                return "rejected", str(detail or "Не удалось начать задачу на компьютере")[:500]
            from koddi.events import event_bus

            await event_bus.publish("remote.turn", {"chat_id": chat_id})
            return "accepted", ""
        if kind == "stop":
            await self.hooks.cancel_turn(chat_id)
            return "done", ""
        if kind in {"approval", "answer"}:
            match = re.match(r"^ar-(\d+)$", str(payload.get("request_id") or ""))
            if not match:
                return "rejected", "Запрос не найден"
            request_id = int(match[1])
            with db() as conn:
                found = conn.execute(
                    "SELECT t.chat_id, r.kind FROM agent_requests r JOIN turns t ON t.id=r.turn_id WHERE r.id=?", (request_id,)
                ).fetchone()
            # разрешение отвечается только решением, вопрос - только ответом: иначе агент получил бы не то, чего ждёт
            if not found or found["chat_id"] != chat_id or found["kind"] != ("approval" if kind == "approval" else "question"):
                return "rejected", "Запрос не найден"
            if kind == "approval":
                answer: dict = {"approved": payload.get("decision") == "allow"}
            else:
                raw = payload.get("options")
                options = [str(item) for item in raw if str(item).strip()] if isinstance(raw, list) else []
                text = str(payload.get("text") or "").strip()
                answer = {"answer": "; ".join(part for part in (", ".join(options), text) if part)}
                if not answer["answer"]:
                    return "rejected", "Пустой ответ"
            try:
                await asyncio.to_thread(self.hooks.respond, request_id, answer)
            except HTTPException as exc:
                if exc.status_code == 409:
                    return "done", ""  # на компьютере уже ответили, закрытие карточки уже отправлено
                return "rejected", str(exc.detail)[:500]
            return "done", ""
        return "rejected", "Неизвестная команда"

    async def new_session(self, payload: dict) -> tuple[str, str]:
        assert self.hooks is not None
        now = time.monotonic()
        self._created = [moment for moment in self._created if now - moment < 3600]
        if len(self._created) >= NEW_SESSIONS_PER_HOUR:
            return "rejected", "Слишком много новых чатов за час. Продолжите в открытом чате"
        self._created.append(now)
        config = self.config()
        project_id = config["new_session_project_id"] if self._project(config["new_session_project_id"]) else None
        if project_id is None:
            owner = _owner()
            with db() as conn:
                rows = conn.execute(
                    "SELECT c.project_id, c.owner_id FROM chats c JOIN remote_sessions s ON s.chat_id=c.id "
                    "WHERE s.shared=1 AND c.project_id IS NOT NULL ORDER BY c.updated_at DESC LIMIT 20"
                ).fetchall()
            from koddi.policy.account import owns

            project_id = next(
                (row["project_id"] for row in rows if owns(row["owner_id"], owner) and self._project(row["project_id"])), None
            )
        title = " ".join(str(payload.get("title") or "").split())[:72] or "Новый чат"
        chat = await self.hooks.create_chat(project_id, title)
        self.share(int(chat["id"]))
        from koddi.events import event_bus

        await event_bus.publish("remote.chats", {"chat_id": int(chat["id"])})
        return "done", ""


remote_control = RemoteControl()

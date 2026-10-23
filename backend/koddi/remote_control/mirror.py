"""Перевод хода агента (SSE `agent_runs`) в события сервера: то же, что видно в окне, видно на телефоне.

Подписка `ActiveRun.subscribe` держит очередь на 256 событий, поэтому здесь ничего не ждёт сети: события только
кладутся в outbox, а отправляет их мост. Дельты ответа склеиваются за 120 мс в одно событие.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any

from koddi.logs import log_event
from koddi.remote_control import outbox
from koddi.remote_control.redact import (
    ERROR_CHARS,
    MESSAGE_CHARS,
    PREVIEW_CHARS,
    TARGET_CHARS,
    TEXT_CHARS,
    clean_path,
    clean_text,
    diff_files,
    fit,
    head,
    safe_id,
    secret_file,
    tail,
)
from koddi.storage.db import db
from koddi.tools.schemas import COMMAND_BYTES

if TYPE_CHECKING:
    from koddi.agents.runs import ActiveRun

DELTA_SECONDS = 0.12
WAIT_STATUS_SECONDS = 2.0

# заголовок карточки в настоящем времени, как строка журнала в окне
TITLES = {
    "file.read": "Читаю файл",
    "file.read_many": "Читаю файлы",
    "file.read_range": "Читаю часть файла",
    "file.read_chunk": "Читаю часть файла",
    "file.metadata": "Смотрю сведения о файле",
    "file.outline": "Смотрю структуру файла",
    "directory.list": "Изучаю папку проекта",
    "directory.tree": "Изучаю структуру проекта",
    "search.text": "Ищу в проекте",
    "search.files": "Ищу файлы",
    "web.search": "Ищу в интернете",
    "web.fetch": "Открываю страницу",
    "file.create": "Создаю файл",
    "file.write_patch": "Записываю файл",
    "file.edit": "Правлю файл",
    "file.search_replace": "Правлю файл",
    "patch.apply": "Правлю файлы",
    "patch.preview": "Готовлю правку",
    "file.delete": "Удаляю файл",
    "file.move": "Перемещаю файл",
    "file.copy": "Копирую файл",
    "file.restore": "Откатываю изменения",
    "changes.rollback": "Откатываю изменения",
    "shell.run": "Выполняю команду",
    "test.run": "Запускаю тесты",
    "build.run": "Запускаю сборку",
    "lint.run": "Проверяю код",
    "format.run": "Форматирую код",
    "verification.run": "Проверяю результат",
    "process.kill": "Завершаю процесс",
    "git.status": "Смотрю состояние Git",
    "git.diff": "Смотрю изменения Git",
    "git.add": "Добавляю файлы в Git",
    "git.commit": "Делаю коммит",
    "git.checkout": "Переключаю ветку",
    "git.create_branch": "Создаю ветку",
    "browser.open": "Открываю страницу",
    "browser.click": "Нажимаю на странице",
    "browser.type": "Ввожу текст на странице",
    "browser.screenshot": "Делаю снимок страницы",
    "browser.dom": "Читаю страницу",
    "browser.console": "Читаю консоль страницы",
    "browser.network": "Смотрю запросы страницы",
    "devserver.start": "Запускаю сервер разработки",
    "devserver.stop": "Останавливаю сервер разработки",
    "memory.set": "Запоминаю заметку",
    "computer.use": "Работаю с приложением",
    "task.delegate": "Передаю подзадачу агенту",
    "tool.output_read": "Читаю результат действия",
    "project.instructions": "Читаю правила проекта",
    "attachment.read": "Читаю вложение",
    "workspace.snapshot": "Делаю резервную копию",
}
COMMAND_TOOLS = {"shell.run", "test.run", "build.run", "lint.run", "format.run", "verification.run", "devserver.start"}
URL_TOOLS = {"web.fetch", "browser.open"}
QUERY_TOOLS = {"web.search", "search.text", "search.files"}
# у этих инструментов на телефоне своё место: план - карточка плана, итог - ответ, вопрос - карточка вопроса
CONTROL_TOOLS = {"plan.update", "task.complete", "task.ask_user"}


def parse(item: str) -> tuple[int, str, dict]:
    sequence, kind, data = 0, "", "{}"
    for line in item.splitlines():
        if line.startswith("id: "):
            try:
                sequence = int(line[4:])
            except ValueError:
                sequence = 0
        elif line.startswith("event: "):
            kind = line[7:].strip()
        elif line.startswith("data: "):
            data = line[6:]
    try:
        payload = json.loads(data)
    except ValueError:
        payload = {}
    return sequence, kind, payload if isinstance(payload, dict) else {}


def _when(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def title(name: str, display: str = "") -> str:
    if name in TITLES:
        return TITLES[name]
    label = (display or name).replace("__", ".")
    return f"Вызываю {label}"[:120]


def target(name: str, args: dict, workspace: Path | None, display: str = "") -> str:
    if name in COMMAND_TOOLS:
        return clean_text(" ".join(str(args.get("command") or args.get("cmd") or "").split()), workspace, TARGET_CHARS)
    if name in URL_TOOLS:
        url = str(args.get("url") or "")
        # адрес без query: в параметрах бывают токены
        return clean_text(url.split("?", 1)[0].split("#", 1)[0], workspace, TARGET_CHARS)
    if name in QUERY_TOOLS:
        return clean_text(args.get("query") or args.get("pattern") or "", workspace, TARGET_CHARS)
    if name == "file.read_many":
        paths = args.get("paths") if isinstance(args.get("paths"), list) else []
        return ", ".join(clean_path(item, workspace) for item in paths[:8])[:TARGET_CHARS]
    if name in {"file.move", "file.copy"}:
        return f"{clean_path(args.get('source'), workspace)} → {clean_path(args.get('destination'), workspace)}"[:TARGET_CHARS]
    if name == "mcp.tool_call":
        return clean_text(f"{args.get('server', '')} / {args.get('name', '')}", workspace, TARGET_CHARS)
    for key in ("path", "source", "file", "directory"):
        if args.get(key):
            return clean_path(args[key], workspace)
    if display:
        return clean_text(display, workspace, TARGET_CHARS)
    return ""


class Mirror:
    """Один ход одного чата: читает поток окна и кладёт события сервера в outbox."""

    def __init__(self, bridge, run: ActiveRun, key: str, workspace: Path | None, command: dict | None = None):
        self.bridge = bridge
        self.command = command
        self.run = run
        self.chat_id = run.chat_id
        self.key = key
        self.workspace = workspace
        self.turn_id: int | None = None
        self.started = False
        self.ended = False
        self.after = 0
        self.last_status = ""
        self.status_at: dict[str, float] = {}
        self.buffer: list[str] = []
        self.flush_handle: asyncio.TimerHandle | None = None
        self.texts: dict[int, str] = {}
        self.cards: dict[str, dict] = {}
        self.requests: dict[int, str] = {}

    # поток
    async def follow(self) -> None:
        resets = 0
        try:
            while True:
                restart = False
                async for item in self.run.subscribe(self.after):
                    sequence, kind, payload = parse(item)
                    if kind == "stream.reset":
                        # окно отстало и потеряло хвост очереди: начинаем с первого доступного события
                        resets += 1
                        first = int(payload.get("first_available") or 1)
                        last = int(payload.get("last_sequence") or 0)
                        self.after = max(self.after, first - 1) if resets < 3 else last
                        restart = True
                        break
                    if sequence:
                        self.after = sequence
                    try:
                        self.handle(kind, payload)
                    except Exception as exc:  # noqa: BLE001 - зеркало не должно ронять ход агента
                        log_event("WARNING", "remote.mirror.event", type(exc).__name__, {"chat_id": self.chat_id, "event": kind})
                if not restart:
                    break
        finally:
            self.flush()
            if self.started and not self.ended:
                # поток закрылся без итога (приложение закрывается или ход забыт): веб не должен ждать вечно
                self.close_cards("error", "Ход прерван на компьютере")
                self.event("error", "Ход прерван на компьютере.")
                self.ended = True
            self.bridge.wake()

    def handle(self, kind: str, payload: dict) -> None:
        if kind == "turn.started":
            self.turn_started(payload)
            return
        if not self.started:
            return
        if kind == "agent.status":
            self.status(payload.get("text"))
        elif kind == "agent.waiting":
            self.status("Жду ответа модели…", throttle="wait")
        elif kind == "tool.waiting":
            self.status(f"Жду завершения: {title(str(payload.get('name') or ''), str(payload.get('display_name') or '')).lower()}", throttle="wait")
        elif kind == "context.compacting":
            self.status("Сжимаю контекст")
        elif kind == "context.compacted":
            self.status(payload.get("text") or "Контекст сжат, продолжаю задачу")
        elif kind == "agent.warning":
            self.status(payload.get("error"))
        elif kind == "task.progress":
            self.status(f"Подзадача: {payload.get('title') or 'агент работает'}", throttle="task")
        elif kind == "message.delta":
            self.delta(payload)
        elif kind == "message.reset":
            self.flush()
            if isinstance(payload.get("message_id"), int):
                self.texts.pop(payload["message_id"], None)
            self.event("reset")
        elif kind == "message.updated":
            self.updated(payload)
        elif kind == "tool.call":
            self.tool_call(payload)
        elif kind == "tool.result":
            self.tool_result(payload)
        elif kind == "plan.updated":
            self.plan(payload)
        elif kind == "agent.input_required":
            self.input_required(payload)
        elif kind == "turn.completed":
            self.completed()
        elif kind == "turn.cancelled":
            self.cancelled()
        elif kind == "turn.failed":
            self.failed(payload)

    # служебное
    def event(self, kind: str, text: str = "", data: dict | None = None) -> None:
        if outbox.event(self.key, kind, text[:TEXT_CHARS] if text else "", data) is not None:
            self.bridge.wake()

    def status(self, value: Any, throttle: str = "") -> None:
        text = clean_text(value, self.workspace, 300).strip()
        if not text or text == self.last_status:
            return
        if throttle:
            now = monotonic()
            if now - self.status_at.get(throttle, 0) < WAIT_STATUS_SECONDS:
                return
            self.status_at[throttle] = now
        self.last_status = text
        self.event("status", text, {"node": "remote"})

    def delta(self, payload: dict) -> None:
        content = payload.get("content")
        if not isinstance(content, str) or not content:
            return
        message_id = payload.get("message_id")
        if isinstance(message_id, int):
            self.texts[message_id] = (self.texts.get(message_id, "") + content)[:MESSAGE_CHARS]
        self.buffer.append(content)
        if sum(len(part) for part in self.buffer) >= TEXT_CHARS:
            self.flush()
        elif self.flush_handle is None:
            self.flush_handle = asyncio.get_running_loop().call_later(DELTA_SECONDS, self.flush)

    def flush(self) -> None:
        if self.flush_handle is not None:
            self.flush_handle.cancel()
            self.flush_handle = None
        if not self.buffer:
            return
        text = clean_text("".join(self.buffer), self.workspace, MESSAGE_CHARS)
        self.buffer = []
        for start in range(0, len(text), TEXT_CHARS):
            self.event("delta", text[start : start + TEXT_CHARS])

    # ход
    def turn_started(self, payload: dict) -> None:
        name = payload.get("chat_title")
        if isinstance(name, str) and name.strip():
            self.bridge.retitle(self.chat_id, name)
        if self.started or not isinstance(payload.get("turn_id"), int):
            return
        self.turn_id = payload["turn_id"]
        self.started = True
        command, self.command = self.command, None
        if command:
            self.event("turn_start", data={"command_id": command["id"], "request_uuid": command.get("request_uuid") or uuid.uuid4().hex})
            return
        # ход начат на компьютере: сначала сообщение пользователя, чтобы веб показал его над ходом работы
        with db() as conn:
            row = conn.execute(
                "SELECT id, content, created_at, metadata_json FROM messages WHERE chat_id=? AND role='user' "
                "AND json_extract(metadata_json, '$.turn_id')=? ORDER BY id DESC LIMIT 1",
                (self.chat_id, self.turn_id),
            ).fetchone()
        data: dict[str, Any] = {"request_uuid": uuid.uuid4().hex}
        try:
            remote = json.loads(row["metadata_json"] or "{}").get("origin") == "remote" if row else False
        except (ValueError, AttributeError):
            remote = False
        # сообщение с телефона сервер уже хранит, повторно его не загружаем
        if row and not remote and str(row["content"] or "").strip():
            client_id = f"m{row['id']}"
            if self.bridge.upload_message(self.key, client_id, "user", row["content"], row["created_at"], self.workspace):
                data["user_client_id"] = client_id
        self.event("turn_start", data=data)

    def updated(self, payload: dict) -> None:
        message_id = payload.get("message_id")
        if payload.get("channel") != "commentary" or not isinstance(message_id, int):
            return
        self.flush()
        text = self.texts.pop(message_id, "").strip()
        if not text:
            return
        # текст шага оказался комментарием: веб убирает живой текст и показывает его комментарием, как журнал окна
        self.event("reset")
        self.event("commentary", clean_text(text, self.workspace, TEXT_CHARS))

    def plan(self, payload: dict) -> None:
        steps = []
        for step in payload.get("steps") or []:
            if not isinstance(step, dict):
                continue
            status = str(step.get("status") or "pending")
            text = clean_text(step.get("title") or "", self.workspace, 300).strip()
            if not text:
                continue
            if status == "blocked":
                text += " - заблокировано"
            steps.append({"text": text, "status": {"running": "in_progress", "completed": "completed"}.get(status, "pending")})
        self.event("plan", data={"steps": steps[:50]})

    def card_id(self, call_id: str) -> str:
        raw = f"{self.turn_id}-{call_id}"
        return raw if safe_id(raw) == raw else f"{self.turn_id}-{safe_id('#' + call_id)}"

    def tool_call(self, payload: dict) -> None:
        name = str(payload.get("name") or "")
        call_id = str(payload.get("id") or "")
        if name in CONTROL_TOOLS or not call_id:
            if name == "task.complete":
                self.status("Подвожу итог")
            return
        self.flush()
        display = str(payload.get("display_name") or "")
        args = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
        card = {
            "id": self.card_id(call_id),
            "state": "running",
            "tool": name[:80],
            "target": target(name, args, self.workspace, display),
        }
        self.cards[call_id] = {"card": card, "title": title(name, display), "started": _when(payload.get("created_at"))}
        self.event("action", self.cards[call_id]["title"], fit(card))

    def tool_result(self, payload: dict) -> None:
        call_id = str(payload.get("id") or "")
        entry = self.cards.pop(call_id, None)
        if not entry:
            return
        name = str(payload.get("name") or "")
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        card = dict(entry["card"])
        failed = result.get("ok") is False
        state = "done"
        if failed:
            state = "denied" if self.denied(call_id) else "error"
        card["state"] = state
        finished = _when(payload.get("created_at"))
        if entry["started"] and finished and finished >= entry["started"]:
            card["duration_ms"] = int((finished - entry["started"]) * 1000)
        self.attach_result(card, name, result)
        if failed and not card.get("error"):
            card["error"] = "Запрещено" if state == "denied" else "Не выполнено"
        self.event("action", entry["title"], fit(card))

    def attach_result(self, card: dict, name: str, result: dict) -> None:
        if result.get("cancelled"):
            card["error"] = "Остановлено"
        elif result.get("error"):
            card["error"] = clean_text(result["error"], self.workspace, ERROR_CHARS)
        if isinstance(result.get("change_set_id"), int):
            for key, value in (("added", result.get("lines_added")), ("removed", result.get("lines_removed"))):
                if isinstance(value, int) and value >= 0:
                    card[key] = value
            files = result.get("files") if isinstance(result.get("files"), list) else []
            if len(files) > 1:
                card["files"] = [
                    {
                        "path": clean_path(item.get("path"), self.workspace),
                        "added": int(item.get("lines_added") or 0),
                        "removed": int(item.get("lines_removed") or 0),
                    }
                    for item in files[:20]
                    if isinstance(item, dict)
                ]
            if not card.get("target") and files and isinstance(files[0], dict):
                card["target"] = clean_path(files[0].get("path"), self.workspace)
            with db() as conn:
                row = conn.execute("SELECT diff FROM change_sets WHERE id=?", (result["change_set_id"],)).fetchone()
            if row and row["diff"]:
                self.diff_preview(card, row["diff"], [item.get("path") for item in files if isinstance(item, dict)])
        elif name in COMMAND_TOOLS or "stdout" in result or "stderr" in result:
            output = "\n".join(
                part for part in (str(result.get("stdout") or "").rstrip(), str(result.get("stderr") or "").rstrip()) if part
            )
            if output:
                card["preview"] = tail(clean_text(output, self.workspace, PREVIEW_CHARS * 8), PREVIEW_CHARS)
                card["preview_kind"] = "output"
            code = result.get("returncode")
            if isinstance(code, int) and code != 0 and not card.get("error"):
                card["error"] = f"Код выхода {code}"
        elif isinstance(result.get("diff"), str) and result["diff"]:
            self.diff_preview(card, result["diff"], [result.get("path")])

    def diff_preview(self, card: dict, diff: str, paths: list) -> None:
        # правку .env, ключей и подобного видно только счётчиком строк: содержимое на сервер не уходит
        if any(secret_file(path) for path in [*paths, card.get("target"), *diff_files(diff)]):
            return
        card["preview"] = head(clean_text(diff, self.workspace, PREVIEW_CHARS * 4), PREVIEW_CHARS)
        card["preview_kind"] = "diff"

    def denied(self, call_id: str) -> bool:
        with db() as conn:
            row = conn.execute(
                "SELECT response_json FROM agent_requests WHERE turn_id=? AND kind='approval' "
                "AND json_extract(payload_json, '$.id')=? ORDER BY id DESC LIMIT 1",
                (self.turn_id, call_id),
            ).fetchone()
        if not row or not row[0]:
            return False
        try:
            return json.loads(row[0]).get("approved") is False
        except (ValueError, AttributeError):
            return False

    def input_required(self, payload: dict) -> None:
        request = payload.get("id")
        if not isinstance(request, int):
            return
        self.flush()
        request_id = f"ar-{request}"
        if payload.get("kind") == "question":
            self.requests[request] = "question"
            question = clean_text(payload.get("question") or "Агент задал вопрос", self.workspace, TEXT_CHARS)
            self.event("question_request", question, {"request_id": request_id, "options": [], "allow_text": True})
            return
        self.requests[request] = "approval"
        action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
        heading = str(action.get("title") or "Подтвердите действие")
        detail = action.get("detail") or action.get("target") or ""
        # пометки идут в описание: его веб уже показывает, отдельных полей он не знает
        notes = [clean_text(payload.get("question") or heading, self.workspace, 1000)]
        if action.get("dangerous"):
            notes.append("Команда может удалить данные или изменить систему.")
        if action.get("outside_project"):
            notes.append("Файл вне папки проекта; изменение сохранится для отката.")
        data = {
            "request_id": request_id,
            "tool": str(payload.get("name") or "")[:80],
            "description": "\n".join(notes),
            # команду показываем целиком: она ограничена COMMAND_BYTES и вместе с описанием влезает в DATA_BYTES
            "detail": clean_text(detail, self.workspace, COMMAND_BYTES + 512),
            "can_remember": False,
        }
        self.event("approval_request", clean_text(heading, self.workspace, 300), data)

    def settle_requests(self) -> None:
        """Запросы, оставшиеся без ответа к концу хода: веб не должен держать активные кнопки."""
        if not self.requests:
            return
        with db() as conn:
            rows = conn.execute(
                f"SELECT id FROM agent_requests WHERE id IN ({','.join('?' for _ in self.requests)}) AND response_json IS NULL",
                list(self.requests),
            ).fetchall()
        for row in rows:
            kind = self.requests[row["id"]]
            if kind == "approval":
                self.event("approval_resolved", data={"request_id": f"ar-{row['id']}", "decision": "expired"})
            else:
                self.event("question_resolved", data={"request_id": f"ar-{row['id']}", "answer": ""})
        self.requests.clear()

    def close_cards(self, state: str, error: str) -> None:
        for entry in self.cards.values():
            card = {**entry["card"], "state": state, "error": error}
            self.event("action", entry["title"], fit(card))
        self.cards.clear()

    def final_message(self) -> str | None:
        with db() as conn:
            row = conn.execute(
                "SELECT id, content, created_at FROM messages WHERE chat_id=? AND role='assistant' AND content<>'' "
                "AND json_extract(metadata_json, '$.turn_id')=? AND json_extract(metadata_json, '$.channel')='final' "
                "ORDER BY id DESC LIMIT 1",
                (self.chat_id, self.turn_id),
            ).fetchone()
        if not row:
            return None
        client_id = f"m{row['id']}"
        content = clean_text(row["content"], self.workspace, MESSAGE_CHARS)
        return client_id if self.bridge.upload_message(self.key, client_id, "assistant", content, row["created_at"], self.workspace) else None

    def completed(self) -> None:
        self.flush()
        self.close_cards("done", "")
        self.settle_requests()
        client_id = self.final_message()
        self.event("turn_end", data={"message_client_id": client_id} if client_id else {})
        self.ended = True

    def cancelled(self) -> None:
        self.flush()
        self.close_cards("error", "Остановлено")
        self.settle_requests()
        data: dict[str, Any] = {"stopped": True}
        partial = next(((key, text) for key, text in sorted(self.texts.items(), reverse=True) if text.strip()), None)
        if partial:
            client_id = f"m{partial[0]}"
            if self.bridge.upload_message(self.key, client_id, "assistant", clean_text(partial[1], self.workspace, MESSAGE_CHARS), None):
                data["message_client_id"] = client_id
        self.event("turn_end", data=data)
        self.ended = True

    def failed(self, payload: dict) -> None:
        self.flush()
        self.close_cards("error", "Прервано")
        self.settle_requests()
        reason = clean_text(payload.get("error") or "", self.workspace, 1000).strip()
        self.event("error", f"Агент на компьютере остановился с ошибкой: {reason}" if reason else "Агент на компьютере остановился с ошибкой.")
        self.ended = True

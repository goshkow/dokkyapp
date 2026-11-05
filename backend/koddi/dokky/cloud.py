"""Облачные инструменты агента через сервер Докки: картинка, разбор YouTube, набросок документа.

Работу делают те же модели и пайплайны, что в обычном чате Докки, но запрос обезличенный: сервер не видит
памяти, истории и чатов человека, только инструкцию и приложенные файлы. Расход идёт в лимит режима
разработки. Файлы проекта уходят на сервер только явно перечисленные агентом, с проверкой пути, типа и
размера; результат сохраняется в папку проекта и не перезаписывает существующие файлы.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from koddi.dokky.client import API, DokkyError, dokky

LOG = logging.getLogger(__name__)

# списки и лимиты совпадают с GET /api/v1/coding/cloud/capabilities, сервер проверяет их ещё раз
IMAGE_INPUTS = frozenset({"png", "jpg", "jpeg", "webp"})
IMAGE_CONTEXT = frozenset({"pdf", "docx", "pptx", "txt", "md", "csv"})
DOCUMENT_OUTPUTS = ("pdf", "docx", "pptx", "xlsx")
DOCUMENT_INPUTS = frozenset({"pdf", "docx", "pptx", "txt", "md", "csv", "png", "jpg", "jpeg", "webp"})
MAX_REFERENCES = 4
MAX_INPUTS = 5
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 40 * 1024 * 1024
MAX_PROMPT = 4000
MAX_QUESTION = 4000
MAX_INSTRUCTION = 8000
# скачанный результат больше этого не сохраняем: защита папки проекта от неожиданного ответа
MAX_RESULT_BYTES = 80 * 1024 * 1024
YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
DEFAULT_FOLDER = "dokky-cloud"
# сервер обрывает задачу сам по этим срокам; клиенту - небольшой запас на очередь и скачивание
DEADLINES = {"image": 240, "video": 1260, "document": 660}
POLL_SECONDS = 2.5
MEDIA_TYPES = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
    "pdf": "application/pdf", "txt": "text/plain", "md": "text/markdown", "csv": "text/csv",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
_SAFE_NAME = re.compile(r"[^\w.\- ()]+", re.UNICODE)


class CloudError(Exception):
    def __init__(self, message: str, *, code: str = "cloud_failed", retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def failure(exc: Exception) -> dict:
    if isinstance(exc, CloudError):
        return {"ok": False, "error": str(exc), "error_code": exc.code, "retryable": exc.retryable}
    if isinstance(exc, DokkyError):
        code = getattr(exc, "code", "") or "server_error"
        hint = {
            "quota_exceeded": "Лимит использования исчерпан: сообщите пользователю, сделайте работу локально или подождите сброса лимита.",
            "rate_limited": "Слишком много облачных задач подряд. Подождите и повторите или сделайте локально.",
            "insufficient_scope": "Войдите в Докки заново: у входа нет нужного разрешения.",
            "invalid_token": "Пользователь не вошёл в Докки. Сделайте работу локально.",
        }.get(code, "")
        result = {"ok": False, "error": str(exc), "error_code": code,
                  "retryable": bool((getattr(exc, "details", None) or {}).get("retryable"))}
        if hint:
            result["hint"] = hint
        return result
    LOG.warning("cloud task failed: %s", type(exc).__name__)
    return {"ok": False, "error": "Облачная задача не выполнилась.", "error_code": "cloud_failed", "retryable": True}


def _text(value, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CloudError(f"{name}: нужна непустая строка", code="invalid_args")
    value = value.strip()
    if len(value) > limit or "\x00" in value:
        raise CloudError(f"{name}: не больше {limit} символов", code="invalid_args")
    return value


def youtube_url(value) -> str:
    url = _text(value, "url", 2048)
    try:
        parts = urlsplit(url)
    except ValueError:
        raise CloudError("url: ссылка не разбирается", code="invalid_args") from None
    if parts.scheme != "https" or (parts.hostname or "").casefold() not in YOUTUBE_HOSTS or parts.username or parts.port:
        raise CloudError("url: только https-ссылка на ролик YouTube (youtube.com или youtu.be)", code="invalid_args")
    return url


def project_files(workspace: Path, values, allowed: frozenset[str], limit: int) -> list[Path]:
    """Файлы проекта для отправки: внутри папки проекта, без ключей и секретов, нужного типа и размера."""
    from koddi.policy.engine import sensitive_path
    from koddi.policy.paths import PathAccessError, WorkspacePaths

    if values is None:
        return []
    if not isinstance(values, list) or len(values) > limit:
        raise CloudError(f"Не больше {limit} файлов за раз", code="invalid_args")
    paths = WorkspacePaths(workspace)
    files: list[Path] = []
    total = 0
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise CloudError("Путь к файлу должен быть строкой", code="invalid_args")
        try:
            path = paths.resolve(value.strip())
        except (PathAccessError, ValueError, OSError):
            raise CloudError(f"{value}: можно отправлять только файлы внутри проекта", code="invalid_args") from None
        if sensitive_path(str(path)) or path.name.casefold().startswith(".env"):
            raise CloudError(f"{value}: файлы с ключами и паролями в облако не отправляются", code="invalid_args")
        if not path.is_file():
            raise CloudError(f"{value}: файл не найден", code="invalid_args")
        suffix = path.suffix.casefold().lstrip(".")
        if suffix not in allowed:
            raise CloudError(f"{value}: тип .{suffix or '?'} не принимается, можно: {', '.join(sorted(allowed))}",
                             code="unsupported_file")
        size = path.stat().st_size
        if size == 0:
            raise CloudError(f"{value}: файл пуст", code="invalid_args")
        if size > MAX_FILE_BYTES:
            raise CloudError(f"{value}: больше {MAX_FILE_BYTES // 1024 // 1024} МБ. Большие файлы обрабатывайте локально",
                             code="file_too_large")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise CloudError(f"Вместе файлы больше {MAX_TOTAL_BYTES // 1024 // 1024} МБ. Отправьте меньше или делайте локально",
                             code="file_too_large")
        if path in files:
            raise CloudError(f"{value}: файл указан дважды", code="invalid_args")
        files.append(path)
    return files


def output_folder(workspace: Path, value) -> Path:
    from koddi.policy.paths import PathAccessError, WorkspacePaths

    raw = DEFAULT_FOLDER if value in (None, "") else value
    if not isinstance(raw, str) or len(raw) > 300:
        raise CloudError("save_to: путь к папке внутри проекта", code="invalid_args")
    try:
        folder = WorkspacePaths(workspace).resolve(raw.strip())
    except (PathAccessError, ValueError, OSError):
        raise CloudError("save_to: можно сохранять только внутри проекта", code="invalid_args") from None
    if folder.exists() and not folder.is_dir():
        raise CloudError("save_to: это файл, а нужна папка", code="invalid_args")
    # служебные папки (.git/hooks, .github/workflows, .vscode) - только для правок, которые человек видит
    if any(part.startswith(".") for part in folder.relative_to(workspace.resolve()).parts):
        raise CloudError("save_to: в скрытые и служебные папки облачный результат не сохраняется", code="invalid_args")
    return folder


def _unique(folder: Path, name: str) -> Path:
    clean = _SAFE_NAME.sub("_", Path(name).name).strip(" .") or "result"
    stem, suffix = Path(clean).stem[:80] or "result", Path(clean).suffix[:10]
    target = folder / f"{stem}{suffix}"
    index = 2
    while target.exists():
        target = folder / f"{stem}-{index}{suffix}"
        index += 1
    return target


async def _upload(path: Path) -> str:
    suffix = path.suffix.casefold().lstrip(".")
    content = await asyncio.to_thread(path.read_bytes)
    response = await dokky.request(
        "POST", "/files", files={"file": (path.name, content, MEDIA_TYPES.get(suffix, "application/octet-stream"))},
        key="cloud-" + secrets.token_hex(12),
    )
    body = response.json()
    file_id = body.get("id") if isinstance(body, dict) else None
    if not isinstance(file_id, str) or not re.fullmatch(r"file_[A-Za-z0-9_-]{1,128}", file_id):
        raise CloudError("Сервер не принял файл")
    return file_id


def _place(partial: Path, folder: Path, name: str) -> Path:
    """Переносит скачанное под свободным именем; файл, появившийся одновременно, не перезаписывается."""
    import os

    for _ in range(50):
        target = _unique(folder, name)
        try:
            os.link(partial, target)
        except FileExistsError:
            continue
        except OSError:
            # файловая система без жёстких ссылок
            if target.exists():
                continue
            partial.replace(target)
            return target
        partial.unlink(missing_ok=True)
        return target
    raise CloudError("Не удалось подобрать имя для результата")


async def _download(file_id: str, folder: Path, name: str, allowed: frozenset[str], default: str, budget: int) -> Path:
    if not re.fullmatch(r"file_[A-Za-z0-9_-]{1,128}", file_id):
        raise CloudError("Сервер вернул непонятный файл")
    suffix = Path(name).suffix.casefold().lstrip(".")
    if suffix not in allowed:
        # сервер выбирает имя, но тип файла в проекте решает клиент: никаких .py, .sh и прочего исполняемого
        name = f"{Path(name).stem or 'result'}.{default}"
    folder.mkdir(parents=True, exist_ok=True)
    partial = folder / f".dokky-{secrets.token_hex(8)}.part"
    client = dokky.http()
    try:
        headers = {"Authorization": "Bearer " + await dokky.access_token()}
        async with client.stream("GET", API + f"/files/{file_id}/content", headers=headers) as response:
            if response.status_code != 200:
                await response.aread()
                dokky.check(response)
                raise CloudError("Не удалось скачать результат", retryable=True)
            size = 0
            with partial.open("xb") as handle:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > budget:
                        raise CloudError("Результат слишком большой")
                    handle.write(chunk)
        return _place(partial, folder, name)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        if dokky.transport is not None:
            await client.aclose()


async def _cancel(task_id: str) -> None:
    try:
        await asyncio.wait_for(dokky.request("POST", f"/coding/cloud/tasks/{task_id}/cancel"), 10)
    except BaseException:  # noqa: BLE001 - отмена на сервере не должна мешать остановке
        pass


async def _wait(task_id: str, kind: str, cancel_event, on_wait) -> dict:
    finished = False
    try:
        result = await _poll(task_id, kind, cancel_event, on_wait)
        finished = True
        return result
    finally:
        # остановка, таймаут, обрыв сети: задача на сервере не должна дальше тратить лимит и держать слот
        if not finished:
            await asyncio.shield(_cancel(task_id))


async def _poll(task_id: str, kind: str, cancel_event, on_wait) -> dict:
    deadline = time.monotonic() + DEADLINES[kind]
    started = time.monotonic()
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise CloudError("Остановлено пользователем", code="cancelled")
        body = (await dokky.request("GET", f"/coding/cloud/tasks/{task_id}")).json()
        status = body.get("status") if isinstance(body, dict) else None
        if status == "completed":
            return body.get("result") if isinstance(body.get("result"), dict) else {}
        if status in {"failed", "cancelled"}:
            error = body.get("error") if isinstance(body.get("error"), dict) else {}
            raise CloudError(str(error.get("message") or "Облачная задача не выполнилась."),
                             code=str(error.get("code") or status), retryable=bool(error.get("retryable")))
        if time.monotonic() > deadline:
            raise CloudError("Облачная задача не уложилась во время. Сделайте это локально или повторите позже",
                             code="timeout", retryable=True)
        if on_wait is not None:
            on_wait(int(time.monotonic() - started))
        await asyncio.sleep(POLL_SECONDS)


async def _ready() -> None:
    try:
        if not dokky.store.load():
            raise CloudError("Облачные инструменты работают после входа в Докки", code="not_signed_in")
        from koddi.dokky.access import developer_access

        access = await developer_access()
    except CloudError:
        raise
    except Exception as exc:  # noqa: BLE001 - без проверки доступа задачу не запускаем
        raise CloudError("Сервер Докки сейчас недоступен", code="unavailable", retryable=True) from exc
    if access.get("allowed") is False:
        raise CloudError("Облачные инструменты доступны с тарифа Plus и выше", code="plan_required")


async def run(kind: str, args: dict, workspace: Path | None, *, cancel_event=None, on_wait=None) -> dict:
    """Выполняет облачную задачу и сохраняет файлы результата в проект; ошибки - словарём для модели."""
    try:
        body, folder, expected = _prepare(kind, args, workspace)
        await _ready()
        if kind in {"image", "document"}:
            names = ("reference_ids", "references") if kind == "image" else ("input_ids", "inputs")
            body[names[0]] = [await _upload(path) for path in body.pop(names[1])]
        created = (await dokky.request("POST", "/coding/cloud/tasks", json=body, key="ctask-" + uuid.uuid4().hex)).json()
        task_id = created.get("id") if isinstance(created, dict) else None
        if not isinstance(task_id, str) or not re.fullmatch(r"ctask_[A-Za-z0-9_-]{1,128}", task_id):
            raise CloudError("Сервер вернул непонятный ответ")
        result = await _wait(task_id, kind, cancel_event, on_wait)
        if kind == "video":
            answer = str(result.get("answer") or "")
            if not answer.strip():
                raise CloudError("Разбор видео вернулся пустым", retryable=True)
            return {"ok": True, "source": "Докки (облако)", "url": body["url"], "title": str(result.get("title") or ""),
                    "answer": answer[:20000], "truncated": len(answer) > 20000}
        saved = []
        allowed = IMAGE_INPUTS if kind == "image" else frozenset({expected})
        budget = MAX_RESULT_BYTES
        for item in (result.get("files") or [])[:6]:
            if not isinstance(item, dict):
                continue
            path = await _download(str(item.get("id") or ""), folder, str(item.get("name") or f"result.{expected}"),
                                   allowed, expected, budget)
            budget -= path.stat().st_size
            saved.append({"path": path.relative_to(workspace).as_posix(), "size": path.stat().st_size,
                          "media_type": str(item.get("media_type") or "")})
        if not saved:
            raise CloudError("Облако не вернуло файл", retryable=True)
        reply = {"ok": True, "source": "Докки (облако)", "files": saved}
        if kind == "image":
            reply["mode"] = str(result.get("mode") or "")
            if result.get("note"):
                reply["note"] = str(result["note"])[:2000]
        else:
            reply["summary"] = str(result.get("summary") or "")[:4000]
            reply["next"] = ("Это набросок. Проверьте файл и вносите правки локально (python-docx, python-pptx, openpyxl "
                             "или приложение пользователя), если они доступны.")
        return reply
    except Exception as exc:  # noqa: BLE001 - модель получает понятную ошибку вместо обрыва хода
        return failure(exc)


def _prepare(kind: str, args: dict, workspace: Path | None) -> tuple[dict, Path | None, str]:
    if kind == "video":
        body = {"kind": "video", "url": youtube_url(args.get("url")),
                "question": _text(args.get("question") or "Кратко перескажи ролик по разделам.", "question", MAX_QUESTION),
                "scope": "relevant" if args.get("scope") == "relevant" else "overview"}
        return body, None, ""
    if workspace is None:
        raise CloudError("Нужна открытая папка проекта: результат сохраняется в неё", code="invalid_args")
    folder = output_folder(workspace, args.get("save_to"))
    if kind == "image":
        references = project_files(workspace, args.get("references"), IMAGE_INPUTS | IMAGE_CONTEXT, MAX_REFERENCES)
        return {"kind": "image", "prompt": _text(args.get("prompt"), "prompt", MAX_PROMPT),
                "references": references}, folder, "png"
    if kind == "document":
        fmt = str(args.get("format") or "").casefold().lstrip(".")
        if fmt not in DOCUMENT_OUTPUTS:
            raise CloudError(f"format: одно из {', '.join(DOCUMENT_OUTPUTS)}", code="unsupported_file")
        inputs = project_files(workspace, args.get("inputs"), DOCUMENT_INPUTS, MAX_INPUTS)
        return {"kind": "document", "instruction": _text(args.get("instruction"), "instruction", MAX_INSTRUCTION),
                "format": fmt, "inputs": inputs}, folder, fmt
    raise CloudError("Неизвестная облачная задача", code="invalid_args")

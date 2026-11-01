"""Чтение PDF в отдельном процессе.

PDF - сложный чужой формат: файл может зациклить разбор или съесть память, поэтому
процесс приложения его не разбирает. Работник получает байты по stdin и отдаёт JSON
по stdout, главный процесс ждёт его с таймаутом и при необходимости завершает.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_PDF_PAGES = 1000
MAX_PDF_CHARS = 200_000
PDF_SECONDS = 45
_CPU_SECONDS = 60
_MEMORY_BYTES = 2 * 1024 * 1024 * 1024


class PdfUnreadable(ValueError):
    """Причину можно показать человеку как есть."""


_REASONS = {
    "encrypted": "PDF защищён паролем, поэтому текст прочитать нельзя. Нужен файл без пароля.",
    "empty": "В этом PDF нет текста: похоже, это скан или картинки. Пришлите страницы картинками, и Докки их распознает.",
    "broken": "PDF повреждён или не читается.",
}


def read(data: bytes, max_chars: int = MAX_PDF_CHARS) -> dict:
    from pypdf import PdfReader
    from pypdf.errors import DependencyError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            return {"status": "encrypted"}
        total = len(reader.pages)
        parts: list[str] = []
        used = 0
        for page in reader.pages[:MAX_PDF_PAGES]:
            try:
                text = (page.extract_text() or "").strip()
            except DependencyError:
                raise
            except Exception:  # noqa: BLE001 - одна плохая страница не должна стоить всего файла
                continue
            if text:
                parts.append(text)
                used += len(text)
            if used >= max_chars:
                break
    except DependencyError:
        # AES без библиотеки шифрования: для человека это тот же защищённый файл
        return {"status": "encrypted"}
    except Exception:  # noqa: BLE001 - в чужом файле сломаться может что угодно
        return {"status": "broken"}
    if not parts:
        return {"status": "empty"}
    return {"status": "ok", "text": "\n\n".join(parts)[:max_chars], "pages": total}


def _limit_resources() -> None:
    """На Windows этих средств нет: там остаётся таймаут родителя и лимит размера файла."""
    try:
        import resource
    except ImportError:
        return
    try:
        # таймаут ставит родитель; этот лимит на случай, если родитель умер раньше работника
        resource.setrlimit(resource.RLIMIT_CPU, (_CPU_SECONDS, _CPU_SECONDS))
    except (OSError, ValueError):
        pass
    # RLIMIT_AS macOS не принимает, поэтому память смотрим сами
    unit = 1 if sys.platform == "darwin" else 1024

    def watch() -> None:
        while True:
            if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit > _MEMORY_BYTES:
                os._exit(3)
            time.sleep(0.2)

    threading.Thread(target=watch, daemon=True).start()


def main() -> int:
    _limit_resources()
    data = sys.stdin.buffer.read(MAX_PDF_BYTES + 1)
    result = read(data) if len(data) <= MAX_PDF_BYTES else {"status": "broken"}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


def _command() -> tuple[list[str], dict[str, str]]:
    # работнику не нужны ни токены, ни настройки из окружения приложения
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "TEMP", "TMP", "TMPDIR") if key in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    if "__compiled__" in globals():
        # собранное приложение запускает само себя: см. pdf_entry
        for start in (sys.argv[0] if sys.argv else "", sys.executable):
            if start and Path(start).is_file():
                return [str(Path(start).resolve()), "--read-pdf"], env
        raise PdfUnreadable(_REASONS["broken"])
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    return [sys.executable, "-m", "koddi.documents.pdf_worker"], env


def pdf_text(data: bytes, max_chars: int = MAX_PDF_CHARS) -> str:
    if len(data) > MAX_PDF_BYTES:
        raise PdfUnreadable("PDF больше 64 МБ: такой файл прочитать нельзя.")
    command, env = _command()
    try:
        done = subprocess.run(command, input=data, capture_output=True, timeout=PDF_SECONDS, env=env, check=False)
    except subprocess.TimeoutExpired as exc:
        raise PdfUnreadable("PDF читается слишком долго. Попробуйте файл поменьше.") from exc
    except OSError as exc:
        raise PdfUnreadable(_REASONS["broken"]) from exc
    try:
        result = json.loads(done.stdout)
    except ValueError as exc:
        # работник упал или его остановил лимит
        raise PdfUnreadable(_REASONS["broken"]) from exc
    if result.get("status") != "ok":
        raise PdfUnreadable(_REASONS.get(str(result.get("status")), _REASONS["broken"]))
    return str(result["text"])[:max_chars]


if __name__ == "__main__":
    raise SystemExit(main())

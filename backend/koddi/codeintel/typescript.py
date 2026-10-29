"""Языковой сервис TypeScript/JavaScript через TypeScript из node_modules проекта.

Рабочий процесс `native/ts_worker.js` исполняет код пакета typescript самого проекта, поэтому запускается только
когда проект в режиме «Автоматически» или «Разрешить всё» (там команды проекта и так выполняются) и пакет лежит
внутри папки проекта. Иначе сервис недоступен, а вызывающий получает честный приблизительный ответ.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from koddi.tools.runner import HIDDEN_SUBPROCESS

WORKER = Path(__file__).with_name("native") / "ts_worker.js"
JS_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
IDLE_SECONDS = 300.0
FIRST_TIMEOUT = 90.0
TIMEOUT = 30.0
MAX_WORKERS = 3
_workers: dict[tuple[str, str], "Worker"] = {}
_lock = threading.Lock()
_node: tuple[float, str | None] | None = None


class TypeScriptUnavailable(RuntimeError):
    """Сервис нельзя запустить: нет node, нет typescript в проекте или нет доверия к коду проекта."""


def is_script(path: str) -> bool:
    return path.lower().endswith(JS_SUFFIXES)


def node_binary() -> str | None:
    """node из PATH; приложение из Finder видит короткий PATH, поэтому спрашиваем оболочку входа (с кэшем)."""
    global _node
    if _node and time.monotonic() - _node[0] < 600:
        return _node[1]
    found = shutil.which("node")
    if found is None and os.name != "nt":
        from koddi.tools.runner import shell_argv

        try:
            proc = subprocess.run(shell_argv("command -v node"), capture_output=True, text=True, timeout=8, check=False)
            candidate = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
            found = candidate if candidate and Path(candidate).is_file() else None
        except (OSError, subprocess.SubprocessError):
            found = None
        for fallback in ("/opt/homebrew/bin/node", "/usr/local/bin/node"):
            if found is None and Path(fallback).is_file():
                found = fallback
    _node = (time.monotonic(), found)
    return found


def typescript_module(root: Path, relative: str) -> Path | None:
    """typescript.js ближайшего node_modules от файла до корня проекта; пакет за пределами проекта не берём."""
    return _find_typescript(root, relative)[0]


def _find_typescript(root: Path, relative: str) -> tuple[Path | None, str]:
    root = root.resolve()
    directory = (root / relative).parent
    while True:
        package = directory / "node_modules" / "typescript"
        manifest = package / "package.json"
        if manifest.is_file():
            try:
                real = package.resolve()
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data, real = {}, package
            library = real / "lib" / "typescript.js"
            if data.get("name") == "typescript" and real.is_relative_to(root):
                if library.is_file():
                    return library, ""
                # typescript 7 (нативный tsgo) не публикует JS API языкового сервиса
                return None, (
                    f"typescript {data.get('version') or '?'} in the project has no JavaScript language service API "
                    "(TypeScript 7 native); install typescript@5 as a dev dependency for semantic results"
                )
        if directory == root or root not in directory.parents:
            return None, "typescript is not installed in the project's node_modules (run npm install)"
        directory = directory.parent


def trusted(project_id: int | None) -> bool:
    from koddi.policy.grants import permission_profile
    from koddi.storage.db import get_setting

    if get_setting("code_intel_node", "auto") == "off":
        return False
    return permission_profile(project_id) in {"trusted", "full"}


class Worker:
    def __init__(self, root: Path, library: Path, node: str):
        self.root = root
        self.library = library
        self.started = time.monotonic()
        self.used = time.monotonic()
        self.calls = 0
        self._ids = 0
        self._lock = threading.Lock()
        self._responses: queue.Queue = queue.Queue()
        self.proc = subprocess.Popen(
            [node, str(WORKER), str(root), str(library)],
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=HIDDEN_SUBPROCESS,
            env={**os.environ, "NODE_OPTIONS": "--max-old-space-size=3072"},
        )
        threading.Thread(target=self._read, daemon=True, name="ts-worker-reader").start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            try:
                self._responses.put(json.loads(line))
            except ValueError:
                continue
        self._responses.put(None)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def call(self, op: str, args: dict, *, cancel: threading.Event | None = None) -> dict:
        with self._lock:
            if not self.alive():
                raise TypeScriptUnavailable("TypeScript worker stopped")
            self._ids += 1
            request_id = self._ids
            assert self.proc.stdin is not None
            try:
                self.proc.stdin.write(json.dumps({"id": request_id, "op": op, "args": args}) + "\n")
                self.proc.stdin.flush()
            except (OSError, ValueError) as exc:
                # процесс умер между проверкой и записью (BrokenPipe) - _reap заберёт его, следующий вызов поднимет новый
                self.stop()
                raise TypeScriptUnavailable("TypeScript worker stopped") from exc
            deadline = time.monotonic() + (FIRST_TIMEOUT if self.calls == 0 else TIMEOUT)
            while True:
                if cancel is not None and cancel.is_set():
                    # ответ на отменённый запрос придёт позже и собьёт порядок: процесс перезапускается
                    self.stop()
                    raise TypeScriptUnavailable("operation cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.stop()
                    raise TypeScriptUnavailable("TypeScript language service timed out")
                try:
                    response = self._responses.get(timeout=min(0.2, remaining))
                except queue.Empty:
                    continue
                if response is None:
                    raise TypeScriptUnavailable("TypeScript worker stopped")
                if response.get("id") != request_id:
                    continue
                self.calls += 1
                self.used = time.monotonic()
                if not response.get("ok"):
                    raise ValueError(response.get("error") or "TypeScript request failed")
                return response["result"]

    def stop(self) -> None:
        if self.alive():
            try:
                self.proc.kill()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def _reap() -> None:
    now = time.monotonic()
    for key, worker in list(_workers.items()):
        if not worker.alive() or now - worker.used > IDLE_SECONDS:
            worker.stop()
            _workers.pop(key, None)


def worker(root: Path, relative: str, project_id: int | None) -> Worker:
    """Рабочий процесс для файла проекта; при недоступности - TypeScriptUnavailable с причиной."""
    if not trusted(project_id):
        raise TypeScriptUnavailable(
            "TypeScript language service runs the project's own typescript package and needs the project "
            "permission mode «Автоматически» or «Разрешить всё»"
        )
    library, reason = _find_typescript(root, relative)
    if library is None:
        raise TypeScriptUnavailable(reason)
    node = node_binary()
    if node is None:
        raise TypeScriptUnavailable("node is not found in PATH")
    key = (str(root.resolve()), str(library))
    with _lock:
        _reap()
        current = _workers.get(key)
        if current is not None and current.alive():
            return current
        if len(_workers) >= MAX_WORKERS:
            oldest = min(_workers, key=lambda item: _workers[item].used)
            _workers.pop(oldest).stop()
        from koddi.logs import log_event

        log_event("INFO", "codeintel.typescript", "start TypeScript language service", {"library": str(library)})
        try:
            created = Worker(root.resolve(), library, node)
        except OSError as exc:
            raise TypeScriptUnavailable(f"cannot start node: {exc}") from exc
        _workers[key] = created
        return created


def shutdown() -> None:
    with _lock:
        for worker_item in _workers.values():
            worker_item.stop()
        _workers.clear()


atexit.register(shutdown)


# ---------- колонки: Python считает символы, TypeScript - единицы UTF-16

def to_utf16(line_text: str, column: int) -> int:
    return len(line_text[:column].encode("utf-16-le")) // 2


def from_utf16(line_text: str, units: int) -> int:
    encoded = line_text.encode("utf-16-le")[: units * 2]
    return len(encoded.decode("utf-16-le", errors="ignore"))


def read_text(path: Path) -> str:
    # без перевода строк: смещения TypeScript считаются по исходному тексту
    return path.read_bytes().decode("utf-8", errors="replace")


# переводы строк так, как их считает TypeScript (computeLineStarts): номера строк из языкового сервиса совпадают
LINE_BREAK = re.compile("(\r\n|\r|\n|\u2028|\u2029)")


def split_keep(text: str) -> tuple[list[str], list[str]]:
    """Строки и разделители между ними: "".join(чередуя) возвращает исходный текст байт в байт."""
    parts = LINE_BREAK.split(text)
    return parts[0::2], parts[1::2]


def join_keep(lines: list[str], breaks: list[str]) -> str:
    out = [lines[0]]
    for separator, line in zip(breaks, lines[1:]):
        out.extend((separator, line))
    return "".join(out)


def line_of(text: str, line: int) -> str:
    lines, _ = split_keep(text)
    return lines[line - 1] if 0 < line <= len(lines) else ""


def apply_utf16_changes(text: str, changes: list[dict]) -> str:
    """Правки TypeScript по смещениям UTF-16 от начала файла; применяются с конца."""
    data = text.encode("utf-16-le")
    for change in sorted(changes, key=lambda item: item["start"], reverse=True):
        start, length = int(change["start"]) * 2, int(change["length"]) * 2
        if start < 0 or start + length > len(data):
            raise ValueError("TypeScript edit is outside the file")
        data = data[:start] + str(change["text"]).encode("utf-16-le") + data[start + length:]
    return data.decode("utf-16-le")

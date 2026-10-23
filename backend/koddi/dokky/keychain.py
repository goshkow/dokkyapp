"""Защита сессии на macOS: случайный ключ лежит в файле 0600 в папке данных, файл сессии шифруется AES-GCM."""

from __future__ import annotations

import os
import secrets
import threading
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_FILE = "dokky-session.key"
MAGIC = b"DKY1"
_AAD = b"dokky-session"
_lock = threading.Lock()
_cached: bytes | None = None


class KeychainError(RuntimeError):
    pass


class KeychainUnavailable(KeychainError):
    pass


def _key_path() -> Path:
    from koddi.config import data_dir

    return data_dir() / KEY_FILE


def _read_key(path: Path) -> bytes | None:
    try:
        key = path.read_bytes()
    except FileNotFoundError:
        return None
    return key if len(key) == 32 else None


def _key(create: bool) -> bytes:
    # без Связки ключей: у неподписанной сборки macOS спрашивает пароль при каждом запуске
    global _cached
    with _lock:
        if _cached is not None:
            return _cached
        try:
            path = _key_path()
            key = _read_key(path)
            if key is None:
                if not create:
                    raise KeychainError("Ключ сессии не найден")
                path.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(path.parent, 0o700)
                key = secrets.token_bytes(32)
                # запись через временный файл: оборванный запуск не оставит битый ключ
                tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(key)
                os.replace(tmp, path)
        except KeychainError:
            raise
        except OSError as exc:
            raise KeychainUnavailable("Не удалось открыть ключ сессии") from exc
        _cached = key
        return key


def seal(value: bytes) -> bytes:
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(_key(True)).encrypt(nonce, value, _AAD)


def unseal(blob: bytes) -> bytes:
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 12 + 16:
        raise KeychainError("Неизвестный формат сессии")
    nonce = blob[len(MAGIC):len(MAGIC) + 12]
    try:
        return AESGCM(_key(False)).decrypt(nonce, blob[len(MAGIC) + 12:], _AAD)
    except InvalidTag as exc:
        raise KeychainError("Сессия повреждена") from exc


def forget_cached_key() -> None:
    """Для тестов: следующий вызов снова прочитает ключ с диска."""
    global _cached
    with _lock:
        _cached = None

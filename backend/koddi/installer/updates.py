"""Проверка подписанных метаданных и файла, без установки и запуска обновления.

Ответ канала: base64-поля manifest и signature. Ed25519 подписывает точные
байты manifest, JSON содержит version/platform/url/sha256/size/expires.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import platform
import re
import ssl
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

try:
    from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:
    Ed25519PublicKey = None


# задаются только в исходниках релизной сборки, не из env, профиля или HTTP.
PINNED_PUBLIC_KEY: bytes | None = None
MANIFEST_URL: str | None = None
TARGET_PLATFORM = "windows-x86_64"
MAX_MANIFEST_BYTES = 16_384
MAX_RESPONSE_BYTES = 32_768
MAX_ARTIFACT_BYTES = 2 * 1024**3


class UpdateVerificationError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedManifest:
    version: str
    platform: str
    url: str
    sha256: str
    size: int
    expires: datetime


def _https_origin(url: str):
    try:
        if (not isinstance(url, str) or len(url) > 2048
                or not url.isascii() or any(ord(char) <= 32 or ord(char) == 127 for char in url)):
            raise ValueError
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment or "\\" in url
                or not re.fullmatch(r"[A-Za-z0-9.-]+", parsed.hostname)):
            raise ValueError
        port = parsed.port
        if port is not None and port == 0:
            raise ValueError
        return parsed.hostname.lower(), 443 if port is None else port
    except (TypeError, ValueError):
        raise UpdateVerificationError("Нужен допустимый HTTPS URL обновления") from None


def _require_config():
    if not MANIFEST_URL or type(PINNED_PUBLIC_KEY) is not bytes or len(PINNED_PUBLIC_KEY) != 32:
        raise UpdateVerificationError("Канал обновлений не настроен в сборке")
    if Ed25519PublicKey is None:
        raise UpdateVerificationError("Проверка подписи недоступна")
    return _https_origin(MANIFEST_URL)


def _require_url(url: str):
    if _https_origin(url) != _require_config():
        raise UpdateVerificationError("Другой узел обновлений запрещён")


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,4})(?:\.(?:0|[1-9][0-9]{0,4})){2}", value):
        raise UpdateVerificationError("Недопустимая версия обновления")
    return tuple(int(part) for part in value.split("."))


def _runtime_platform():
    machine = platform.machine().lower()
    if sys.platform != "win32":
        return None
    return {"amd64": "windows-x86_64", "x86_64": "windows-x86_64", "arm64": "windows-arm64"}.get(machine)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpdateVerificationError("Повторяющееся поле манифеста")
        result[key] = value
    return result


def _json_object(raw):
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, RecursionError):
        raise UpdateVerificationError("Недопустимый JSON манифеста") from None
    if type(value) is not dict:
        raise UpdateVerificationError("Недопустимая структура манифеста")
    return value


def verify_signed_manifest(raw: bytes, signature: bytes, *, current_version: str) -> VerifiedManifest:
    _require_config()
    if (type(raw) is not bytes or not 0 < len(raw) <= MAX_MANIFEST_BYTES
            or type(signature) is not bytes or len(signature) != 64):
        raise UpdateVerificationError("Недопустимый размер манифеста или подписи")
    try:
        Ed25519PublicKey.from_public_bytes(PINNED_PUBLIC_KEY).verify(signature, raw)
    except (InvalidSignature, UnsupportedAlgorithm, ValueError):
        raise UpdateVerificationError("Подпись обновления не подтверждена") from None
    manifest = _json_object(raw)
    if set(manifest) != {"version", "platform", "url", "sha256", "size", "expires"}:
        raise UpdateVerificationError("Недопустимые поля манифеста")
    if _version(manifest["version"]) <= _version(current_version):
        raise UpdateVerificationError("Повторная установка или откат версии запрещены")
    if manifest["platform"] != TARGET_PLATFORM or _runtime_platform() != TARGET_PLATFORM:
        raise UpdateVerificationError("Платформа обновления не подходит")
    _require_url(manifest["url"])
    if not isinstance(manifest["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", manifest["sha256"]):
        raise UpdateVerificationError("Недопустимый SHA256 обновления")
    if type(manifest["size"]) is not int or not 0 < manifest["size"] <= MAX_ARTIFACT_BYTES:
        raise UpdateVerificationError("Недопустимый размер обновления")
    try:
        if not isinstance(manifest["expires"], str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", manifest["expires"]):
            raise ValueError
        expires = datetime.strptime(manifest["expires"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise UpdateVerificationError("Недопустимый срок действия манифеста") from None
    now = datetime.now(timezone.utc)
    if not now < expires <= now + timedelta(days=7):
        raise UpdateVerificationError("Манифест истёк или подписан на слишком долгий срок")
    return VerifiedManifest(manifest["version"], manifest["platform"], manifest["url"],
                            manifest["sha256"], manifest["size"], expires)


class SameOriginRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _require_url(req.full_url)
        _require_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_verified_manifest(*, current_version: str) -> VerifiedManifest:
    _require_config()
    _version(current_version)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), SameOriginRedirects(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    request = urllib.request.Request(MANIFEST_URL, headers={"Accept": "application/json"})
    try:
        with opener.open(request, timeout=10) as response:
            _require_url(response.geturl())
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except OSError:
        raise UpdateVerificationError("Не удалось получить манифест обновления") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise UpdateVerificationError("Ответ канала обновлений слишком большой")
    envelope = _json_object(raw)
    if set(envelope) != {"manifest", "signature"}:
        raise UpdateVerificationError("Нужен подписанный ответ канала обновлений")
    try:
        manifest = base64.b64decode(envelope["manifest"], validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
    except (TypeError, ValueError, binascii.Error):
        raise UpdateVerificationError("Недопустимая кодировка подписанного ответа") from None
    return verify_signed_manifest(manifest, signature, current_version=current_version)


def verify_artifact(path: Path, manifest: VerifiedManifest) -> None:
    if manifest.expires <= datetime.now(timezone.utc):
        raise UpdateVerificationError("Срок действия манифеста истёк")
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as source:
            while chunk := source.read(min(1024**2, manifest.size - total + 1)):
                total += len(chunk)
                if total > manifest.size:
                    raise UpdateVerificationError("Размер файла обновления не совпадает")
                digest.update(chunk)
    except OSError:
        raise UpdateVerificationError("Не удалось прочитать файл обновления") from None
    if total != manifest.size or not hmac.compare_digest(digest.hexdigest(), manifest.sha256):
        raise UpdateVerificationError("Размер или SHA256 файла обновления не совпадает")

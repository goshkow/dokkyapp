"""Граница доверия перед сервером: пути без папок компьютера, без секретов и в пределах сервера.

Телефон - тот же человек, но канал и хранение на сервере не должны получать лишнего:
полный путь к проекту, домашняя папка, токены и ключи наружу не уходят.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from koddi.logs.service import redact as redact_secrets

# пределы сервера (remote_api.py): data события до 16 КБ, text до 4000, карточка действия - поля ниже
DATA_BYTES = 15 * 1024
TEXT_CHARS = 4000
TARGET_CHARS = 500
PREVIEW_CHARS = 4000
ERROR_CHARS = 500
MESSAGE_CHARS = 60000
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
# сверх маскировки журнала: наружу текст уходит целиком (вывод команд, диффы), поэтому ловим больше видов секретов
EXTRA_SECRETS = (
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@"), r"\1[redacted]@"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[redacted]"),
    (re.compile(r"\b(?:xox[abposr]-[A-Za-z0-9-]{10,}|glpat-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{35}|[rs]k_(?:live|test)_[A-Za-z0-9]{10,}"
                r"|npm_[A-Za-z0-9]{36}|hf_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"), "[redacted]"),
    # строки .env и export: имя переменной говорит, что значение секретное
    (re.compile(r"\b((?:[A-Z0-9]+_)*(?:KEY|APIKEY|TOKEN|SECRET|PASS|PASSWD|PASSWORD|PWD|CREDENTIALS?|AUTH|PRIVATE|DSN)(?:_[A-Z0-9]+)*)"
                r"(\s*[:=]\s*)(?!\[redacted\])(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s,;]+)"), r"\1\2[redacted]"),
    # json, yaml и ini в любом регистре: "password": "...", db_pass = ..., aws_secret_access_key=...;
    # числа и true/false не трогаем, иначе прячутся счётчики вроде prompt_tokens
    (re.compile(r"(?i)([\"']?)(\b[\w.-]*?(?:password|passwd|(?<![a-z])(?:pass|pwd)(?![a-z])|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key"
                r"|credentials?)[\w.-]*)\1(\s*[:=]\s*)(?![\"']?\[redacted\])"
                r"(?!(?:\d+(?:\.\d+)?|true|false|null|none)(?:$|[\s,;}\])]))(\"[^\"\n]*\"|'[^'\n]*'|[^\s,;}\]]+)"),
     lambda match: match.group(1) + match.group(2) + match.group(1) + match.group(3) + _masked(match.group(4))),
    (re.compile(r"(?i)\b((?:proxy-)?authorization\s*[:=]\s*)(?:basic|digest|negotiate|ntlm|token)\s+[A-Za-z0-9+/=._~-]+"), r"\1[redacted]"),
    # пароль в аргументах команды: --password x, --token=x, mysql -pСЕКРЕТ
    (re.compile(r"(?i)(\s--?(?:password|passwd|pass|token|secret|api-key|apikey|access-key)(?:=|\s+))(?:\"[^\"\n]*\"|'[^'\n]*'|\S+)"), r"\1[redacted]"),
    (re.compile(r"(?i)\b(mysql(?:dump|admin)?\b[^\n]*?\s-p)(?!\s)\S+"), r"\1[redacted]"),
)


def _masked(value: str) -> str:
    quote = value[0] if value[:1] in {"\"", "'"} else ""
    return f"{quote}[redacted]{quote}"
# файлы, где содержимое почти наверняка секрет: превью их правок наружу не уходит
SECRET_FILE_RE = re.compile(
    r"(?i)(^|/)(\.env(\..*)?|\.npmrc|\.pypirc|\.netrc|\.pgpass|id_(rsa|dsa|ecdsa|ed25519)(\.pub)?|credentials(\.\w+)?"
    r"|[^/]*\.(pem|key|p12|pfx|jks|keystore))$"
)


def _variants(path: Path | str) -> list[str]:
    text = str(path).rstrip("/\\")
    if not text:
        return []
    forms = {text, text.replace("\\", "/"), text.replace("/", "\\")}
    return sorted((item for item in forms if len(item) > 1), key=len, reverse=True)


def _replace_prefix(text: str, prefix: str, value: str) -> str:
    flags = re.IGNORECASE if os.name == "nt" else 0
    # «/home/me/project» не должен совпадать с «/home/me/project2»
    pattern = re.escape(prefix) + r"(?=$|[\\/\s\"'`:;,)\]])"
    return re.sub(pattern, value.replace("\\", "\\\\"), text, flags=flags)


def clean_text(value: Any, workspace: Path | str | None = None, limit: int = TEXT_CHARS) -> str:
    """Свободный текст (команда, вывод, ошибка, дифф): папка проекта -> «.», домашняя -> «~», секреты скрыты."""
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    if workspace:
        for form in _variants(workspace):
            text = _replace_prefix(text, form, ".")
    for form in _variants(Path.home()):
        text = _replace_prefix(text, form, "~")
    text = redact_secrets(text)
    for pattern, replacement in EXTRA_SECRETS:
        text = pattern.sub(replacement, text)
    return text[:limit]


def secret_file(path: Any) -> bool:
    return bool(SECRET_FILE_RE.search(str(path or "").replace("\\", "/").strip()))


def diff_files(diff: str) -> list[str]:
    """Пути из заголовков диффа (`+++ b/...`, `--- a/...`, `diff --git`)."""
    found = []
    for line in diff.splitlines():
        if line.startswith(("+++ ", "--- ")):
            found.append(re.sub(r"^[ab]/", "", line[4:].split("\t")[0].strip()))
        elif line.startswith("diff --git "):
            found.extend(re.sub(r"^[ab]/", "", part) for part in line[11:].split())
    return [item for item in found if item and item != "/dev/null"]


def clean_path(value: Any, workspace: Path | str | None = None) -> str:
    """Путь для показа: относительный к проекту; вне проекта - от домашней папки или последние звенья."""
    text = str(value or "").strip()
    if not text:
        return ""
    windows = bool(re.match(r"^[A-Za-z]:[\\/]", text)) or text.startswith("\\\\")
    pure = PureWindowsPath(text) if windows else PurePosixPath(text.replace("\\", "/"))
    if not pure.is_absolute():
        return clean_text(text.replace("\\", "/").removeprefix("./"), workspace, TARGET_CHARS)
    for root, label in ((workspace, ""), (Path.home(), "~")):
        if not root:
            continue
        base = PureWindowsPath(str(root)) if windows else PurePosixPath(str(root).replace("\\", "/"))
        try:
            relative = pure.relative_to(base)
        except ValueError:
            continue
        joined = relative.as_posix()
        if label:
            return clean_text(f"~/{joined}" if joined != "." else "~", None, TARGET_CHARS)
        return clean_text(joined or ".", None, TARGET_CHARS)
    parts = [part for part in pure.parts[1:] if part not in {"\\", "/"}]
    return clean_text("…/" + "/".join(parts[-2:]), None, TARGET_CHARS)


def head(text: str, limit: int = PREVIEW_CHARS) -> str:
    """Начало текста по границе строки (дифф читается сверху)."""
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    return text[: cut if cut > limit // 2 else limit].rstrip("\n") + "\n…"


def tail(text: str, limit: int = PREVIEW_CHARS) -> str:
    """Конец текста по границе строки (у вывода команды важен хвост)."""
    if len(text) <= limit:
        return text
    start = text.find("\n", len(text) - limit)
    start = start + 1 if 0 <= start < len(text) - limit // 2 else len(text) - limit
    return "…\n" + text[start:]


def size(data: dict) -> int:
    return len(json.dumps(data, ensure_ascii=False, default=str).encode())


def fit(data: dict, limit: int = DATA_BYTES) -> dict:
    """Карточка не больше предела сервера: сначала ужимается превью, затем список файлов."""
    if size(data) <= limit:
        return data
    data = dict(data)
    preview = str(data.get("preview") or "")
    while preview and size(data) > limit:
        preview = preview[: len(preview) // 2]
        data["preview"] = (tail if data.get("preview_kind") == "output" else head)(preview, len(preview))
        if len(preview) < 200:
            data.pop("preview", None)
            data.pop("preview_kind", None)
            break
    files = list(data.get("files") or [])
    while files and size(data) > limit:
        files = files[: len(files) // 2]
        data["files"] = files
    if size(data) > limit:
        data = {key: data[key] for key in ("id", "state", "tool", "added", "removed", "duration_ms") if key in data}
    return data


def safe_id(value: str) -> str:
    """Идентификатор в формате сервера; длинный или с чужими символами - короткий хэш."""
    import hashlib

    if ID_RE.match(value):
        return value
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]

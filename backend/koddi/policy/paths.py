from __future__ import annotations

import os
import re
import stat
from pathlib import Path, PureWindowsPath


class PathAccessError(ValueError):
    pass


# служебные папки: поиск и обзор дерева их пропускают, чтобы не тонуть в шуме, но по прямому пути они открыты
NOISE_PARTS = {".git", ".venv", "node_modules", "__pycache__"}
IGNORE_FILES = (".gitignore", ".ignore", ".koddiignore")


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    # в Python 3.11 нет Path.is_junction; атрибут виден без перехода по ссылке
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _check_root(root: Path) -> None:
    for path in (*reversed(root.parents), root):
        if _is_reparse_point(path):
            raise PathAccessError("workspace reparse points are not allowed")


def _glob_regex(pattern: str) -> str:
    result = ""
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if pattern[i : i + 3] == "**/":
            result += "(?:.*/)?"
            i += 3
        elif pattern[i : i + 2] == "**":
            result += ".*"
            i += 2
        elif char == "*":
            result += "[^/]*"
            i += 1
        elif char == "?":
            result += "[^/]"
            i += 1
        elif char == "[" and "]" in pattern[i + 1 :]:
            end = pattern.index("]", i + 1)
            group = pattern[i + 1 : end]
            if group.startswith("!"):
                group = "^" + group[1:]
            result += "[" + group.replace("\\", "\\\\") + "]"
            i = end + 1
        elif char == "\\" and i + 1 < len(pattern):
            result += re.escape(pattern[i + 1])
            i += 2
        else:
            result += re.escape(char)
            i += 1
    return result


class WorkspacePaths:
    """Одна граница доступа и для прямых файловых инструментов, и для обхода каталогов."""

    def __init__(self, workspace: Path):
        self.root = Path(os.path.abspath(workspace))
        _check_root(self.root)
        self._rules: dict[Path, list[tuple[re.Pattern, bool, bool]]] = {}

    def _load_rules(self, directory: Path) -> list[tuple[re.Pattern, bool, bool]]:
        if _is_reparse_point(directory):
            raise PathAccessError("linked workspace ignore directory is not allowed")
        if directory in self._rules:
            return self._rules[directory]
        rules = []
        for name in IGNORE_FILES:
            path = directory / name
            if _is_reparse_point(path):
                raise PathAccessError("linked workspace ignore file is not allowed")
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                line = line.rstrip()
                if not line or line.startswith("#"):
                    continue
                negate = line.startswith("!")
                if negate:
                    line = line[1:]
                directories_only = line.endswith("/")
                line = line.rstrip("/")
                anchored = line.startswith("/") or "/" in line
                line = line.lstrip("/")
                if not line:
                    continue
                prefix = "^" if anchored else "(?:^|/)"
                flags = re.IGNORECASE if os.name == "nt" else 0
                rules.append(
                    (re.compile(prefix + _glob_regex(line) + "$", flags), negate, directories_only)
                )
        self._rules[directory] = rules
        return rules

    def resolve(self, value: str, *, check_ignore: bool = False) -> Path:
        """Путь внутри рабочей папки. Весь проект открыт: игнор-файлы, .git, .env и т.п. не закрывают прямой доступ.

        Закрыты только выход за пределы папки, ссылки/junction и зарезервированные имена Windows.
        ``check_ignore=True`` включает правила .gitignore: так работает обход (поиск, список файлов).
        """
        _check_root(self.root)
        if not isinstance(value, str) or not value or "\x00" in value:
            raise PathAccessError("invalid workspace path")
        win = PureWindowsPath(value)
        if os.name != "nt" and (win.drive or "\\" in value):
            raise PathAccessError("foreign platform path is not allowed")
        candidate = Path(os.path.abspath(self.root / value))
        try:
            relative = candidate.relative_to(self.root)
        except ValueError as exc:
            raise PathAccessError("path escapes workspace") from exc
        current = self.root
        for part in relative.parts:
            if ":" in part or part.rstrip(" .") != part:
                raise PathAccessError("unsafe path component")
            if os.name == "nt" and re.fullmatch(
                r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part
            ):
                raise PathAccessError("reserved Windows path")
            current /= part
            if _is_reparse_point(current):
                raise PathAccessError("workspace reparse points are not allowed")
        try:
            candidate.resolve().relative_to(self.root)
        except ValueError as exc:
            raise PathAccessError("path escapes workspace") from exc
        if check_ignore and relative.parts:
            ancestors = [self.root]
            current = self.root
            for index, part in enumerate(relative.parts):
                current /= part
                ignored = False
                for base in ancestors:
                    local = current.relative_to(base).as_posix()
                    for pattern, negate, directories_only in self._load_rules(base):
                        is_dir = index < len(relative.parts) - 1 or current.is_dir()
                        if (not directories_only or is_dir) and pattern.search(local):
                            ignored = not negate
                if ignored:
                    raise PathAccessError("path is excluded by workspace ignore rules")
                ancestors.append(current)
        return candidate

    def accessible(self, path: Path, *, ignore: bool = False) -> bool:
        try:
            self.resolve(str(path), check_ignore=ignore)
            return True
        except (ValueError, OSError):
            return False

    @staticmethod
    def is_noise(name: str) -> bool:
        return name.lower() in NOISE_PARTS

    def files(self, root: Path | None = None):
        start = self.resolve(str(root or self.root))
        for current, dirs, files in os.walk(start, followlinks=False):
            if not self.accessible(Path(current), ignore=True):
                dirs[:] = []
                continue
            dirs[:] = sorted(
                d for d in dirs if not self.is_noise(d) and self.accessible(Path(current) / d, ignore=True)
            )
            for name in sorted(files):
                path = Path(current) / name
                if self.accessible(path, ignore=True):
                    yield path


def external_path(value: str) -> Path:
    """Файл вне проекта по абсолютному пути (настройки программ, игр и т.п.).

    Доступ к таким путям решает политика разрешений; здесь только проверка самого пути. Папка данных Докки
    закрыта всегда: там сессия аккаунта и разрешения, правка которых дала бы агенту права в обход человека.
    """
    if not isinstance(value, str) or not value or "\x00" in value:
        raise PathAccessError("invalid path")
    if os.name != "nt" and (PureWindowsPath(value).drive or "\\" in value):
        raise PathAccessError("foreign platform path is not allowed")
    candidate = Path(os.path.expandvars(os.path.expanduser(value)))
    if not candidate.is_absolute():
        raise PathAccessError("path escapes workspace; use an absolute path for files outside the project")
    resolved = candidate.resolve()
    for part in resolved.parts[1:]:
        if ":" in part or part.rstrip(" .") != part:
            raise PathAccessError("unsafe path component")
        if os.name == "nt" and re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part):
            raise PathAccessError("reserved Windows path")
    from koddi.config import data_dir

    private = data_dir().resolve()
    if resolved == private or resolved.is_relative_to(private):
        raise PathAccessError("Dokky application data is not accessible to the agent")
    return resolved

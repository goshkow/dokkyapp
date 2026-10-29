"""Безопасные git-операции агента: атомарные коммиты своих файлов, идемпотентные worktree и их очистка.

- git.add только по явным путям: «git add .», маски и pathspec-магия отклоняются - иначе в коммит уезжает всё
  подряд, включая чужие правки и секреты.
- git.commit коммитит только переданные пути (`--only`): то, что человек сам подготовил в индексе, остаётся
  в индексе нетронутым. Без путей коммит разрешён, только если в индексе нет чужих файлов. Никаких --amend,
  --no-verify и push: публикацию делает человек.
- worktree: повторное создание того же пути и ветки возвращает существующий; запись в базе сверяется
  с `git worktree list`. Очистка удаляет только чистые worktree без незамерженных коммитов и без --force.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from koddi.storage.db import db

MAX_SUBJECT = 100
MAX_BODY = 4000
FORBIDDEN_PATHS = {".", "./", "*", "-A", "--all", "-u", "--update", ":/", ":"}


# core.fsmonitor - команда из .git/config, которую git запускает даже на status/diff: git, который зовёт сама Докки,
# её не исполняет (встроенный fsmonitor просто не используется, git сканирует дерево сам)
SAFE_CONFIG = ("-c", "core.fsmonitor=false")


def git_env() -> dict[str, str]:
    # без необязательных блокировок индекса: фоновые git status не мешают пользователю
    # пути - буквально: маски и pathspec-магию выключает сам git, а «app/[id]/page.tsx» остаётся обычным файлом
    return {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_LITERAL_PATHSPECS": "1"}


def _git(root: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    from koddi.tools.runner import HIDDEN_SUBPROCESS

    # git пишет UTF-8, а text=True без кодировки на Windows читает в ANSI-кодовой странице
    return subprocess.run(["git", *SAFE_CONFIG, *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, check=False, env=git_env(),
                          creationflags=HIDDEN_SUBPROCESS)


def branch_error(branch: str) -> str | None:
    """Имя ветки, которое git не примет за опцию или путь: «-f», «.» и «--orphan» из аргумента агента не исполняются."""
    if (not branch or branch.startswith(("-", "/", ".")) or branch.endswith(("/", ".", ".lock")) or ".." in branch
            or "@{" in branch or "//" in branch or branch == "@"
            or any(char in branch for char in " ~^:?*[\\") or any(ord(char) < 32 or ord(char) == 127 for char in branch)):
        return "некорректное имя ветки"
    return None


def index_text(root: Path, raw: str) -> tuple[str, str]:
    """(путь от рабочей папки, текст файла из индекса git) - как его вернул бы `git restore`, с eol и фильтрами."""
    relative = _normalize(root, raw)
    proc = subprocess.run(["git", *SAFE_CONFIG, "cat-file", "--filters", ":./" + relative], cwd=root,
                          capture_output=True, timeout=30, check=False, env=git_env(), creationflags=_hidden())
    if proc.returncode != 0:
        raise ValueError(f"{relative}: файла нет в индексе git")
    if b"\x00" in proc.stdout:
        raise ValueError(f"{relative}: двоичный файл - текстовые инструменты его не восстанавливают")
    try:
        return relative, proc.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{relative}: файл не в UTF-8") from exc


def _hidden() -> int:
    from koddi.tools.runner import HIDDEN_SUBPROCESS

    return HIDDEN_SUBPROCESS


def _result(proc: subprocess.CompletedProcess, **extra: Any) -> dict[str, Any]:
    return {"ok": proc.returncode == 0, "stdout": proc.stdout[-6000:], "stderr": proc.stderr[-3000:],
            "returncode": proc.returncode, **extra}


def _normalize(root: Path, raw: str) -> str:
    value = str(raw or "").strip().replace("\\", "/")
    if not value or value in FORBIDDEN_PATHS or value.startswith((":", "-")):
        raise ValueError(f"путь {raw!r}: укажи конкретные файлы - «всё подряд» и pathspec-магия запрещены")
    target = (root / value).resolve()
    try:
        relative = target.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"путь {raw!r} вне рабочей папки") from exc
    if not relative or relative == ".":
        raise ValueError("путь указывает на всю папку проекта")
    if target.is_dir():
        raise ValueError(f"{relative} - папка: добавляй файлы по одному, чтобы коммит был атомарным")
    return relative


def staged(root: Path) -> list[str]:
    """Весь индекс репозитория в путях от рабочей папки (вне её - с «../»): папка может быть подпапкой репозитория."""
    prefix = _prefix(root)
    proc = _git(root, "diff", "--cached", "--name-only", "-z")
    if proc.returncode != 0:
        return []
    return [_from_top(item, prefix) for item in proc.stdout.split("\0") if item]


def _prefix(root: Path) -> str:
    return _git(root, "rev-parse", "--show-prefix").stdout.strip()


def _from_top(path: str, prefix: str) -> str:
    if not prefix or path.startswith(prefix):
        return path[len(prefix):]
    return "../" * prefix.rstrip("/").count("/") + "../" + path


def add(root: Path, paths: list[str] | None, *, accessible=None) -> dict[str, Any]:
    if not paths:
        return {"ok": False, "error": "git.add требует явный список файлов (paths). «git add .» не выполняется."}
    try:
        normalized = [_normalize(root, item) for item in paths[:200]]
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    hidden = [item for item in normalized if accessible and not accessible(item)]
    if hidden:
        return {"ok": False, "error": "файлы закрыты настройками доступа: " + ", ".join(hidden[:5])}
    proc = _git(root, "add", "--", *normalized)
    return _result(proc, staged=normalized if proc.returncode == 0 else [])


def commit(root: Path, message: str, *, paths: list[str] | None, body: str | None, own: set[str],
           accessible=None) -> dict[str, Any]:
    """own - файлы, которые в этом ходе менял или добавлял в индекс агент."""
    subject = " ".join(str(message or "").split())
    if not subject:
        return {"ok": False, "error": "пустое сообщение коммита"}
    if len(subject) > MAX_SUBJECT:
        return {"ok": False, "error": f"первая строка коммита длиннее {MAX_SUBJECT} символов - вынеси подробности в body"}
    index = staged(root)
    args = ["commit", "-m", subject]
    if body and body.strip():
        args += ["-m", body.strip()[:MAX_BODY]]
    if paths:
        try:
            normalized = [_normalize(root, item) for item in paths[:200]]
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        hidden = [item for item in normalized if accessible and not accessible(item)]
        if hidden:
            return {"ok": False, "error": "файлы закрыты настройками доступа: " + ", ".join(hidden[:5])}
        foreign = [item for item in normalized if item not in own and item not in index]
        if foreign:
            return {"ok": False, "error": "в коммит попали бы файлы, которые агент не менял: " + ", ".join(foreign[:8])}
        # --only коммитит ровно эти пути; остальное, что человек положил в индекс, остаётся в индексе
        proc = _git(root, *args, "--only", "--", *normalized, timeout=120)
        kept = sorted(set(index) - set(normalized))
        if proc.returncode != 0 and _already_committed(root, subject, normalized):
            return _reconciled(root, normalized, kept)
        return _result(proc, committed=normalized if proc.returncode == 0 else [], kept_staged=kept[:50])
    if not index:
        return {"ok": False, "error": "индекс пуст: сначала git.add с конкретными файлами или передай paths"}
    foreign = [item for item in index if item not in own]
    if foreign:
        return {"ok": False, "error": "в индексе есть файлы, которые агент не добавлял (их подготовил человек): "
                + ", ".join(foreign[:8]) + ". Передай paths со своими файлами - чужие останутся в индексе."}
    proc = _git(root, *args, timeout=120)
    return _result(proc, committed=index if proc.returncode == 0 else [])


def _already_committed(root: Path, subject: str, paths: list[str]) -> bool:
    """Повтор после обрыва: коммит с тем же сообщением уже есть, а у путей нет изменений относительно него."""
    head = _git(root, "log", "-1", "--format=%s")
    if head.returncode != 0 or head.stdout.strip() != subject:
        return False
    changed = _git(root, "diff", "--name-only", "HEAD", "--", *paths)
    # -z: имена с пробелами и кириллицей без кавычек и экранирования; --relative - пути от рабочей папки
    touched = _git(root, "show", "--name-only", "--format=", "-z", "--relative", "HEAD")
    return changed.returncode == 0 and not changed.stdout.strip() \
        and set(paths) <= {item.strip("\n") for item in touched.stdout.split("\0") if item.strip("\n")}


def _reconciled(root: Path, paths: list[str], kept: list[str]) -> dict[str, Any]:
    sha = _git(root, "rev-parse", "HEAD").stdout.strip()
    return {"ok": True, "already_committed": True, "commit": sha, "committed": [], "kept_staged": kept[:50],
            "stdout": "", "stderr": "", "returncode": 0,
            "note": "этот коммит уже создан прошлой попыткой - второй не создаётся"}


# ---------- worktree

def worktrees(root: Path) -> list[dict[str, Any]]:
    """`git worktree list --porcelain`: путь, ветка, HEAD, признаки prunable/locked."""
    proc = _git(root, "worktree", "list", "--porcelain")
    items: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in proc.stdout.splitlines() + [""]:
        if not line:
            if current:
                items.append(current)
            current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = str(Path(value).resolve())
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key == "HEAD":
            current["head"] = value
        elif key in {"prunable", "locked", "detached", "bare"}:
            current[key] = value or True
    return items


def _same(left: str, right: str) -> bool:
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(str(Path(right).resolve()))


def ensure_worktree(root: Path, target: Path, branch: str | None) -> dict[str, Any]:
    """Создаёт worktree или возвращает уже существующий на той же ветке (повтор после сбоя безопасен).
    Без ветки - отсоединённый HEAD текущего коммита."""
    if not branch or branch == "HEAD":
        for item in worktrees(root):
            if _same(item["path"], str(target)):
                return {"ok": True, "reused": True, "path": item["path"], "branch": item.get("branch")}
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            return {"ok": False, "error": "по пути назначения уже есть файл или непустая папка"}
        proc = _git(root, "worktree", "add", "--detach", str(target), "HEAD", timeout=180)
        if proc.returncode != 0:
            return {"ok": False, "error": (proc.stderr or proc.stdout).strip()[-1000:]}
        return {"ok": True, "reused": False, "path": str(target.resolve()), "branch": None}
    if error := branch_error(branch):
        return {"ok": False, "error": error}
    for item in worktrees(root):
        if _same(item["path"], str(target)):
            if item.get("branch") == branch:
                return {"ok": True, "reused": True, "path": item["path"], "branch": branch}
            return {"ok": False, "error": f"по этому пути уже есть worktree на ветке {item.get('branch') or 'detached'}"}
        if item.get("branch") == branch:
            return {"ok": False, "error": f"ветка {branch} уже открыта в другом worktree: {item['path']}"}
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        return {"ok": False, "error": "по пути назначения уже есть файл или непустая папка"}
    exists = _git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0
    args = ["worktree", "add", str(target), branch] if exists else ["worktree", "add", "-b", branch, str(target)]
    proc = _git(root, *args, timeout=180)
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout).strip()[-1000:]}
    return {"ok": True, "reused": False, "path": str(target.resolve()), "branch": branch}


def unreachable_commits(path: Path) -> int | None:
    """Коммиты HEAD рабочей копии, которых нет ни в одной ветке: при удалении отсоединённого worktree они теряются."""
    proc = _git(path, "rev-list", "--count", "HEAD", "--not", "--branches", "--remotes", "--tags")
    try:
        return int(proc.stdout.strip()) if proc.returncode == 0 else None
    except ValueError:
        return None


def remove_worktree(root: Path, path: Path, *, force: bool = False) -> dict[str, Any]:
    """Удаление одной копии: не основной, без потери коммитов отсоединённого HEAD; --force - только по явной
    просьбе человека и только против незакоммиченных правок, не против потери коммитов."""
    target = Path(path)
    if not target.is_absolute():
        target = root / target
    items = worktrees(root)
    known = next((item for item in items if _same(item["path"], str(target))), None)
    if known is None:
        return {"ok": False, "error": "такого worktree у репозитория нет"}
    # первая запись `git worktree list` - основная рабочая копия
    if _same(known["path"], str(root)) or _same(known["path"], items[0]["path"]):
        return {"ok": False, "error": "основную рабочую копию удалить нельзя"}
    if known.get("detached"):
        lost = unreachable_commits(Path(known["path"]))
        if lost is None or lost:
            return {"ok": False, "error": "отсоединённый HEAD с коммитами вне веток - они потеряются; сначала создай ветку "
                                          f"(коммитов: {lost if lost is not None else 'не удалось посчитать'})"}
    proc = _git(root, "worktree", "remove", *(["--force"] if force else []), known["path"], timeout=120)
    return _result(proc, removed=known["path"] if proc.returncode == 0 else None)


def _dirty(path: Path) -> int | None:
    proc = _git(path, "status", "--porcelain")
    return None if proc.returncode != 0 else len([line for line in proc.stdout.splitlines() if line.strip()])


def _ahead(root: Path, base: str, branch: str) -> int | None:
    proc = _git(root, "rev-list", "--count", f"{base}..refs/heads/{branch}")
    try:
        return int(proc.stdout.strip()) if proc.returncode == 0 else None
    except ValueError:
        return None


def cleanup(project_id: int, root: Path, *, dry_run: bool = True, active_paths: set[str] | None = None) -> dict[str, Any]:
    """Удаляет только worktree, созданные Докки: чистые, без коммитов сверх текущей ветки проекта и не занятые
    идущим ходом. Ветки не удаляются, --force не используется; всё остальное попадает в «оставлено» с причиной."""
    with db() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM worktrees WHERE project_id=?", (project_id,)).fetchall()]
    registered = {os.path.normcase(item["path"]): item for item in worktrees(root)}
    base = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "HEAD"
    removed, kept, pruned = [], [], []
    for row in rows:
        path = Path(row["path"])
        key = os.path.normcase(str(path.resolve()))
        git_item = registered.get(key)
        if not path.exists() or git_item is None or git_item.get("prunable"):
            pruned.append(row["path"])
            if not dry_run:
                with db() as conn:
                    conn.execute("DELETE FROM worktrees WHERE id=?", (row["id"],))
            continue
        reason = None
        if active_paths and key in active_paths:
            reason = "занят идущим ходом агента"
        elif git_item.get("locked"):
            reason = "worktree заблокирован (git worktree lock)"
        else:
            dirty = _dirty(path)
            # ветку берём только из git: worktree, ставший отсоединённым, мог получить коммиты вне веток
            branch = git_item.get("branch")
            ahead = _ahead(root, base, branch) if branch else None
            if dirty is None:
                reason = "git status не прочитан"
            elif dirty:
                reason = f"есть незакоммиченные изменения ({dirty})"
            elif not branch:
                reason = "отсоединённый HEAD - коммиты можно потерять"
            elif ahead is None:
                reason = "не удалось сравнить ветку с основной"
            elif ahead:
                reason = f"в ветке {branch} есть коммиты, которых нет в {base} ({ahead})"
        if reason:
            kept.append({"path": row["path"], "branch": row.get("branch"), "reason": reason})
            continue
        if not dry_run:
            proc = _git(root, "worktree", "remove", str(path), timeout=120)
            if proc.returncode != 0:
                kept.append({"path": row["path"], "branch": row.get("branch"),
                             "reason": (proc.stderr or proc.stdout).strip()[-300:]})
                continue
            with db() as conn:
                conn.execute("DELETE FROM worktrees WHERE id=?", (row["id"],))
        removed.append({"path": row["path"], "branch": row.get("branch")})
    if pruned and not dry_run:
        _git(root, "worktree", "prune")
    return {"ok": True, "dry_run": dry_run, "removed": removed, "kept": kept, "stale_records": pruned, "base": base}


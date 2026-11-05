"""Git-инструменты агента, которые ходят на сервер или сливают ветки: push, pull, fetch, merge, stash, remote.

Границы те же, что у кнопок в окне (gitpanel): никакого --force, --amend и --no-verify, push только ветки в
одноимённую ветку настроенного remote, pull только fast-forward (rebase - явным флагом), merge без автокоммита
конфликтов. Remote - только из `git remote`: произвольный адрес в аргументе не принимается.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from koddi.projects.gitpanel import URL_CREDENTIALS, _push_error, _remote, _upstream, clean_output
from koddi.projects.gitsafe import _git, branch_error

NETWORK_TIMEOUT = 180
MAX_CONFLICTS = 50
STASH_ACTIONS = ("push", "pop", "list")
AUTH_MARKS = ("permission denied", "could not read", "authentication failed", "publickey", "terminal prompts disabled")


def _fail(error: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": error, "stdout": "", "stderr": error, **extra}


def _git_plain(root: Path, *args: str, timeout: int) -> subprocess.CompletedProcess:
    """git без GIT_LITERAL_PATHSPECS: с ним `stash -u` молча не прячет неотслеживаемые файлы, хотя сообщает об успехе."""
    from koddi.projects.gitsafe import SAFE_CONFIG, git_env
    from koddi.tools.runner import HIDDEN_SUBPROCESS

    env = {key: value for key, value in git_env().items() if key != "GIT_LITERAL_PATHSPECS"}
    return subprocess.run(["git", *SAFE_CONFIG, *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
                          stdin=subprocess.DEVNULL, errors="replace", timeout=timeout, check=False, env=env,
                          creationflags=HIDDEN_SUBPROCESS)


def _run(root: Path, *args: str, timeout: int = 60, literal: bool = True) -> subprocess.CompletedProcess | dict[str, Any]:
    try:
        return _git(root, *args, timeout=timeout) if literal else _git_plain(root, *args, timeout=timeout)
    except subprocess.TimeoutExpired:
        return _fail(f"git не ответил за {timeout} с: возможно, он ждёт пароль или сеть недоступна")
    except OSError:
        return _fail("Не удалось запустить git: он не установлен или недоступен")


def _done(proc: subprocess.CompletedProcess, **extra: Any) -> dict[str, Any]:
    out, err = clean_output(proc.stdout), clean_output(proc.stderr)
    return {"ok": proc.returncode == 0, "stdout": out, "stderr": err, "returncode": proc.returncode, **extra}


def _network_error(output: str, fallback: str) -> str:
    lowered = output.lower()
    if any(mark in lowered for mark in AUTH_MARKS):
        return "Нет доступа к серверу: проверьте вход или ключ в терминале"
    if "could not resolve host" in lowered or "unable to access" in lowered or "network is unreachable" in lowered:
        return "Сервер недоступен: проверьте сеть"
    return output or fallback


def current_branch(root: Path) -> str | None:
    result = _run(root, "symbolic-ref", "--short", "-q", "HEAD")
    return None if isinstance(result, dict) else result.stdout.strip() or None


def remotes(root: Path) -> list[str]:
    result = _run(root, "remote")
    return [] if isinstance(result, dict) else [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _pick_remote(root: Path, requested: str | None, branch: str | None) -> tuple[str | None, str | None]:
    """(remote, ошибка): только настроенный в репозитории, по умолчанию - remote ветки."""
    known = remotes(root)
    if not known:
        return None, "У репозитория нет удалённого сервера (git remote пуст)"
    if requested:
        name = str(requested).strip()
        if name not in known:
            return None, f"Remote «{name}» не настроен. Есть: {', '.join(known)}"
        return name, None
    return _remote(root, branch), None


def _conflicts(root: Path) -> list[str]:
    result = _run(root, "diff", "--name-only", "--diff-filter=U", "-z")
    if isinstance(result, dict):
        return []
    return [item for item in result.stdout.split("\0") if item][:MAX_CONFLICTS]


def push(root: Path, branch: str | None = None, remote: str | None = None) -> dict[str, Any]:
    name = (str(branch).strip() if branch else "") or current_branch(root)
    if not name:
        return _fail("HEAD не на ветке: переключитесь на ветку или укажите branch")
    if error := branch_error(name):
        return _fail(error)
    if isinstance(exists := _run(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"), dict) or exists.returncode != 0:
        return _fail(f"Локальной ветки «{name}» нет")
    target, error = _pick_remote(root, remote, name)
    if error or not target:
        return _fail(error or "Не удалось выбрать remote")
    tracked = _upstream(root, name, target)
    # только эта ветка в одноимённую; без --force, тегов и «всех веток». upstream ставится при первой отправке
    result = _run(root, "push", "--porcelain", *(() if tracked else ("--set-upstream",)), target,
                  f"refs/heads/{name}:refs/heads/{name}", timeout=NETWORK_TIMEOUT)
    if isinstance(result, dict):
        return result
    if result.returncode != 0:
        text = clean_output(result.stderr or result.stdout)
        error = _push_error(text) if "rejected" in text.lower() or "fetch first" in text.lower() else _network_error(text, "Отправка не удалась")
        return _fail(error, branch=name, remote=target)
    return _done(result, branch=name, remote=target, upstream_set=not tracked)


def pull(root: Path, remote: str | None = None, branch: str | None = None, rebase: bool = False) -> dict[str, Any]:
    current = current_branch(root)
    if not current:
        return _fail("HEAD не на ветке: pull невозможен")
    target, error = _pick_remote(root, remote, current)
    if error or not target:
        return _fail(error or "Не удалось выбрать remote")
    source = (str(branch).strip() if branch else "") or current
    if error := branch_error(source):
        return _fail(error)
    mode = "--rebase" if rebase else "--ff-only"
    result = _run(root, "pull", mode, target, source, timeout=NETWORK_TIMEOUT)
    if isinstance(result, dict):
        return result
    if result.returncode != 0:
        text = clean_output(result.stderr or result.stdout)
        lowered = text.lower()
        if "not possible to fast-forward" in lowered or "diverging" in lowered:
            text = "Ветки разошлись, fast-forward невозможен. Сделайте git.merge или pull с rebase=true"
        elif "would be overwritten" in lowered:
            text = "Pull затёр бы незакоммиченные правки: закоммитьте их или отложите через git.stash"
        elif "couldn't find remote ref" in lowered:
            text = f"На {target} нет ветки «{source}»"
        return _fail(_network_error(text, "Pull не удался"), conflicts=_conflicts(root))
    return _done(result, branch=current, remote=target)


def fetch(root: Path, remote: str | None = None) -> dict[str, Any]:
    target, error = _pick_remote(root, remote, current_branch(root))
    if error or not target:
        return _fail(error or "Не удалось выбрать remote")
    result = _run(root, "fetch", "--prune", target, timeout=NETWORK_TIMEOUT)
    if isinstance(result, dict):
        return result
    if result.returncode != 0:
        return _fail(_network_error(clean_output(result.stderr or result.stdout), "Fetch не удался"), remote=target)
    return _done(result, remote=target)


def merge(root: Path, branch: str | None = None, *, abort: bool = False, ff_only: bool = False) -> dict[str, Any]:
    if abort:
        result = _run(root, "merge", "--abort")
        return result if isinstance(result, dict) else _done(result, aborted=result.returncode == 0)
    name = str(branch or "").strip()
    if error := branch_error(name):
        return _fail(error)
    # конфликтные файлы остаются в дереве с маркерами: чинит их человек или агент правкой, коммитит отдельно
    result = _run(root, "merge", "--no-edit", *(("--ff-only",) if ff_only else ()), name, timeout=NETWORK_TIMEOUT)
    if isinstance(result, dict):
        return result
    if result.returncode == 0:
        return _done(result, conflicts=[])
    files = _conflicts(root)
    text = clean_output(result.stdout or result.stderr)
    if files:
        return _fail("Слияние остановилось на конфликтах: исправьте файлы и закоммитьте, либо отмените через abort=true",
                     conflicts=files, returncode=result.returncode)
    if "not possible to fast-forward" in text.lower():
        text = "fast-forward невозможен: ветки разошлись"
    elif "would be overwritten" in text.lower():
        text = "Слияние затёрло бы незакоммиченные правки: закоммитьте их или отложите через git.stash"
    return _fail(text or "Слияние не удалось", conflicts=[], returncode=result.returncode)


def stash(root: Path, action: str = "list", message: str | None = None, include_untracked: bool = False) -> dict[str, Any]:
    if action not in STASH_ACTIONS:
        return _fail(f"action: одно из {', '.join(STASH_ACTIONS)}")
    if action == "list":
        result = _run(root, "stash", "list", "--format=%gd|%cr|%gs")
        return result if isinstance(result, dict) else _done(result, items=_stash_items(result.stdout))
    if action == "push":
        note = " ".join(str(message or "").split())[:200]
        args = ["stash", "push", *(("--include-untracked",) if include_untracked else ()), *(("-m", note) if note else ())]
        result = _run(root, *args, literal=not include_untracked)
        if isinstance(result, dict):
            return result
        saved = result.returncode == 0 and "no local changes" not in result.stdout.lower()
        return _done(result, saved=saved)
    result = _run(root, "stash", "pop")
    if isinstance(result, dict):
        return result
    if result.returncode != 0:
        files = _conflicts(root)
        # при конфликте git оставляет запись в stash: ничего не потеряно
        return _fail(clean_output(result.stderr or result.stdout) or "Stash не применился", conflicts=files)
    return _done(result)


def _stash_items(text: str) -> list[dict[str, str]]:
    items = []
    for line in text.splitlines()[:30]:
        ref, _, rest = line.partition("|")
        when, _, subject = rest.partition("|")
        items.append({"ref": ref.strip(), "when": when.strip(), "message": URL_CREDENTIALS.sub("", subject).strip()})
    return items


def remote_list(root: Path) -> dict[str, Any]:
    result = _run(root, "remote", "-v")
    if isinstance(result, dict):
        return result
    if result.returncode != 0:
        return _fail(clean_output(result.stderr) or "Не удалось прочитать remote")
    found: dict[str, dict[str, str]] = {}
    for line in result.stdout.splitlines():
        match = re.match(r"^(\S+)\s+(\S+)\s+\((fetch|push)\)$", line.strip())
        if match:
            # токен в адресе (https://user:token@host) наружу не уходит
            found.setdefault(match[1], {"name": match[1]})[match[3]] = URL_CREDENTIALS.sub("", match[2])
    items = list(found.values())
    return {"ok": True, "remotes": items, "stdout": "\n".join(f"{item['name']}\t{item.get('fetch', '')}" for item in items), "stderr": ""}

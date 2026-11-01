"""Git в окне: действия человека кнопками - ветка, коммит выбранных файлов, отправка.

В отличие от инструментов агента (gitsafe) здесь решает человек, но границы те же: только явные файлы,
никаких --force и --amend, чужой индекс не трогаем. Push - только текущая ветка в её же ветку на remote,
и только если ветка и remote совпали с тем, что человек видел в окне подтверждения.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from koddi.logs.service import redact
from koddi.projects.gitsafe import MAX_BODY, _from_top, _git, _prefix, add, branch_error, commit as safe_commit
from koddi.storage.db import db

MAX_FILES = 300
MAX_BRANCHES = 300
SUBJECT_SUGGESTION = 100
MAX_NAMED_FILES = 3
CHANGES_HEADING = re.compile(r"^#+\s*(что (было )?(изменено|сделано)|изменения|what changed|changes)\b", re.IGNORECASE)
STATUS_ONLY = re.compile(
    r"^(всё |все )?(готово|сделано|исправлено|выполнено|done|fixed|finished|тесты?( \w+)? (проход\w*|прош\w*|зелён\w*)|"
    r"все тесты \w+|(all )?tests? pass\w*|проверки? (проход\w*|прош\w*))[.!:]*$", re.IGNORECASE)
# такие файлы не отмечаются для коммита сами: человек может выбрать их только руками
SENSITIVE = re.compile(r"(^|/)(\.env(\.[^/]*)?|id_(rsa|dsa|ecdsa|ed25519)|credentials(\.json)?|[^/]*\.(pem|key|p12|pfx))$", re.I)
URL_CREDENTIALS = re.compile(r"(?<=://)[^/@\s]+@")


def clean_output(text: str) -> str:
    """Вывод git и адреса remote могут содержать токен в URL: в интерфейс и журнал он не уходит."""
    return redact(URL_CREDENTIALS.sub("", text or "")).strip()[-2000:]


def _lines(proc: subprocess.CompletedProcess) -> list[str]:
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()] if proc.returncode == 0 else []


def _files(root: Path) -> tuple[list[dict[str, str]], bool]:
    prefix = _prefix(root)
    # --no-renames: путь старого и нового файла отдельными строками, коммит получает оба
    proc = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames", "--", ".")
    if proc.returncode != 0:
        return [], False
    files = []
    for entry in proc.stdout.split("\0"):
        if len(entry) < 4:
            continue
        code, path = entry[:2], _from_top(entry[3:], prefix)
        status = "new" if code in {"??", "A ", "AM"} else "deleted" if "D" in code else "modified"
        files.append({"path": path, "status": status})
    return files[:MAX_FILES], len(files) > MAX_FILES


def _branches(root: Path, current: str | None) -> list[str]:
    local = _lines(_git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads"))
    known = set(local)
    # ветка с remote: git switch сам создаст локальную с тем же именем
    remote = []
    for name in _lines(_git(root, "for-each-ref", "--format=%(refname:lstrip=3)", "refs/remotes")):
        if name != "HEAD" and name not in known:
            known.add(name)
            remote.append(name)
    ordered = sorted(local, key=str.casefold) + sorted(remote, key=str.casefold)
    return ([current] if current else []) + [name for name in ordered if name != current][:MAX_BRANCHES]


def _remote(root: Path, branch: str | None) -> str | None:
    remotes = _lines(_git(root, "remote"))
    if branch:
        configured = _git(root, "config", "--get", f"branch.{branch}.remote").stdout.strip()
        if configured in remotes:
            return configured
    return "origin" if "origin" in remotes else remotes[0] if remotes else None


def _upstream(root: Path, branch: str | None, remote: str | None) -> str | None:
    """Ветка на remote, если текущая отслеживает одноимённую. Ветка, созданная от origin/main, отслеживает main:
    кнопка отправки не должна молча залить её в main, поэтому такое отслеживание здесь не считается."""
    if not branch or not remote or _git(root, "config", "--get", f"branch.{branch}.remote").stdout.strip() != remote:
        return None
    merge = _git(root, "config", "--get", f"branch.{branch}.merge").stdout.strip()
    return branch if merge == f"refs/heads/{branch}" else None


def chat_own_paths(chat_id: int | None, project_id: int) -> set[str]:
    if chat_id is None:
        return set()
    with db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT f.path FROM file_changes f JOIN change_sets c ON c.id=f.change_set_id JOIN turns t ON t.id=c.turn_id "
            "WHERE t.chat_id=? AND c.project_id=? AND c.status='applied'", (chat_id, project_id)).fetchall()
    return {row["path"] for row in rows}


def suggestion(chat_id: int | None) -> str:
    """Сообщение коммита от агента: его commit_message, иначе первая фраза последнего итога с правками."""
    if chat_id is None:
        return ""
    with db() as conn:
        row = conn.execute(
            "SELECT t.id,q.commit_message FROM turns t JOIN change_sets c ON c.turn_id=t.id AND c.status='applied' "
            "LEFT JOIN turn_quality q ON q.turn_id=t.id WHERE t.chat_id=? ORDER BY t.id DESC LIMIT 1", (chat_id,)).fetchone()
        if row is None:
            return ""
        if row["commit_message"]:
            return row["commit_message"]
        final = conn.execute(
            "SELECT content FROM messages WHERE chat_id=? AND role='assistant' AND json_extract(metadata_json,'$.turn_id')=? "
            "ORDER BY id DESC LIMIT 1", (chat_id, row["id"])).fetchone()
        files = [item["path"] for item in conn.execute(
            "SELECT DISTINCT f.path FROM file_changes f JOIN change_sets c ON c.id=f.change_set_id "
            "WHERE c.turn_id=? AND c.status='applied' ORDER BY f.path LIMIT ?", (row["id"], MAX_NAMED_FILES + 1))]
    # в итоге нет фразы про правку: честнее назвать файлы, чем подставить «Тест проходит»
    named = "Правка " + ", ".join(files) if files and len(files) <= MAX_NAMED_FILES else ""
    subject = first_sentence(final["content"] if final else "") or named
    return subject if len(subject) <= SUBJECT_SUGGESTION else ""


def _subject(line: str) -> str:
    """Тема коммита из строки итога: первая фраза, а длинная - до первого двоеточия. «» - если это только про проверку."""
    line = re.sub(r"^[>*\-\s]+", "", line).replace("**", "").replace("`", "").strip()
    for sentence in re.split(r"(?<=[.!?])\s", line):
        sentence = sentence.rstrip(".").replace(" — ", " - ").strip()
        if not sentence or STATUS_ONLY.match(sentence):
            continue
        return sentence if len(sentence) <= SUBJECT_SUGGESTION else sentence.split(": ", 1)[0]
    return ""


def first_sentence(text: str) -> str:
    """Тема из итога агента: сначала первый пункт раздела «Что изменено» (базовый промпт просит такой раздел), иначе
    первая фраза. «Тест проходит» и «Готово» - про проверку, в коммите они ни о чём не скажут."""
    lines = (text or "").splitlines()
    for index, line in enumerate(lines):
        if CHANGES_HEADING.match(line.strip()):
            item = next((value for value in lines[index + 1:] if value.strip()), "")
            subject = "" if item.lstrip().startswith("#") else _subject(item)
            if subject and len(subject) <= SUBJECT_SUGGESTION:
                return subject
            break
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        subject = _subject(line)
        if subject:
            return subject if len(subject) <= SUBJECT_SUGGESTION else ""
    return ""


def state(root: Path, *, chat_id: int | None = None, project_id: int | None = None) -> dict[str, Any]:
    if _git(root, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        return {"repo": False}
    branch = _git(root, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip() or None
    head = _git(root, "rev-parse", "--short", "HEAD").stdout.strip()
    remote = _remote(root, branch)
    upstream = _upstream(root, branch, remote)
    ahead = behind = None
    if upstream:
        counts = _git(root, "rev-list", "--left-right", "--count", "@{upstream}...HEAD").stdout.split()
        if len(counts) == 2:
            behind, ahead = int(counts[0]), int(counts[1])
    files, truncated = _files(root)
    own = chat_own_paths(chat_id, project_id) if project_id is not None else set()
    dirty = {item["path"] for item in files}
    for item in files:
        # чат что-то менял: предлагаем только его файлы, а незаконченные правки человека оставляем ему
        wanted = item["path"] in own if own & dirty else item["status"] != "new"
        item["suggested"] = wanted and not SENSITIVE.search(item["path"])
    url = _git(root, "remote", "get-url", "--push", remote).stdout.strip() if remote else ""
    return {"repo": True, "branch": branch, "head": head, "branches": _branches(root, branch), "files": files,
            "truncated": truncated, "remote": remote, "remote_url": URL_CREDENTIALS.sub("", url), "upstream": upstream,
            "ahead": ahead, "behind": behind, "suggestion": suggestion(chat_id)}


def switch(root: Path, branch: str, *, create: bool = False) -> dict[str, Any]:
    if error := branch_error(branch):
        return {"ok": False, "error": error}
    # без --force: git сам откажется, если переключение затёрло бы незакоммиченные правки
    proc = _git(root, "switch", *(("-c",) if create else ()), branch)
    if proc.returncode != 0:
        output = clean_output(proc.stderr)
        if "would be overwritten" in output:
            files = [line.strip() for line in output.splitlines() if line.startswith(("\t", "    "))][:8]
            return {"ok": False, "error": "Переключение затёрло бы незакоммиченные правки: " + ", ".join(files)
                    + ". Закоммитьте их или отложите"}
        return {"ok": False, "error": output or "Ветку не удалось переключить"}
    return {"ok": True, "branch": branch}


def commit(root: Path, message: str, paths: list[str]) -> dict[str, Any]:
    subject, _, body = (message or "").strip().partition("\n")
    if not subject.strip():
        return {"ok": False, "error": "Напишите сообщение коммита"}
    if not paths:
        return {"ok": False, "error": "Выберите файлы для коммита"}
    # новые файлы git commit --only не видит, пока их нет в индексе
    added = add(root, paths)
    if not added.get("ok"):
        return {"ok": False, "error": clean_output(added.get("error") or added.get("stderr") or "")}
    result = safe_commit(root, subject, paths=paths, body=body.strip()[:MAX_BODY] or None, own=set(added["staged"]))
    if not result.get("ok"):
        return {"ok": False, "error": clean_output(result.get("error") or result.get("stderr") or result.get("stdout") or "")
                or "Коммит не создан"}
    return {"ok": True, "commit": _git(root, "rev-parse", "--short", "HEAD").stdout.strip(), "files": result.get("committed") or []}


def _push_error(output: str) -> str:
    lowered = output.lower()
    if "rejected" in lowered or "non-fast-forward" in lowered or "fetch first" in lowered:
        return "На сервере есть коммиты, которых нет у вас: сначала заберите их, потом отправляйте. Ничего не отправлено"
    if any(mark in lowered for mark in ("permission denied", "could not read", "authentication failed", "publickey")):
        return "Нет доступа к серверу: проверьте вход или ключ в терминале. Ничего не отправлено"
    return output or "Отправка не удалась"


def push(root: Path, branch: str, remote: str) -> dict[str, Any]:
    current = _git(root, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
    if not current or current != branch:
        return {"ok": False, "error": f"Сейчас открыта ветка «{current or 'без имени'}», а не «{branch}»: ничего не отправлено"}
    if remote != _remote(root, current):
        return {"ok": False, "error": f"Отправка настроена не в «{remote}»: ничего не отправлено"}
    tracked = _upstream(root, current, remote)
    # только эта ветка в одноимённую: --force, теги и «все ветки» недоступны
    proc = _git(root, "push", "--porcelain", *(() if tracked else ("--set-upstream",)), remote,
                f"refs/heads/{current}:refs/heads/{current}", timeout=180)
    if proc.returncode != 0:
        return {"ok": False, "error": _push_error(clean_output(proc.stderr or proc.stdout))}
    return {"ok": True, "branch": current, "remote": remote, "target": current}

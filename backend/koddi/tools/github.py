"""GitHub для агента и панели через установленный `gh`: свои токены Докки не хранит и не видит.

Вход в GitHub - это `gh auth login` человека. Все вызовы с таймаутом и закрытым stdin (gh не должен ждать ввода),
вывод режется по размеру, токены в ответы не попадают.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

GH_TIMEOUT = 30
LOG_TIMEOUT = 90
MAX_LIST = 50
MAX_BODY = 4000
MAX_LOG = 6000
MAX_LOG_CAP = 20000
SUMMARY_TTL = 45.0
INSTALL_HINT = ("GitHub CLI (gh) не установлен. Установите: macOS - `brew install gh`, Windows - "
                "`winget install --id GitHub.cli`, затем выполните `gh auth login` в терминале")
AUTH_HINT = "gh не авторизован: выполните `gh auth login` в терминале и повторите"
NOT_GITHUB_HINT = "Репозиторий не на GitHub (или у него нет remote на github.com)"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
TOKEN_LIKE = re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
# приложение, запущенное из Finder/меню Пуск, не видит PATH оболочки: gh ищем и в обычных местах установки
EXTRA_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", r"C:\Program Files\GitHub CLI",
              r"C:\Program Files (x86)\GitHub CLI")


class GhError(Exception):
    """Понятная человеку ошибка (по-русски); kind: missing, auth, not_github, timeout, failed."""

    def __init__(self, message: str, kind: str = "failed") -> None:
        super().__init__(message)
        self.kind = kind


def gh_path() -> str | None:
    found = shutil.which("gh")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    for folder in (*EXTRA_DIRS, *((str(Path(local) / "Programs" / "GitHub CLI"),) if local else ())):
        found = shutil.which("gh", path=folder)
        if found:
            return found
    return None


def _clean(text: str, limit: int | None = None) -> str:
    value = TOKEN_LIKE.sub("[скрыто]", ANSI.sub("", text or "")).replace("\ufeff", "")
    return value if limit is None else value[-limit:]


def _gh_env() -> dict[str, str]:
    return {**os.environ, "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1", "CLICOLOR": "0", "GH_NO_UPDATE_NOTIFIER": "1",
            "GH_SPINNER_DISABLED": "1", "GH_PAGER": "cat", "PAGER": "cat", "GIT_TERMINAL_PROMPT": "0"}


def run_gh(root: Path | None, args: list[str], *, timeout: int = GH_TIMEOUT) -> subprocess.CompletedProcess:
    """Запуск gh; проблему установки и входа превращает в GhError, остальное решает вызывающий по коду возврата."""
    from koddi.tools.runner import HIDDEN_SUBPROCESS

    binary = gh_path()
    if not binary:
        raise GhError(INSTALL_HINT, "missing")
    try:
        proc = subprocess.run([binary, *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL, timeout=timeout, check=False, env=_gh_env(),
                              creationflags=HIDDEN_SUBPROCESS)
    except subprocess.TimeoutExpired as exc:
        raise GhError(f"gh не ответил за {timeout} с: проверьте сеть и повторите", "timeout") from exc
    except OSError as exc:
        raise GhError(INSTALL_HINT, "missing") from exc
    proc.stdout, proc.stderr = _clean(proc.stdout), _clean(proc.stderr)
    return proc


def _explain(proc: subprocess.CompletedProcess) -> GhError:
    text = (proc.stderr or proc.stdout or "").strip()
    lowered = text.lower()
    if any(mark in lowered for mark in ("gh auth login", "not logged in", "authentication required", "http 401",
                                        "bad credentials", "gh_token")):
        return GhError(AUTH_HINT, "auth")
    if any(mark in lowered for mark in ("none of the git remotes", "not a git repository", "no github remotes",
                                        "could not determine base repo", "unable to determine repo")):
        return GhError(NOT_GITHUB_HINT, "not_github")
    if "no pull requests found" in lowered or "could not find any pull requests" in lowered:
        return GhError("Для этой ветки нет pull request", "no_pr")
    if "http 404" in lowered or "could not resolve to" in lowered:
        return GhError("GitHub не нашёл запрошенное (проверьте номер и доступ к репозиторию)", "not_found")
    if "rate limit" in lowered:
        return GhError("GitHub ограничил число запросов: повторите позже", "rate")
    return GhError(text[-600:] or f"gh завершился с кодом {proc.returncode}")


def gh_json(root: Path | None, args: list[str], *, timeout: int = GH_TIMEOUT, tolerate: tuple[int, ...] = ()) -> Any:
    proc = run_gh(root, args, timeout=timeout)
    if proc.returncode != 0 and proc.returncode not in tolerate:
        raise _explain(proc)
    text = proc.stdout.strip()
    if not text:
        if proc.returncode != 0:
            raise _explain(proc)
        return None
    try:
        return json.loads(text)
    except ValueError as exc:
        if proc.returncode != 0:
            raise _explain(proc) from exc
        raise GhError("gh вернул ответ в неожиданном виде") from exc


def _cut(value: Any, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "\n... (обрезано)"


def _login(value: Any) -> str:
    return str(value.get("login") or "") if isinstance(value, dict) else ""


def _labels(items: Any) -> list[str]:
    return [str(item.get("name")) for item in items or [] if isinstance(item, dict) and item.get("name")][:20]


def _limit(value: Any, default: int = 20) -> int:
    try:
        return max(1, min(int(value), MAX_LIST))
    except (TypeError, ValueError):
        return default


def _number(value: Any, required: bool = False) -> str | None:
    if value in (None, ""):
        if required:
            raise GhError("Нужен номер (number)")
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise GhError("Номер должен быть числом") from exc
    if number < 1:
        raise GhError("Номер должен быть положительным")
    return str(number)


def _safe_value(value: Any, name: str, limit: int = 200) -> str:
    text = str(value or "").strip()
    if len(text) > limit or text.startswith("-") or any(ord(ch) < 32 for ch in text):
        raise GhError(f"Некорректное значение {name}")
    return text


# ----- проверки CI

FAILED_STEP = {"failure", "timed_out", "cancelled"}
PASSED = {"SUCCESS", "NEUTRAL", "SKIPPED"}
FAILED = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR", "STALE"}


def summarize_checks(rollup: Any) -> dict[str, Any]:
    """Сводка statusCheckRollup: CheckRun (status/conclusion) и старые статусы (state) приводятся к трём группам."""
    passed = failed = pending = 0
    failed_names: list[str] = []
    for item in rollup or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("context") or "")
        if "state" in item and "status" not in item:
            result = str(item.get("state") or "").upper()
            group = "pass" if result in PASSED else "fail" if result in FAILED else "pending"
        elif str(item.get("status") or "").upper() != "COMPLETED":
            group = "pending"
        else:
            result = str(item.get("conclusion") or "").upper()
            group = "pass" if result in PASSED else "fail" if result in FAILED else "pending"
        if group == "pass":
            passed += 1
        elif group == "fail":
            failed += 1
            if len(failed_names) < 10:
                failed_names.append(name)
        else:
            pending += 1
    total = passed + failed + pending
    state = "none" if not total else "failure" if failed else "pending" if pending else "success"
    return {"state": state, "total": total, "passed": passed, "failed": failed, "pending": pending, "failed_names": failed_names}


def _pr_row(item: dict[str, Any]) -> dict[str, Any]:
    return {"number": item.get("number"), "title": _cut(item.get("title"), 200), "state": item.get("state"),
            "draft": bool(item.get("isDraft")), "branch": item.get("headRefName"), "base": item.get("baseRefName"),
            "author": _login(item.get("author")), "url": item.get("url"), "updated": item.get("updatedAt")}


# ----- инструменты агента: каждый возвращает {"ok": bool, ...}; ошибка - {"ok": False, "error": "..."}

def _guard(call) -> dict[str, Any]:
    try:
        return call()
    except GhError as exc:
        return {"ok": False, "error": str(exc), "kind": exc.kind}


def status(root: Path) -> dict[str, Any]:
    binary = gh_path()
    if not binary:
        return {"ok": True, "installed": False, "authenticated": False, "hint": INSTALL_HINT}

    def work() -> dict[str, Any]:
        result: dict[str, Any] = {"ok": True, "installed": True, "authenticated": False}
        try:
            proc = run_gh(root, ["auth", "status"])
        except GhError as exc:
            return {**result, "installed": exc.kind != "missing", "hint": str(exc)}
        text = f"{proc.stdout}\n{proc.stderr}"
        if proc.returncode != 0:
            return {**result, "hint": AUTH_HINT}
        result["authenticated"] = True
        if match := re.search(r"account (\S+)", text):
            result["account"] = match[1]
        try:
            repo = gh_json(root, ["repo", "view", "--json", "nameWithOwner,url,isPrivate,defaultBranchRef"])
        except GhError as exc:
            return {**result, "repo": None, "hint": str(exc)}
        repo = repo or {}
        return {**result, "repo": repo.get("nameWithOwner"), "url": repo.get("url"), "private": repo.get("isPrivate"),
                "default_branch": (repo.get("defaultBranchRef") or {}).get("name")}

    return _guard(work)


def pr_list(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    def work() -> dict[str, Any]:
        command = ["pr", "list", "--json", "number,title,state,isDraft,headRefName,baseRefName,author,url,updatedAt",
                   "--limit", str(_limit(args.get("limit")))]
        state = str(args.get("state") or "open")
        if state not in {"open", "closed", "merged", "all"}:
            raise GhError("state: open, closed, merged или all")
        command += ["--state", state]
        for key, flag in (("author", "--author"), ("base", "--base"), ("head", "--head")):
            if args.get(key):
                command.append(f"{flag}={_safe_value(args[key], key)}")
        rows = gh_json(root, command) or []
        return {"ok": True, "pull_requests": [_pr_row(item) for item in rows]}

    return _guard(work)


def pr_view(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    def work() -> dict[str, Any]:
        command = ["pr", "view"]
        if number := _number(args.get("number")):
            command.append(number)
        command += ["--json", "number,title,state,isDraft,body,headRefName,baseRefName,author,url,mergeable,"
                    "reviewDecision,additions,deletions,changedFiles,labels,statusCheckRollup,updatedAt"]
        item = gh_json(root, command)
        if not isinstance(item, dict):
            raise GhError("gh вернул пустой ответ")
        return {"ok": True, **_pr_row(item), "body": _cut(item.get("body"), MAX_BODY), "mergeable": item.get("mergeable"),
                "review": item.get("reviewDecision"), "additions": item.get("additions"), "deletions": item.get("deletions"),
                "files": item.get("changedFiles"), "labels": _labels(item.get("labels")),
                "checks": summarize_checks(item.get("statusCheckRollup"))}

    return _guard(work)


def pr_create(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    def work() -> dict[str, Any]:
        title = " ".join(str(args.get("title") or "").split())
        if not title:
            raise GhError("Нужен заголовок (title)")
        command = ["pr", "create", f"--title={title[:250]}", f"--body={_cut(args.get('body'), 20000)}"]
        if args.get("base"):
            command.append(f"--base={_safe_value(args['base'], 'base')}")
        if args.get("draft"):
            command.append("--draft")
        proc = run_gh(root, command, timeout=60)
        if proc.returncode != 0:
            error = _explain(proc)
            text = (proc.stderr or "").lower()
            if "push" in text and ("current branch" in text or "head" in text):
                raise GhError("Ветка ещё не отправлена на GitHub: сначала git.push", "failed")
            if "already exists" in text:
                raise GhError("Pull request для этой ветки уже есть: " + (proc.stderr.strip().splitlines() or [""])[-1][-300:])
            raise error
        urls = re.findall(r"https://\S+/pull/\d+", proc.stdout + "\n" + proc.stderr)
        return {"ok": True, "url": urls[-1] if urls else "", "stdout": proc.stdout.strip()[-500:]}

    return _guard(work)


def pr_checks(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    def work() -> dict[str, Any]:
        command = ["pr", "checks"]
        if number := _number(args.get("number")):
            command.append(number)
        command += ["--json", "name,state,bucket,link,workflow,description"]
        # коды 1 (есть упавшие) и 8 (идут) для gh - обычный результат, список всё равно выводится
        try:
            rows = gh_json(root, command, tolerate=(1, 8)) or []
        except GhError as exc:
            if exc.kind == "failed" and "no checks reported" in str(exc).lower():
                rows = []
            else:
                raise
        counts = {"pass": 0, "fail": 0, "pending": 0, "skipping": 0, "cancel": 0}
        checks = []
        for row in rows[:100]:
            bucket = str(row.get("bucket") or "pending")
            counts[bucket] = counts.get(bucket, 0) + 1
            checks.append({"name": _cut(row.get("name"), 120), "workflow": row.get("workflow"), "bucket": bucket,
                           "state": row.get("state"), "link": row.get("link")})
        failed = counts.get("fail", 0) + counts.get("cancel", 0)
        state = "none" if not rows else "failure" if failed else "pending" if counts.get("pending") else "success"
        return {"ok": True, "state": state, "counts": counts, "checks": checks}

    return _guard(work)


def issue_list(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    def work() -> dict[str, Any]:
        state = str(args.get("state") or "open")
        if state not in {"open", "closed", "all"}:
            raise GhError("state: open, closed или all")
        command = ["issue", "list", "--json", "number,title,state,labels,author,url,updatedAt", "--state", state,
                   "--limit", str(_limit(args.get("limit")))]
        for key, flag in (("label", "--label"), ("assignee", "--assignee"), ("author", "--author")):
            if args.get(key):
                command.append(f"{flag}={_safe_value(args[key], key)}")
        rows = gh_json(root, command) or []
        return {"ok": True, "issues": [{"number": r.get("number"), "title": _cut(r.get("title"), 200), "state": r.get("state"),
                                        "labels": _labels(r.get("labels")), "author": _login(r.get("author")),
                                        "url": r.get("url"), "updated": r.get("updatedAt")} for r in rows]}

    return _guard(work)


def issue_view(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    def work() -> dict[str, Any]:
        number = _number(args.get("number"), required=True)
        item = gh_json(root, ["issue", "view", str(number), "--json", "number,title,state,body,author,labels,url,comments"])
        if not isinstance(item, dict):
            raise GhError("gh вернул пустой ответ")
        comments = [{"author": _login(c.get("author")), "body": _cut(c.get("body"), 1000), "created": c.get("createdAt")}
                    for c in (item.get("comments") or [])[-10:] if isinstance(c, dict)]
        return {"ok": True, "number": item.get("number"), "title": _cut(item.get("title"), 200), "state": item.get("state"),
                "author": _login(item.get("author")), "labels": _labels(item.get("labels")), "url": item.get("url"),
                "body": _cut(item.get("body"), MAX_BODY), "comments": comments}

    return _guard(work)


def run_list(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    def work() -> dict[str, Any]:
        command = ["run", "list", "--json", "databaseId,displayTitle,workflowName,status,conclusion,headBranch,event,url,createdAt",
                   "--limit", str(_limit(args.get("limit"), 10))]
        if args.get("branch"):
            command.append(f"--branch={_safe_value(args['branch'], 'branch')}")
        if args.get("status"):
            command.append(f"--status={_safe_value(args['status'], 'status', 30)}")
        rows = gh_json(root, command) or []
        return {"ok": True, "runs": [_run_row(item) for item in rows]}

    return _guard(work)


def _run_row(item: dict[str, Any]) -> dict[str, Any]:
    return {"id": item.get("databaseId"), "title": _cut(item.get("displayTitle"), 200), "workflow": item.get("workflowName"),
            "status": item.get("status"), "conclusion": item.get("conclusion"), "branch": item.get("headBranch"),
            "event": item.get("event"), "url": item.get("url"), "created": item.get("createdAt")}


def run_view(root: Path, args: dict[str, Any], branch: str | None = None) -> dict[str, Any]:
    def work() -> dict[str, Any]:
        run_id = _number(args.get("run_id"))
        if run_id is None:
            command = ["run", "list", "--json", "databaseId", "--limit", "1", *((f"--branch={branch}",) if branch else ())]
            latest = gh_json(root, command) or []
            if not latest:
                raise GhError("Запусков Actions пока нет")
            run_id = str(latest[0]["databaseId"])
        item = gh_json(root, ["run", "view", run_id, "--json",
                              "databaseId,displayTitle,workflowName,status,conclusion,headBranch,event,url,createdAt,jobs"])
        if not isinstance(item, dict):
            raise GhError("gh вернул пустой ответ")
        jobs = []
        for job in (item.get("jobs") or [])[:30]:
            steps = [{"name": _cut(s.get("name"), 120), "conclusion": s.get("conclusion")} for s in job.get("steps") or []
                     if s.get("conclusion") in FAILED_STEP]
            jobs.append({"name": _cut(job.get("name"), 120), "status": job.get("status"), "conclusion": job.get("conclusion"),
                         "failed_steps": steps[:10]})
        result = {"ok": True, **_run_row(item), "jobs": jobs}
        failing = item.get("conclusion") in {"failure", "timed_out", "startup_failure"} or any(
            job["conclusion"] == "failure" for job in jobs)
        if failing and args.get("log", True):
            cap = max(500, min(int(args.get("max_chars") or MAX_LOG), MAX_LOG_CAP))
            proc = run_gh(root, ["run", "view", run_id, "--log-failed"], timeout=LOG_TIMEOUT)
            # хвост лога: ошибка почти всегда в конце упавшего шага
            result["failed_log"] = proc.stdout[-cap:] if proc.returncode == 0 else ""
            result["log_truncated"] = len(proc.stdout) > cap
        return result

    return _guard(work)


# ----- карточка в правой панели

_cache: dict[str, tuple[float, str, dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def pr_summary(root: Path, branch: str | None, *, refresh: bool = False) -> dict[str, Any]:
    """Короткая сводка для панели: PR текущей ветки и его проверки. Если gh нет или репозиторий не на GitHub -
    {"available": False}: панель молчит. Ответ кэшируется на SUMMARY_TTL секунд, «Обновить» кэш пропускает."""
    key = str(root)
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and not refresh and cached[1] == (branch or "") and now - cached[0] < SUMMARY_TTL:
            return cached[2]
    result = _summary(root, branch)
    with _cache_lock:
        if len(_cache) > 50:
            _cache.clear()
        _cache[key] = (time.monotonic(), branch or "", result)
    return result


def _summary(root: Path, branch: str | None) -> dict[str, Any]:
    if not branch or branch.startswith("-") or not gh_path():
        return {"available": False}
    try:
        item = gh_json(root, ["pr", "view", branch, "--json", "number,title,state,isDraft,url,statusCheckRollup"])
    except GhError as exc:
        if exc.kind == "no_pr":
            return {"available": True, "pr": None}
        return {"available": False}
    if not isinstance(item, dict):
        return {"available": False}
    return {"available": True, "pr": {"number": item.get("number"), "title": _cut(item.get("title"), 200),
                                      "state": item.get("state"), "draft": bool(item.get("isDraft")), "url": item.get("url"),
                                      "checks": summarize_checks(item.get("statusCheckRollup"))}}


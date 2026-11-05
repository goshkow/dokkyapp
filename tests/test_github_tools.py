"""Инструменты агента git push/pull/fetch/merge/stash/remote и GitHub через gh: разбор вывода и ошибки."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from koddi.projects import gitremote
from koddi.tools import github
from koddi.tools.schemas import tool_schemas_for_mode


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


def commit_file(root: Path, name: str, text: str, message: str) -> None:
    (root / name).write_text(text, encoding="utf-8")
    git(root, "add", name)
    git(root, "commit", "-m", message)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Тест")
    git(root, "config", "user.email", "test@example.com")
    commit_file(root, "a.txt", "один\n", "первый")
    return root


@pytest.fixture
def linked(repo, tmp_path):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(bare))
    return repo, bare


@pytest.fixture
def other(linked, tmp_path):
    """Второй клон: другой человек, который пушит в тот же remote."""
    repo, bare = linked
    git(repo, "push", "-u", "origin", "main")
    clone = tmp_path / "other"
    subprocess.run(["git", "clone", str(bare), str(clone)], check=True, capture_output=True)
    git(clone, "config", "user.name", "Другой")
    git(clone, "config", "user.email", "other@example.com")
    return clone


# ---------- git

def test_first_push_sets_upstream_and_second_does_not(linked):
    repo, _ = linked
    first = gitremote.push(repo)
    assert first["ok"] and first["upstream_set"] and first["branch"] == "main"
    assert git(repo, "rev-parse", "--abbrev-ref", "main@{upstream}") == "origin/main"
    commit_file(repo, "b.txt", "два\n", "второй")
    second = gitremote.push(repo)
    assert second["ok"] and not second["upstream_set"]


def test_push_rejects_unknown_remote_url_and_odd_branch(linked):
    repo, bare = linked
    assert not gitremote.push(repo, remote=str(bare))["ok"]
    assert "не настроен" in gitremote.push(repo, remote="nope")["error"]
    assert not gitremote.push(repo, branch="--force")["ok"]
    assert "нет" in gitremote.push(repo, branch="ghost")["error"]


def test_push_without_remote_is_clear_error(repo):
    assert "нет удалённого" in gitremote.push(repo)["error"]


def test_push_refused_when_remote_is_ahead(other, linked):
    repo, _ = linked
    commit_file(other, "x.txt", "чужое\n", "чужой")
    git(other, "push")
    commit_file(repo, "y.txt", "своё\n", "свой")
    result = gitremote.push(repo)
    assert not result["ok"] and "сначала заберите" in result["error"]


def test_pull_fast_forward_and_diverged(other, linked):
    repo, _ = linked
    commit_file(other, "x.txt", "чужое\n", "чужой")
    git(other, "push")
    assert gitremote.fetch(repo)["ok"]
    pulled = gitremote.pull(repo)
    assert pulled["ok"] and (repo / "x.txt").exists()
    commit_file(other, "x2.txt", "ещё\n", "чужой 2")
    git(other, "push")
    commit_file(repo, "y.txt", "своё\n", "свой")
    diverged = gitremote.pull(repo)
    assert not diverged["ok"] and "разошлись" in diverged["error"]
    assert not (repo / "x2.txt").exists()


def test_merge_conflict_lists_files_without_commit_and_abort(repo):
    git(repo, "checkout", "-b", "feature")
    commit_file(repo, "a.txt", "ветка\n", "в ветке")
    git(repo, "checkout", "main")
    commit_file(repo, "a.txt", "main\n", "в main")
    head = git(repo, "rev-parse", "HEAD")
    result = gitremote.merge(repo, "feature")
    assert not result["ok"] and result["conflicts"] == ["a.txt"]
    assert git(repo, "rev-parse", "HEAD") == head
    aborted = gitremote.merge(repo, abort=True)
    assert aborted["ok"] and (repo / "a.txt").read_text(encoding="utf-8") == "main\n"
    assert not gitremote.merge(repo, "--abort")["ok"]


def test_merge_clean_and_ff_only_refusal(repo):
    git(repo, "checkout", "-b", "feature")
    commit_file(repo, "f.txt", "f\n", "фича")
    git(repo, "checkout", "main")
    commit_file(repo, "m.txt", "m\n", "main")
    assert not gitremote.merge(repo, "feature", ff_only=True)["ok"]
    merged = gitremote.merge(repo, "feature")
    assert merged["ok"] and merged["conflicts"] == [] and (repo / "f.txt").exists()


def test_stash_push_list_pop(repo):
    (repo / "a.txt").write_text("правка\n", encoding="utf-8")
    saved = gitremote.stash(repo, "push", "моя правка")
    assert saved["ok"] and saved["saved"]
    assert (repo / "a.txt").read_text(encoding="utf-8") == "один\n"
    items = gitremote.stash(repo, "list")["items"]
    assert len(items) == 1 and items[0]["ref"] == "stash@{0}" and "моя правка" in items[0]["message"]
    assert gitremote.stash(repo, "pop")["ok"]
    assert (repo / "a.txt").read_text(encoding="utf-8") == "правка\n"
    assert gitremote.stash(repo, "push")["saved"]
    assert gitremote.stash(repo, "pop")["ok"]
    assert gitremote.stash(repo, "pop")["ok"] is False
    assert not gitremote.stash(repo, "drop")["ok"]


def test_stash_include_untracked(repo):
    (repo / "new.txt").write_text("новый\n", encoding="utf-8")
    assert not gitremote.stash(repo, "push")["saved"]
    assert gitremote.stash(repo, "push", include_untracked=True)["saved"]
    assert not (repo / "new.txt").exists()


def test_remote_list_hides_credentials(repo):
    git(repo, "remote", "add", "origin", "https://user:secret@example.com/o/r.git")
    result = gitremote.remote_list(repo)
    assert result["ok"] and result["remotes"][0]["name"] == "origin"
    assert "secret" not in json.dumps(result) and result["remotes"][0]["push"] == "https://example.com/o/r.git"


# ---------- gh

class FakeGh:
    """Подставной gh: ответы по началу списка аргументов; запоминает вызовы."""

    def __init__(self, monkeypatch, replies: dict[tuple, tuple[int, str, str]] | None = None):
        self.calls: list[list[str]] = []
        self.replies = replies or {}
        monkeypatch.setattr(github, "gh_path", lambda: "gh")
        monkeypatch.setattr(github.subprocess, "run", self.run)

    def run(self, command, **kwargs):
        args = list(command[1:])
        self.calls.append(args)
        assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["timeout"] > 0 and kwargs["encoding"] == "utf-8"
        for prefix, (code, out, err) in self.replies.items():
            if tuple(args[: len(prefix)]) == prefix:
                return subprocess.CompletedProcess(command, code, out, err)
        return subprocess.CompletedProcess(command, 1, "", "unknown command")


ROLLUP = [
    {"name": "lint", "status": "COMPLETED", "conclusion": "SUCCESS"},
    {"name": "tests", "status": "COMPLETED", "conclusion": "FAILURE"},
    {"name": "build", "status": "IN_PROGRESS", "conclusion": ""},
    {"context": "ci/legacy", "state": "SUCCESS"},
    {"name": "docs", "status": "COMPLETED", "conclusion": "SKIPPED"},
]


def test_summarize_checks_groups():
    summary = github.summarize_checks(ROLLUP)
    assert (summary["passed"], summary["failed"], summary["pending"], summary["state"]) == (3, 1, 1, "failure")
    assert summary["failed_names"] == ["tests"]
    assert github.summarize_checks([])["state"] == "none"
    assert github.summarize_checks([{"name": "a", "status": "QUEUED"}])["state"] == "pending"


def test_missing_gh_gives_install_hint(monkeypatch, tmp_path):
    monkeypatch.setattr(github, "gh_path", lambda: None)
    result = github.pr_list(tmp_path, {})
    assert not result["ok"] and "brew install gh" in result["error"] and "winget" in result["error"]
    assert "gh auth login" in result["error"]
    info = github.status(tmp_path)
    assert info["ok"] and not info["installed"] and not info["authenticated"]
    assert github.pr_summary(tmp_path, "main", refresh=True) == {"available": False}


def test_not_authorized_error(monkeypatch, tmp_path):
    FakeGh(monkeypatch, {("pr", "list"): (1, "", "To get started with GitHub CLI, please run:  gh auth login")})
    result = github.pr_list(tmp_path, {})
    assert not result["ok"] and result["kind"] == "auth" and "gh auth login" in result["error"]


def test_timeout_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(github, "gh_path", lambda: "gh")

    def slow(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(github.subprocess, "run", slow)
    result = github.issue_list(tmp_path, {})
    assert not result["ok"] and result["kind"] == "timeout" and "30 с" in result["error"]


def test_not_a_github_repo(monkeypatch, tmp_path):
    FakeGh(monkeypatch, {("pr", "list"): (1, "", "none of the git remotes configured for this repository point to a known GitHub host")})
    assert github.pr_list(tmp_path, {})["kind"] == "not_github"


def test_status_reads_account_and_repo(monkeypatch, tmp_path):
    FakeGh(monkeypatch, {
        ("auth", "status"): (0, "github.com\n  Logged in to github.com account octo (keyring)\n", ""),
        ("repo", "view"): (0, json.dumps({"nameWithOwner": "o/r", "url": "https://github.com/o/r", "isPrivate": True,
                                          "defaultBranchRef": {"name": "main"}}), ""),
    })
    info = github.status(tmp_path)
    assert info["authenticated"] and info["account"] == "octo" and info["repo"] == "o/r" and info["default_branch"] == "main"


def test_status_not_authenticated(monkeypatch, tmp_path):
    FakeGh(monkeypatch, {("auth", "status"): (1, "", "You are not logged into any GitHub hosts. To log in, run: gh auth login")})
    info = github.status(tmp_path)
    assert info["installed"] and not info["authenticated"] and "gh auth login" in info["hint"]


def test_pr_view_parses_and_summarizes(monkeypatch, tmp_path):
    payload = {"number": 7, "title": "Фича", "state": "OPEN", "isDraft": False, "headRefName": "feat", "baseRefName": "main",
               "author": {"login": "octo"}, "url": "https://github.com/o/r/pull/7", "body": "т" * 9000, "mergeable": "MERGEABLE",
               "reviewDecision": "APPROVED", "additions": 3, "deletions": 1, "changedFiles": 2,
               "labels": [{"name": "bug"}], "statusCheckRollup": ROLLUP}
    fake = FakeGh(monkeypatch, {("pr", "view"): (0, json.dumps(payload), "")})
    result = github.pr_view(tmp_path, {"number": 7})
    assert result["ok"] and result["number"] == 7 and result["author"] == "octo" and result["labels"] == ["bug"]
    assert result["checks"]["failed"] == 1 and len(result["body"]) < 4100
    assert fake.calls[0][:3] == ["pr", "view", "7"]
    assert not github.pr_view(tmp_path, {"number": "x"})["ok"]
    assert not github.pr_view(tmp_path, {"number": -3})["ok"]


def test_pr_view_without_pr_for_branch(monkeypatch, tmp_path):
    FakeGh(monkeypatch, {("pr", "view"): (1, "", 'no pull requests found for branch "feat"')})
    result = github.pr_view(tmp_path, {})
    assert not result["ok"] and result["kind"] == "no_pr"


def test_pr_checks_accepts_pending_exit_code_and_empty(monkeypatch, tmp_path):
    rows = [{"name": "a", "bucket": "pass", "state": "SUCCESS", "link": "u", "workflow": "ci"},
            {"name": "b", "bucket": "pending", "state": "IN_PROGRESS", "link": "u", "workflow": "ci"}]
    FakeGh(monkeypatch, {("pr", "checks"): (8, json.dumps(rows), "")})
    result = github.pr_checks(tmp_path, {})
    assert result["ok"] and result["state"] == "pending" and result["counts"]["pass"] == 1
    FakeGh(monkeypatch, {("pr", "checks"): (1, "", "no checks reported on the 'feat' branch")})
    assert github.pr_checks(tmp_path, {}) == {"ok": True, "state": "none", "counts": {"pass": 0, "fail": 0, "pending": 0, "skipping": 0, "cancel": 0}, "checks": []}


def test_pr_create_returns_url_and_validates(monkeypatch, tmp_path):
    fake = FakeGh(monkeypatch, {("pr", "create"): (0, "https://github.com/o/r/pull/9\n", "")})
    assert not github.pr_create(tmp_path, {"title": "  "})["ok"]
    result = github.pr_create(tmp_path, {"title": "Правка", "body": "Текст", "base": "main", "draft": True})
    assert result["ok"] and result["url"].endswith("/pull/9")
    assert "--draft" in fake.calls[-1] and "--base=main" in fake.calls[-1] and "--title=Правка" in fake.calls[-1]
    assert not github.pr_create(tmp_path, {"title": "x", "base": "--web"})["ok"]


def test_pr_create_unpushed_branch_hint(monkeypatch, tmp_path):
    FakeGh(monkeypatch, {("pr", "create"): (1, "", "aborted: you must first push the current branch to a remote, or use the --head flag")})
    assert "git.push" in github.pr_create(tmp_path, {"title": "x"})["error"]


def test_issue_view_requires_number_and_trims_comments(monkeypatch, tmp_path):
    comments = [{"author": {"login": f"u{i}"}, "body": "к" * 3000, "createdAt": "t"} for i in range(15)]
    FakeGh(monkeypatch, {("issue", "view"): (0, json.dumps({"number": 3, "title": "Баг", "state": "OPEN", "body": "b",
                                                              "author": {"login": "a"}, "labels": [], "url": "u", "comments": comments}), "")})
    assert not github.issue_view(tmp_path, {})["ok"]
    result = github.issue_view(tmp_path, {"number": 3})
    assert len(result["comments"]) == 10 and len(result["comments"][0]["body"]) < 1100


def test_run_view_tails_failed_log_with_limit(monkeypatch, tmp_path):
    run = {"databaseId": 55, "displayTitle": "CI", "workflowName": "ci", "status": "completed", "conclusion": "failure",
           "headBranch": "feat", "event": "push", "url": "u", "createdAt": "t",
           "jobs": [{"name": "test", "status": "completed", "conclusion": "failure",
                     "steps": [{"name": "ok", "conclusion": "success"}, {"name": "pytest", "conclusion": "failure"}]}]}
    log = "\x1b[31mначало\x1b[0m\n" + "строка\n" * 5000 + "AssertionError: итог\n"
    fake = FakeGh(monkeypatch, {("run", "list"): (0, json.dumps([{"databaseId": 55}]), ""),
                                ("run", "view", "55", "--json"): (0, json.dumps(run), ""),
                                ("run", "view", "55", "--log-failed"): (0, log, "")})
    result = github.run_view(tmp_path, {"max_chars": 800}, branch="feat")
    assert result["ok"] and result["id"] == 55 and result["jobs"][0]["failed_steps"] == [{"name": "pytest", "conclusion": "failure"}]
    assert len(result["failed_log"]) == 800 and result["failed_log"].endswith("AssertionError: итог\n")
    assert "\x1b" not in result["failed_log"] and result["log_truncated"]
    assert "--branch=feat" in fake.calls[0]
    quiet = github.run_view(tmp_path, {"run_id": 55, "log": False})
    assert "failed_log" not in quiet


def test_tokens_are_not_returned(monkeypatch, tmp_path):
    token = "ghp_" + "a" * 36
    FakeGh(monkeypatch, {("pr", "list"): (1, "", f"boom token {token}")})
    assert token not in github.pr_list(tmp_path, {})["error"]


def test_summary_cached_and_refresh(monkeypatch, tmp_path):
    payload = {"number": 4, "title": "PR", "state": "OPEN", "isDraft": True, "url": "https://github.com/o/r/pull/4",
               "statusCheckRollup": ROLLUP}
    fake = FakeGh(monkeypatch, {("pr", "view"): (0, json.dumps(payload), "")})
    github._cache.clear()
    first = github.pr_summary(tmp_path, "feat")
    assert first["available"] and first["pr"]["number"] == 4 and first["pr"]["draft"] and first["pr"]["checks"]["failed"] == 1
    github.pr_summary(tmp_path, "feat")
    assert len(fake.calls) == 1
    github.pr_summary(tmp_path, "other")
    github.pr_summary(tmp_path, "other", refresh=True)
    assert len(fake.calls) == 3


def test_summary_without_pr_and_hidden_when_unavailable(monkeypatch, tmp_path):
    github._cache.clear()
    FakeGh(monkeypatch, {("pr", "view"): (1, "", 'no pull requests found for branch "feat"')})
    assert github.pr_summary(tmp_path, "feat", refresh=True) == {"available": True, "pr": None}
    FakeGh(monkeypatch, {("pr", "view"): (1, "", "none of the git remotes configured")})
    assert github.pr_summary(tmp_path, "feat", refresh=True) == {"available": False}
    FakeGh(monkeypatch, {("pr", "view"): (1, "", "gh auth login")})
    assert github.pr_summary(tmp_path, "feat", refresh=True) == {"available": False}
    assert github.pr_summary(tmp_path, None, refresh=True) == {"available": False}


# ---------- схемы

NEW_TOOLS = {"git.push", "git.pull", "git.fetch", "git.stash", "git.merge", "git.remote", "github.status", "github.pr_list",
             "github.pr_view", "github.pr_create", "github.pr_checks", "github.issue_list", "github.issue_view",
             "github.run_list", "github.run_view"}


@pytest.mark.parametrize("mode", ["development", "debug"])
def test_new_tools_in_development_and_debug(mode):
    names = {tool["function"]["name"] for tool in tool_schemas_for_mode(mode)}
    assert NEW_TOOLS <= names


def test_new_tools_absent_from_read_only_modes_and_required_args():
    for mode in ("plan", "review", "chat"):
        names = {tool["function"]["name"] for tool in tool_schemas_for_mode(mode)}
        assert not NEW_TOOLS & names
    schemas = {tool["function"]["name"]: tool["function"]["parameters"] for tool in tool_schemas_for_mode("development")}
    assert schemas["github.pr_create"]["required"] == ["title"]
    assert schemas["github.issue_view"]["required"] == ["number"]
    assert schemas["git.push"]["required"] == [] and schemas["git.stash"]["properties"]["action"]["enum"] == ["push", "pop", "list"]
    assert all(schema["additionalProperties"] is False for schema in schemas.values() if "additionalProperties" in schema)

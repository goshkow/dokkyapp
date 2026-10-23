from __future__ import annotations

import stat
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import koddi.tools.runner as runner_module
from koddi.policy.paths import WorkspacePaths
from koddi.tools.runner import ToolError, ToolRunner


@pytest.fixture
def workspace(tmp_path):
    paths = [
        "src/site.py", "src/other.py", "src/templates/z.html", "src/templates/a.html",
        "src/templates/nested/page.html", "src/templates/app.js", "src/static/page.html",
        "other/site.py", "other/page.html", "site.py", "README.md",
    ]
    for relative in paths:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("needle\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("pattern", [
    "src/site.py", "src/templates/*.html", "src/templates/**/*.html",
    "src/{templates,static}/*.html", "src/templates/*.{html,js}",
    "src/{site,other}.py", "{src,other}/site.py", "src/templat[ex]s/*.html",
    "src/*/page.html", "src/**", "src/missing/*.html", "src/missing.py",
    "src/templates", "site.py", "*.html", "*.{py,html}", "**/site.py", "**/*.html",
    "*", "./src/*.py", "src/../*.py", "/src/*.py", "src/", "src\\site.py",
    "src/SITE.py", "SRC/site.py", "SRC/templates/*.html",
])
def test_scoped_search_preserves_existing_matches(workspace, pattern):
    runner = ToolRunner(workspace)
    expected = sorted(
        path.relative_to(workspace).as_posix()
        for path in WorkspacePaths(workspace).files()
        if runner._matches_glob(path.relative_to(workspace).as_posix(), pattern)
    )
    files = runner.search_files({"query": pattern})
    text = runner.search_text({"query": "needle", "glob": pattern})
    assert files == {"ok": True, "files": expected, "truncated": False}
    assert [item["path"] for item in text["matches"]] == expected


@pytest.mark.parametrize("pattern,expected", [
    ("src/site.py", []),
    ("src/missing.py", []),
    ("src/templates/*.html", ["src/templates"]),
    ("src/templates/*.{html,js}", ["src/templates"]),
    ("src/{templates,static}/*.html", ["src"]),
    ("site.py", [None]),
    ("*.html", [None]),
    ("**/site.py", [None]),
])
def test_only_static_scope_is_enumerated(workspace, monkeypatch, pattern, expected):
    roots = []
    original = WorkspacePaths.files

    def files(self, root=None):
        roots.append(root.relative_to(workspace).as_posix() if root else None)
        yield from original(self, root)

    monkeypatch.setattr(WorkspacePaths, "files", files)
    ToolRunner(workspace).search_files({"query": pattern})
    assert roots == expected


@pytest.mark.parametrize("directory", [".git", ".venv", "node_modules", "__pycache__"])
@pytest.mark.parametrize("suffix", ["hidden.txt", "*.txt"])
def test_narrow_scope_cannot_enter_noise_directories(tmp_path, directory, suffix):
    hidden = tmp_path / "src" / directory / "hidden.txt"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("needle", encoding="utf-8")
    runner = ToolRunner(tmp_path)
    pattern = f"src/{directory}/{suffix}"
    assert runner.search_files({"query": pattern})["files"] == []
    assert runner.search_text({"query": "needle", "glob": pattern})["matches"] == []


@pytest.mark.parametrize("ignore_name", [".gitignore", ".ignore", ".koddiignore"])
def test_narrow_scope_obeys_ancestor_and_nested_ignore_rules(tmp_path, ignore_name):
    for relative in ("src/private/hidden.txt", "src/public/hidden.txt", "src/public/open.txt"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("needle", encoding="utf-8")
    (tmp_path / ignore_name).write_text("src/private/\n", encoding="utf-8")
    (tmp_path / "src" / ignore_name).write_text("hidden.txt\n", encoding="utf-8")
    runner = ToolRunner(tmp_path)
    for pattern, expected in (
        ("src/private/hidden.txt", []), ("src/private/*.txt", []),
        ("src/public/hidden.txt", []), ("src/public/*.txt", ["src/public/open.txt"]),
        ("src/public/open.txt", ["src/public/open.txt"]),
    ):
        assert runner.search_files({"query": pattern})["files"] == expected
        assert [item["path"] for item in runner.search_text({"query": "needle", "glob": pattern})["matches"]] == expected


@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("suffix", ["hidden.txt", "*.txt"])
def test_narrow_scope_blocks_reparse_points(tmp_path, monkeypatch, directory, suffix):
    linked = tmp_path / "src" / "linked"
    linked.parent.mkdir()
    if directory:
        linked.mkdir()
        (linked / "hidden.txt").write_text("needle", encoding="utf-8")
        pattern = f"src/linked/{suffix}"
    else:
        linked.write_text("needle", encoding="utf-8")
        pattern = "src/linked"
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == linked:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return info

    monkeypatch.setattr(Path, "lstat", lstat)
    runner = ToolRunner(tmp_path)
    assert runner.search_files({"query": pattern})["files"] == []
    assert runner.search_text({"query": "needle", "glob": pattern})["matches"] == []


@pytest.mark.parametrize("directory", [False, True])
def test_narrow_scope_does_not_follow_symlinks_outside_workspace(tmp_path, directory):
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "hidden.txt"
    secret.write_text("needle", encoding="utf-8")
    link = workspace / "src" / "linked"
    try:
        link.symlink_to(outside if directory else secret, target_is_directory=directory)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    runner = ToolRunner(workspace)
    for pattern in (["src/linked/hidden.txt", "src/linked/*.txt"] if directory else ["src/linked"]):
        assert runner.search_files({"query": pattern})["files"] == []
        assert runner.search_text({"query": "needle", "glob": pattern})["matches"] == []


@pytest.mark.parametrize("pattern", ["src/site.py", "src/templates/*.html"])
def test_scoped_search_keeps_cancellation_and_deadline(workspace, pattern):
    runner = ToolRunner(workspace)
    with pytest.raises(ToolError, match="слишком много времени"):
        runner._search_paths(time.monotonic() - 1, pattern)
    runner.cancel_event.set()
    with pytest.raises(ToolError, match="search cancelled"):
        runner._search_paths(float("inf"), pattern)


def test_scoped_search_keeps_candidate_limit_before_sort(workspace, monkeypatch):
    consumed = 0

    class UnsortablePath:
        def relative_to(self, root):
            pytest.fail("Превышенный список не должен сортироваться")

    def files(self, root=None):
        nonlocal consumed
        assert root == workspace / "src" / "templates"
        for _ in range(4):
            consumed += 1
            yield UnsortablePath()

    monkeypatch.setattr(WorkspacePaths, "files", files)
    monkeypatch.setattr(runner_module, "MAX_SEARCH_CANDIDATE_PATHS", 2)
    with pytest.raises(ToolError, match="лимит 2 файлов-кандидатов"):
        ToolRunner(workspace)._search_paths(float("inf"), "src/templates/*.html")
    assert consumed == 3

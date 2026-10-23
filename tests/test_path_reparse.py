from __future__ import annotations

import importlib
import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from koddi.policy.paths import PathAccessError, WorkspacePaths
from koddi.storage.db import init_db, set_setting
from koddi.tools import ToolRunner


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    storage = importlib.import_module("koddi.storage.db")
    monkeypatch.setenv("KODDI_DATA_DIR", str(tmp_path / "profile"))
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "profile" / "test.sqlite3")
    init_db()
    set_setting("permission_profile", "trusted")


def junction(link: Path, target: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        pytest.skip("junction creation is unavailable on this filesystem")
    assert link.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT


@pytest.mark.parametrize("directory", [False, True])
def test_lstat_attributes_block_reparse_points_without_new_pathlib_api(
    tmp_path, monkeypatch, directory
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "linked"
    if directory:
        target.mkdir()
    else:
        target.write_bytes(b"private")
    paths = WorkspacePaths(workspace)
    original = Path.lstat

    def reparse_info(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == target:
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return info

    monkeypatch.setattr(Path, "lstat", reparse_info)
    with pytest.raises(PathAccessError, match="reparse"):
        paths.resolve("linked/secret.txt" if directory else "linked")
    assert not paths.accessible(target)
    assert list(paths.files()) == []


@pytest.mark.parametrize("ignore_name", [".gitignore", ".ignore", ".koddiignore"])
def test_junction_cannot_bypass_ignore_rules(tmp_path, ignore_name):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    private = workspace / "private"
    private.mkdir()
    (workspace / ignore_name).write_text("private/\n", encoding="utf-8")
    (private / "secret.txt").write_bytes(b"private sentinel")
    (workspace / "public.txt").write_bytes(b"public")
    link = workspace / "alias"
    junction(link, private)
    paths = WorkspacePaths(workspace)
    # ссылки закрыты всегда, а обычная игнорируемая папка по прямому пути открыта
    for name in ("alias", "alias/secret.txt"):
        with pytest.raises(PathAccessError):
            paths.resolve(name)
        with pytest.raises(PathAccessError):
            paths.resolve(name, check_ignore=True)
    assert paths.resolve("private/secret.txt") == private / "secret.txt"
    with pytest.raises(PathAccessError):
        paths.resolve("private/secret.txt", check_ignore=True)
    assert not paths.accessible(link / "secret.txt")
    assert {path.relative_to(workspace).as_posix() for path in paths.files()} == {
        ignore_name,
        "public.txt",
    }


@pytest.mark.parametrize("destination", ["outside", "protected", "cycle"])
def test_real_junctions_are_pruned_before_traversal(tmp_path, destination):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    if destination == "outside":
        target = tmp_path / "private"
    elif destination == "protected":
        target = workspace / ".git"
    else:
        target = workspace
    target.mkdir(exist_ok=True)
    (target / "secret.txt").write_bytes(b"private sentinel")
    if destination == "cycle":
        (workspace / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
    junction(workspace / "alias", target)
    paths = WorkspacePaths(workspace)
    with pytest.raises(PathAccessError):
        paths.resolve("alias/secret.txt")
    with pytest.raises(PathAccessError):
        list(paths.files(workspace / "alias"))
    assert all(path.name != "secret.txt" for path in paths.files())


@pytest.mark.parametrize("location", ["root", "ancestor", "replaced_root"])
def test_workspace_root_reparse_is_rejected(tmp_path, location):
    original = tmp_path / "original"
    original.mkdir()
    (original / "workspace").mkdir()
    alias = tmp_path / "alias"
    if location == "replaced_root":
        alias.mkdir()
        paths = WorkspacePaths(alias)
        alias.rmdir()
        junction(alias, original)
        with pytest.raises(PathAccessError):
            paths.resolve("new.txt")
        assert not paths.accessible(alias / "new.txt")
    else:
        junction(alias, original)
        with pytest.raises(PathAccessError):
            WorkspacePaths(alias if location == "root" else alias / "workspace")


@pytest.mark.parametrize("ignore_name", [".gitignore", ".ignore", ".koddiignore"])
def test_linked_ignore_rules_fail_closed_without_reading_target(tmp_path, monkeypatch, ignore_name):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"private sentinel")
    junction(workspace / ignore_name, outside)
    original = Path.read_text

    def reject_private_read(path, *args, **kwargs):
        assert path.name != ignore_name
        assert not path.is_relative_to(outside)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_private_read)
    paths = WorkspacePaths(workspace)
    # прямой доступ не читает правила; обход с правилами закрывается целиком, если файл правил - ссылка
    assert paths.resolve("public.txt") == workspace / "public.txt"
    with pytest.raises(PathAccessError, match="ignore file"):
        paths.resolve("public.txt", check_ignore=True)
    assert not paths.accessible(workspace / "public.txt", ignore=True)
    assert list(paths.files()) == []


@pytest.mark.parametrize(
    "tool,args",
    [
        ("file.read", {"path": "alias/secret.txt"}),
        ("file.read_chunk", {"path": "alias/secret.txt", "offset": 0, "size": 64}),
        ("file.read_range", {"path": "alias/secret.txt", "start": 1, "end": 2}),
        ("file.metadata", {"path": "alias/secret.txt"}),
        ("directory.list", {"path": "alias"}),
        ("project.instructions", {"path": "alias/secret.txt"}),
        ("file.write_patch", {"path": "alias/secret.txt", "content": "overwritten"}),
        ("file.create", {"path": "alias/new.txt", "content": "created"}),
        ("file.delete", {"path": "alias/secret.txt"}),
    ],
)
def test_direct_tools_never_expose_or_mutate_junction_secrets(tmp_path, tool, args):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    private = workspace / "private"
    private.mkdir()
    (workspace / ".gitignore").write_text("private/\n", encoding="utf-8")
    secret = private / "secret.txt"
    secret.write_bytes(b"private sentinel")
    junction(workspace / "alias", private)
    result = ToolRunner(workspace).run(tool, args)
    assert not result["ok"]
    assert "private sentinel" not in json.dumps(result)
    assert secret.read_bytes() == b"private sentinel"
    assert not (private / "new.txt").exists()


@pytest.mark.parametrize(
    "tool,args",
    [
        ("search.text", {"query": "sentinel"}),
        ("search.files", {"query": "*secret*"}),
        ("directory.tree", {"path": "."}),
    ],
)
def test_recursive_tools_do_not_disclose_junction_or_protected_files(tmp_path, tool, args):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    private = workspace / "private"
    private.mkdir()
    (workspace / ".gitignore").write_text("private/\n", encoding="utf-8")
    for name in ("secret.txt", ".env", "credentials.json"):
        (private / name).write_bytes(b"private sentinel")
    # файлы вроде .env в проекте открыты: поиск и дерево их показывают, закрыты только ссылки и игнорируемые папки при обходе
    (workspace / ".env").write_bytes(b"open env")
    junction(workspace / "alias", private)
    result = ToolRunner(workspace).run(tool, args)
    assert result["ok"], result
    serialized = json.dumps(result)
    assert "private sentinel" not in serialized
    # дерево ничего не прячет по .gitignore (всё в проекте открыто), поиск обход с правилами делает
    if tool != "directory.tree":
        assert "secret.txt" not in serialized
    assert '"alias"' not in serialized


def test_direct_tools_reach_everything_inside_the_project(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True)
    (workspace / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "pkg.js").write_text("module", encoding="utf-8")
    (workspace / ".env").write_text("MODE=dev", encoding="utf-8")
    (workspace / "README.md").write_text("readme", encoding="utf-8")
    (workspace / ".gitignore").write_text("README.md\nnode_modules/\n", encoding="utf-8")
    runner = ToolRunner(workspace)
    for name, expected in ((".git/config", "[core]"), ("node_modules/pkg.js", "module"), (".env", "MODE=dev"), ("README.md", "readme"), (".gitignore", "README.md")):
        result = runner.run("file.read", {"path": name})
        assert result["ok"], (name, result)
        assert expected in json.dumps(result)
    # агент сам правит .gitignore
    edit = runner.run("file.write_patch", {"path": ".gitignore", "content": "dist/\n"})
    assert edit["ok"], edit
    assert (workspace / ".gitignore").read_text(encoding="utf-8").strip() == "dist/"


def test_regular_paths_and_nested_ignore_rules_stay_compatible(tmp_path):
    workspace = tmp_path / "workspace"
    nested = workspace / "nested"
    nested.mkdir(parents=True)
    (workspace / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (nested / ".koddiignore").write_text("hidden.txt\n", encoding="utf-8")
    (nested / "hidden.txt").write_bytes(b"hidden")
    (nested / "trace.log").write_bytes(b"log")
    (nested / "public.txt").write_bytes(b"public")
    paths = WorkspacePaths(workspace)
    assert paths.resolve("nested/public.txt") == nested / "public.txt"
    assert paths.resolve("nested/new.txt") == nested / "new.txt"
    # обход уважает правила, а прямой путь к скрытым файлам открыт
    assert not paths.accessible(nested / "hidden.txt", ignore=True)
    assert not paths.accessible(nested / "trace.log", ignore=True)
    assert paths.accessible(nested / "hidden.txt") and paths.accessible(nested / "trace.log")
    assert nested / "public.txt" in set(paths.files())
    assert nested / "hidden.txt" not in set(paths.files())

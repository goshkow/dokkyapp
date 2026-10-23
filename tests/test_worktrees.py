from pathlib import Path
import subprocess

from koddi.projects import add_project, create_worktree, remove_worktree


def _git(path: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", *args], cwd=path, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr


def test_create_and_remove_worktree(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "readme.txt").write_text("test", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "init")
    project = add_project(str(root))
    result = create_worktree(project["id"], str(tmp_path / "feature"), "koddi-test-branch")
    assert result["ok"] is True
    assert (tmp_path / "feature" / "readme.txt").exists()
    removed = remove_worktree(result["worktree"]["id"])
    assert removed["ok"] is True
    assert not (tmp_path / "feature").exists()

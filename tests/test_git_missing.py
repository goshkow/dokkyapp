"""Нет git или папка без git: понятный результат инструмента, а не сбой шага."""

from __future__ import annotations

import subprocess

from koddi.tools import ToolRunner


def test_git_not_installed_is_a_plain_result(tmp_path, monkeypatch):
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", missing)
    result = ToolRunner(tmp_path).git_command(["git", "status"])
    assert result["ok"] is False and result["git_missing"] and "Git is not installed" in result["error"]


def test_folder_without_repository_is_explained(tmp_path):
    result = ToolRunner(tmp_path).git_command(["git", "status"])
    assert result["ok"] is False and result.get("not_a_repository"), result

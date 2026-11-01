from pathlib import Path

import pytest

from koddi.agents.mentions import mentioned_files, mentioned_paths


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    import koddi.storage.db as storage

    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "mentions.sqlite3")
    storage.init_db()


def test_mentions_are_found_at_word_starts_only():
    text = "Почини @src/app.py и @README.md. Почта user@example.com не путь, @src/app.py повтор"
    assert mentioned_paths(text) == ["src/app.py", "README.md"]
    assert mentioned_paths("@" + " @".join(f"f{index}.py" for index in range(9))) == [f"f{index}.py" for index in range(5)]


def test_mentioned_project_files_reach_the_agent(tmp_path: Path):
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("print('привет')\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("чужое", encoding="utf-8")
    text = mentioned_files("смотри @src/app.py, @../secret.txt и @нет.py", project, None)
    assert "### src/app.py" in text and "print('привет')" in text
    # файлы вне проекта и несуществующие не подставляются
    assert "чужое" not in text and "нет.py" not in text
    assert mentioned_files("без упоминаний", project, None) == ""
    assert mentioned_files("@src/app.py", None, None) == ""


def test_long_file_is_cut_with_a_note(tmp_path: Path):
    (tmp_path / "big.txt").write_text("я" * 30000, encoding="utf-8")
    text = mentioned_files("@big.txt", tmp_path, None)
    assert "показано начало файла" in text
    assert len(text) < 25000

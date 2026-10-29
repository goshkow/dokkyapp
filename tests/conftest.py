"""Изоляция тестов.

pytest грузит модуль раньше, чем любой тест импортирует Koddi. Папка данных во временном
каталоге не даёт тестам задеть настоящий профиль пользователя.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="koddi-pytest-"))
os.environ["KODDI_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["KODDI_PORTABLE"] = "1"
os.environ["KODDI_INTERNAL_TEST"] = "1"


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _home_folder(monkeypatch, tmp_path_factory):
    """Чат без проекта создаёт папку «Докки» в документах: в тестах она во временном каталоге."""
    from koddi.projects import service

    folder = tmp_path_factory.mktemp("home") / "Documents" / service.HOME_NAME
    monkeypatch.setattr(service, "home_directory", lambda: folder)
    return folder

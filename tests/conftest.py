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

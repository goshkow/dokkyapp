"""Проверки вставки в общей браузерной сборке macOS/Windows."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from smoke_pasted_text import SCENARIOS  # noqa: E402


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.__name__)
def test_pasted_text(scenario):
    scenario()

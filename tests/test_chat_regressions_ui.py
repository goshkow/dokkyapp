"""Регрессии полей ввода в общей сборке macOS и Windows."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from smoke_chat_regressions import SCENARIOS  # noqa: E402


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.__name__)
def test_chat_regression(scenario):
    scenario()

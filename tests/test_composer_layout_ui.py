"""Регрессии геометрии ввода в общей сборке macOS/Windows."""
import sys
from pathlib import Path

import pytest

pytest.importorskip('playwright.sync_api')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from smoke_composer_layout import SCENARIOS  # noqa: E402


@pytest.mark.parametrize('scenario', SCENARIOS, ids=lambda item: item.__name__)
def test_composer_layout(scenario):
    scenario()

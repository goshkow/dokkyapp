"""Новый интерфейс (React) в настоящем браузере на подставном API: основные сценарии разработки, чата Докки и поиска."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "backend/koddi/desktop/web-next/index.html"

pytest.importorskip("playwright.sync_api")

if not BUILD.is_file():  # pragma: no cover - сборка делается командой `npm run build` в frontend/
    pytest.skip("Интерфейс не собран: выполните `npm run build` в frontend/", allow_module_level=True)

sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("smoke_next", ROOT / "scripts/smoke_next.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


@pytest.mark.parametrize("scenario", smoke.SCENARIOS, ids=lambda item: item.__name__)
def test_scenario(scenario):
    try:
        scenario()
    except Exception as error:  # noqa: BLE001
        if "Executable doesn't exist" in str(error):
            pytest.skip("Браузер Playwright не установлен")
        raise

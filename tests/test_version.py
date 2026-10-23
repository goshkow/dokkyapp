"""Версия одна на всё: pyproject.toml. Удалённое управление и сборки берут её отсюда."""

import tomllib
from pathlib import Path

import koddi

ROOT = Path(__file__).resolve().parents[1]


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)["project"]["version"]


def test_package_version_matches_pyproject():
    assert koddi.__version__ == project_version()


def test_build_scripts_do_not_hardcode_version():
    """Скрипты сборки читают версию из pyproject.toml, а не держат свою копию."""
    version = project_version()
    for script in ("scripts/build_macos.sh", "scripts/build_nuitka.ps1"):
        text = (ROOT / script).read_text(encoding="utf-8")
        assert "pyproject.toml" in text, script
        assert f"version={version}" not in text and f"version={version}.0" not in text, script

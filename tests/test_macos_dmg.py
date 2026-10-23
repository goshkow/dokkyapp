"""Упаковка не должна писать внутрь исходной .app или заменять готовые образы."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "build_macos_dmg", Path(__file__).resolve().parents[1] / "scripts/build_macos_dmg.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def symlink(link: Path, target: Path, directory: bool = False) -> None:
    # в Windows без режима разработчика ссылки создавать нельзя, сами проверки нужны на macOS
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        pytest.skip(f"символические ссылки недоступны: {error}")


def test_output_parent_cannot_alias_source_bundle(tmp_path):
    bundle = tmp_path / "Докки.app"
    bundle.mkdir()
    alias = tmp_path / "output"
    symlink(alias, bundle, directory=True)
    with pytest.raises(ValueError, match="вне папки приложения"):
        MODULE.resolve_output(alias / "Докки.dmg", bundle)
    assert list(bundle.iterdir()) == []


def test_output_parent_dotdot_cannot_enter_source_bundle(tmp_path):
    bundle = tmp_path / "Докки.app"
    bundle.mkdir()
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(ValueError, match="вне папки приложения"):
        MODULE.resolve_output(staging / ".." / "Докки.app" / "Докки.dmg", bundle)
    assert list(bundle.iterdir()) == []


@pytest.mark.parametrize("existing", ["Докки.dmg", "Докки.json"])
def test_existing_artifacts_are_not_replaced(tmp_path, existing):
    original = tmp_path / existing
    original.write_bytes(b"previous build")
    with pytest.raises(ValueError, match="уже существует"):
        MODULE.resolve_output(tmp_path / "Докки.dmg", tmp_path / "Докки.app")
    assert original.read_bytes() == b"previous build"


def test_dangling_destination_link_is_not_followed(tmp_path):
    destination = tmp_path / "Докки.dmg"
    symlink(destination, tmp_path / "missing")
    with pytest.raises(ValueError, match="уже существует"):
        MODULE.resolve_output(destination, tmp_path / "Докки.app")
    assert destination.is_symlink()

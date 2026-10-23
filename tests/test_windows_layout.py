"""Раскладка Windows для автообновления: ярлыки, автозапуск и koddi:// ведут на лаунчер Dokky.exe."""

import json
import os
import sys

import pytest

from koddi import config
from koddi.desktop import autostart

pytestmark = pytest.mark.skipif(os.name != "nt", reason="раскладка лаунчера только в Windows")


def make_layout(root, version="0.2.0", launcher=True, current=True):
    app = root / f"app-{version}"
    app.mkdir(parents=True)
    (app / "DokkyApp.exe").write_bytes(b"MZ")
    if launcher:
        (root / "Dokky.exe").write_bytes(b"MZ")
    if current:
        (root / "current.json").write_text(json.dumps({"v": 1, "version": version, "dir": app.name, "previous": None}))
    return app


@pytest.fixture
def packaged(monkeypatch):
    monkeypatch.setattr(config, "is_packaged_build", lambda: True)

    def place(app):
        monkeypatch.setattr(config, "app_dir", lambda: app)
    return place


def test_launcher_found_above_version_dir(tmp_path, packaged):
    packaged(make_layout(tmp_path))
    assert config.launcher_path() == tmp_path / "Dokky.exe"


@pytest.mark.parametrize("version", ["0.3.0-beta.2", "10.0.1"])
def test_launcher_accepts_release_and_beta_dirs(tmp_path, packaged, version):
    packaged(make_layout(tmp_path, version))
    assert config.launcher_path() == tmp_path / "Dokky.exe"


@pytest.mark.parametrize("launcher, current", [(False, True), (True, False)])
def test_no_launcher_without_full_layout(tmp_path, packaged, launcher, current):
    packaged(make_layout(tmp_path, launcher=launcher, current=current))
    assert config.launcher_path() is None


def test_old_flat_layout_has_no_launcher(tmp_path, packaged):
    """Прежняя раскладка без app-X: всё указывает на сам DokkyApp.exe, как раньше."""
    (tmp_path / "Dokky.exe").write_bytes(b"MZ")
    (tmp_path / "current.json").write_text("{}")
    packaged(tmp_path)
    assert config.launcher_path() is None


@pytest.mark.parametrize("name", ["app-0.2", "app-0.2.0.tmp", "app-0.2.0-rc1", "app"])
def test_unexpected_version_dir_is_ignored(tmp_path, packaged, name):
    app = tmp_path / name
    app.mkdir()
    (tmp_path / "Dokky.exe").write_bytes(b"MZ")
    (tmp_path / "current.json").write_text("{}")
    packaged(app)
    assert config.launcher_path() is None


def test_source_checkout_has_no_launcher(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "is_packaged_build", lambda: False)
    monkeypatch.setattr(config, "app_dir", lambda: make_layout(tmp_path))
    assert config.launcher_path() is None


def test_autostart_points_to_launcher(tmp_path, packaged, monkeypatch):
    app = make_layout(tmp_path)
    packaged(app)
    monkeypatch.setattr(sys, "argv", [str(app / "DokkyApp.exe")])
    assert autostart._executable() == tmp_path / "Dokky.exe"

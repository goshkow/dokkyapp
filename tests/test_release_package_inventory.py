import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "release_package_inventory", Path(__file__).parents[1] / "scripts/verify_release_candidate.py"
)
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


@pytest.fixture
def package(tmp_path):
    directory = tmp_path / "package"
    directory.mkdir()
    (directory / "DokkyApp.exe").write_bytes(b"test binary")
    manifest = {}
    for source, target in inventory.ASSET_ROOTS.items():
        path = directory / target / "asset.txt"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"asset")
        manifest[source + "asset.txt"] = hashlib.sha256(b"asset").hexdigest()
    sources = tmp_path / "sources.json"
    sources.write_text(json.dumps(manifest), encoding="utf-8")
    return directory, sources


def test_package_matches_sources(package):
    report = inventory.verify(*package)
    assert report["ok"] and report["assets_verified"] == len(inventory.ASSET_ROOTS)
    assert report["file_count"] == len(inventory.ASSET_ROOTS) + 1


@pytest.mark.parametrize("change", ["missing", "modified", "unexpected", "profile", "traversal"])
def test_rejects_inconsistent_or_private_package(package, change):
    directory, sources = package
    asset = directory / "prompts/asset.txt"
    if change == "missing":
        asset.unlink()
    elif change == "modified":
        asset.write_bytes(b"different")
    elif change == "unexpected":
        (directory / "prompts/extra.txt").write_bytes(b"extra")
    elif change == "profile":
        (directory / "koddi.sqlite3").write_bytes(b"private")
    else:
        data = json.loads(sources.read_text())
        data["prompts/../../outside"] = "a" * 64
        sources.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        inventory.verify(directory, sources)


def test_public_ca_bundle_is_not_private_key(package):
    directory, sources = package
    cert = directory / "certifi/cacert.pem"
    cert.parent.mkdir()
    cert.write_bytes(b"public certificates")
    assert inventory.verify(directory, sources)["file_count"] == len(inventory.ASSET_ROOTS) + 2
    (directory / "private.pem").write_bytes(b"private key")
    with pytest.raises(ValueError):
        inventory.verify(directory, sources)


def test_browser_bundle_does_not_copy_runtime_logs(tmp_path, monkeypatch):
    import playwright

    script = importlib.util.spec_from_file_location(
        "browser_bundle", Path(__file__).parents[1] / "scripts/bundle_browser.py"
    )
    bundle = importlib.util.module_from_spec(script)
    script.loader.exec_module(bundle)
    root = tmp_path / "project"
    release = root / "dist"
    release.mkdir(parents=True)
    (release / "DokkyApp.exe").write_bytes(b"test")
    installed = tmp_path / "playwright"
    cache = installed / "driver/package/.local-browsers/chromium_headless_shell-test"
    cache.mkdir(parents=True)
    (cache / "chrome.exe").write_bytes(b"browser")
    (cache / "debug.log").write_bytes(b"runtime data")
    monkeypatch.setattr(playwright, "__file__", str(installed / "__init__.py"))
    monkeypatch.setattr(bundle, "__file__", str(root / "scripts/bundle_browser.py"))
    monkeypatch.setattr(sys, "argv", ["bundle_browser.py", str(release)])
    bundle.main()
    output = release / "playwright/driver/package/.local-browsers/chromium_headless_shell-test"
    assert (output / "chrome.exe").read_bytes() == b"browser"
    assert not (output / "debug.log").exists()


def test_optional_computer_helpers_and_license_notices_are_verified(package):
    directory, sources = package
    manifest = json.loads(sources.read_text())
    for source, target in inventory.OPTIONAL_ASSET_ROOTS.items():
        path = directory / target / 'payload.txt'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'notice or native source')
        manifest[source + 'payload.txt'] = hashlib.sha256(path.read_bytes()).hexdigest()
    sources.write_text(json.dumps(manifest))
    assert inventory.verify(directory, sources)['assets_verified'] == len(inventory.ASSET_ROOTS) + 2
    (directory / 'koddi/licenses/payload.txt').write_bytes(b'modified notice')
    with pytest.raises(ValueError):
        inventory.verify(directory, sources)

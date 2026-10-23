import json
import sys
import re
import sqlite3

import pytest

from koddi.installer import service


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["override", "portable", "user"])
async def test_diagnostics_reports_profile_mode_without_paths_or_settings(tmp_path, monkeypatch, mode):
    monkeypatch.delenv("KODDI_DATA_DIR", raising=False)
    monkeypatch.delenv("KODDI_PORTABLE", raising=False)
    if mode == "override":
        monkeypatch.setenv("KODDI_DATA_DIR", "C:/private-user/secret-profile")
        monkeypatch.setenv("KODDI_PORTABLE", "1")
    elif mode == "portable":
        monkeypatch.setenv("KODDI_PORTABLE", "1")
    profile = tmp_path / "private-user-secret-profile"
    profile.mkdir()
    (profile / "koddi.sqlite3").touch()
    monkeypatch.setattr(service, "data_dir", lambda: profile)
    monkeypatch.setattr(service, "is_packaged_build", lambda: False)
    monkeypatch.setattr(service, "remote_config", lambda: {
        "base_url": "https://private-user:password@internal.example/?token=private-token",
        "api_key": "sk-never-expose-this", "model": "private-model",
    })
    result = await service.diagnostics()
    assert result["ok"] and result["ai_configured"]
    assert result["profile"] == {"mode": mode, "available": True, "database_present": True}
    assert re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", result["build_number"])
    mac = sys.platform == "darwin"
    assert result["caption_rendering"] == ("macos-native" if mac else "windows-caption-glyph")
    assert result["native_dwm_caption"] is False
    assert ("macos_traffic_lights_and_fullscreen" if mac else "windows_10_11_caption_and_snap") in result["external_gates"]
    encoded = json.dumps(result)
    for sensitive in (str(profile), "private-user", "password", "private-token", "internal.example", "private-model", "sk-never-expose-this"):
        assert sensitive not in encoded


@pytest.mark.asyncio
async def test_diagnostics_errors_do_not_disclose_exception_or_profile(monkeypatch):
    def denied():
        raise PermissionError("C:/private-user/profile token=secret")

    def corrupt():
        raise sqlite3.DatabaseError("private database password=secret")

    monkeypatch.setattr(service, "data_dir", denied)
    monkeypatch.setattr(service, "remote_config", corrupt)
    result = await service.diagnostics()
    assert result["ok"] is False
    assert result["profile"]["available"] is False
    assert result["ai_configured"] is None
    assert "secret" not in json.dumps(result)
    assert "private" not in json.dumps(result)


def test_update_channel_is_honestly_disabled_without_architecture_claim():
    result = service.update_check()
    assert result["status"] == "disabled"
    assert result["reason"] == "unconfigured"
    assert "architecture_ready" not in result
    assert set(result["external_gates"]) == {"pinned_update_key", "release_https_url", "publisher_certificate"}

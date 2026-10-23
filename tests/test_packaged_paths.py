from koddi import config


def test_packaged_resources_are_resolved_inside_exe_directory(tmp_path, monkeypatch):
    executable = tmp_path / "release" / "DokkyApp.exe"
    monkeypatch.setattr(config.sys, "executable", str(executable))
    monkeypatch.setattr(config.sys, "frozen", True, raising=False)
    assert config.project_root() == executable.parent
    assert config.app_dir() == executable.parent


def test_development_resources_keep_repository_root(monkeypatch):
    monkeypatch.setattr(config.sys, "executable", "C:/Python/python.exe")
    monkeypatch.setattr(config.sys, "frozen", False, raising=False)
    assert config.project_root().joinpath("prompts").is_dir()


def test_explicit_profile_never_imports_neighboring_usage_data(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "koddi.sqlite3").write_bytes(b"old-profile")
    target = tmp_path / "clean"
    monkeypatch.setenv("KODDI_DATA_DIR", str(target))
    monkeypatch.setattr(config, "legacy_data_dirs", lambda: [legacy])
    assert config.migrate_legacy_data() == []
    assert not (target / "koddi.sqlite3").exists()

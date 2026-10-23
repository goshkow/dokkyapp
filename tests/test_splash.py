import json

from koddi.desktop import splash


def test_load_defaults_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("KODDI_DATA_DIR", str(tmp_path))
    assert splash.load() == splash.DEFAULT


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(splash, "_file", lambda: tmp_path / "startup.json")
    splash.save("light", True, (1.0, 0.5, 0.25))
    assert splash.load() == {"theme": "light", "glass": True, "color": [1.0, 0.5, 0.25]}


def test_load_ignores_broken_file(tmp_path, monkeypatch):
    path = tmp_path / "startup.json"
    monkeypatch.setattr(splash, "_file", lambda: path)
    path.write_text("{oops", encoding="utf-8")
    assert splash.load() == splash.DEFAULT
    path.write_text(json.dumps({"theme": "neon", "glass": True, "color": [0, 0, 0]}), encoding="utf-8")
    assert splash.load() == splash.DEFAULT


def test_save_rejects_bad_theme(tmp_path, monkeypatch):
    path = tmp_path / "startup.json"
    monkeypatch.setattr(splash, "_file", lambda: path)
    splash.save("neon", True, (0, 0, 0))
    assert not path.exists()


def test_mark_exists():
    assert splash.mark_path().is_file()

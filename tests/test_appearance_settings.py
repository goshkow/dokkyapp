import pytest
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.api import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "appearance.sqlite3")
    return TestClient(create_app())


def test_appearance_preferences_persist(client):
    preferences = {
        "ui_motion": "reduced",
        "chat_width": "wide",
        "text_size": 18,
        "code_font": "Cascadia Code",
        "enter_sends": False,
    }
    response = client.put("/api/settings", json=preferences)
    assert response.status_code == 200
    restored = client.get("/api/settings").json()
    assert all(restored[key] == value for key, value in preferences.items())


@pytest.mark.parametrize(
    "preferences",
    [
        {"text_size": 900},
        {"text_size": True},
        {"text_size": "18"},
        {"ui_motion": "broken"},
        {"chat_width": []},
        {"code_font": "url(evil)"},
        {"enter_sends": "false"},
    ],
)
def test_invalid_preferences_are_rejected_atomically(client, preferences):
    before = client.get("/api/settings").json()
    assert client.put("/api/settings", json={"theme": "light", **preferences}).status_code == 422
    assert client.get("/api/settings").json() == before

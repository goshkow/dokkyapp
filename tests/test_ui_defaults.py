from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.api import create_app

WEB = Path("backend/koddi/desktop/web")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "defaults.sqlite3")
    return TestClient(create_app())


def test_new_profile_uses_pro_and_active_cache(client):
    settings = client.get("/api/settings").json()
    assert settings["permission_profile"] == "ask"
    assert settings["coding_reasoning_mode"] == "pro"
    assert settings["coding_keepalive"] is True


def test_explicit_automatic_profile_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "legacy.sqlite3")
    storage.init_db()
    with storage.db() as conn:
        conn.execute("DELETE FROM settings WHERE key = 'defaults_version'")
    storage.set_setting("permission_profile", "trusted")
    storage.init_db()
    assert storage.get_setting("permission_profile") == "trusted"
    storage.set_setting("permission_profile", "trusted")
    storage.init_db()
    assert storage.get_setting("permission_profile") == "trusted"


def test_money_is_never_shown_or_kept():
    # деньги не показываются и не считаются: только токены и проценты остатка
    for path in WEB.glob("*.js"):
        source = path.read_text(encoding="utf-8")
        assert "charged_rub" not in source and "serverCharge" not in source, path.name
        assert "coding-usage" not in source and "₽" not in source, path.name
        assert "Оценка смены режима" not in source, path.name


def test_brand_is_dokky_with_code_badge():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "<title>Докки</title>" in html
    assert 'class="brand-badge"' in html
    for name in ("index.html", "app.js", "dokky.js", "product-ui.js"):
        assert "Кодди" not in (WEB / name).read_text(encoding="utf-8")


def test_search_keeps_chat_list_and_limits_are_in_account_menu():
    dokky = (WEB / "dokky.js").read_text(encoding="utf-8")
    assert 'value === "search" ? "chat" : value' in dokky
    product = (WEB / "product-ui.js").read_text(encoding="utf-8")
    assert "Оставшийся лимит" in product
    assert "cloudPath('subscription')" in product


def test_header_center_and_overlay_edges_follow_original_chat():
    css = (WEB / "app.css").read_text(encoding="utf-8")
    assert "grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr)" in css
    assert ".chat-edge-fade-top" in css and ".chat-edge-fade-bottom" in css
    host = Path("backend/koddi/desktop/web_host.py").read_text(encoding="utf-8")
    assert "ScrollAnimatorEnabled" in host


def test_stream_renders_words_and_finishes_in_place():
    stream = (WEB / "stream-markdown.js").read_text(encoding="utf-8")
    assert "stream-word" in stream and "settleCloudStream" in stream
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "continueStreamedMessage" in app
    css = (WEB / "app.css").read_text(encoding="utf-8")
    assert ".streaming {\n  white-space: pre-wrap;" not in css


def test_chat_and_search_follow_the_site_layout():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for name in ("cloud-search.css", "site-chat.css", "cloud-search.js", "site-chat.js"):
        assert (WEB / name).is_file() and name in html
    assert 'class="ai-product-switch"' in html
    search = (WEB / "cloud-search.js").read_text(encoding="utf-8")
    for contract in ("Что хотите найти?", "Проверим источники и найдём ответ.", "search.source", "ai-search-followup"):
        assert contract in search
    site = (WEB / "site-chat.js").read_text(encoding="utf-8")
    assert "welcomeLines" in site and "Режим Докки" in site
    # палитра сайта
    css = (WEB / "site-chat.css").read_text(encoding="utf-8")
    assert "--panel: #292929" in css and "--user: #244b75" in css


def test_live_text_is_routed_into_the_journal_without_jumps():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    journal = (WEB / "local-journal.js").read_text(encoding="utf-8")
    assert "flushStreamBuffers" in app and "journalRowFor" in app and "orphanBodies" in app
    assert "provisional" in journal and "takeStreamBody" in journal
    # время работы стоит слева от стрелки, секундомер тикает сам
    assert "setInterval" in journal and 'class="journal-duration"></span>${icon("chevron"' in journal

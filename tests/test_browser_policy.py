from __future__ import annotations

import pytest

from koddi.policy.engine import decide
from koddi.browser import service


@pytest.fixture
def profile(monkeypatch):
    def set_profile(value):
        monkeypatch.setattr("koddi.policy.grants.permission_profile", lambda project_id=None: value)

    return set_profile


@pytest.fixture
def local_session(monkeypatch):
    sessions = [{"session_id": 1, "local": True, "project_id": 1, "url": "http://127.0.0.1:3000/", "title": ""}]
    monkeypatch.setattr(service, "live_sessions", lambda: sessions)
    monkeypatch.setattr(service, "session_info", lambda sid: next((s for s in sessions if s["session_id"] == sid), None))
    return sessions


def test_passive_tools_always_allowed(profile, tmp_path):
    profile("read-only")
    for name in ("browser.snapshot", "browser.console", "browser.network", "browser.dom", "browser.screenshot"):
        assert decide(name, {}, tmp_path).action == "allow"


def test_open_local_is_allowed_and_public_follows_profile(profile, tmp_path):
    profile("ask")
    assert decide("browser.open", {"url": "http://localhost:5173"}, tmp_path).action == "allow"
    assert decide("browser.open", {"url": "index.html"}, tmp_path).action == "allow"
    assert decide("browser.open", {"url": "file:///x/index.html"}, tmp_path).action == "allow"
    assert decide("browser.open", {"url": "https://example.com"}, tmp_path).action == "ask"
    assert decide("browser.open", {"url": "http://localhost.evil.com"}, tmp_path).action == "ask"
    profile("trusted")
    assert decide("browser.open", {"url": "https://example.com"}, tmp_path).action == "review"
    profile("full")
    assert decide("browser.open", {"url": "https://example.com"}, tmp_path).action == "allow"
    profile("read-only")
    assert decide("browser.open", {"url": "http://localhost:5173"}, tmp_path).action == "block"
    profile("ask")
    assert decide("browser.open", {"url": "http://localhost:5173"}, None).action == "block"


def test_actions_on_local_page_allowed_public_ask(profile, tmp_path, local_session):
    profile("ask")
    for name in ("browser.click", "browser.type", "browser.eval", "browser.press"):
        assert decide(name, {"session_id": 1}, tmp_path).action == "allow"
        assert decide(name, {}, tmp_path).action == "allow"
    assert decide("browser.navigate", {"action": "reload"}, tmp_path).action == "allow"
    assert decide("browser.navigate", {"url": "http://127.0.0.1:3000/a"}, tmp_path).action == "allow"
    assert decide("browser.navigate", {"url": "https://example.com"}, tmp_path).action == "ask"
    local_session[0]["local"] = False
    assert decide("browser.click", {"session_id": 1}, tmp_path).action == "ask"
    profile("read-only")
    assert decide("browser.click", {"session_id": 1}, tmp_path).action == "block"


def test_unknown_browser_tool_blocked(profile, tmp_path):
    profile("trusted")
    assert decide("browser.nope", {}, tmp_path).action == "block"


def test_loopback_helper():
    for url in ("http://localhost:3000", "http://127.0.0.1", "http://[::1]:8000", "http://app.localhost/", "ws://localhost/x"):
        assert service.is_loopback_url(url)
    for url in ("http://localhost.evil.com", "http://10.0.0.1", "https://example.com", "file:///etc/hosts", "http://127.0.0.1.evil.com"):
        assert not service.is_loopback_url(url)


def test_runner_refuses_localhost_without_devserver(tmp_path, monkeypatch):
    import koddi.tools.runner as runner_module

    monkeypatch.setattr(service, "local_preview_allowed", lambda *a: False)
    monkeypatch.setattr(runner_module, "local_preview_allowed", lambda *a: False)
    runner = runner_module.ToolRunner(workspace=tmp_path)
    with pytest.raises(runner_module.ToolError, match="devserver"):
        runner._dispatch("browser.open", {"url": "http://localhost:3000"})
    with pytest.raises(runner_module.ToolError):
        runner._dispatch("browser.open", {"url": "../outside.html"})


def test_schemas_validate_browser_args():
    from koddi.tools.schemas import tool_schemas_for_mode

    tools = {t["function"]["name"]: t["function"]["parameters"] for t in tool_schemas_for_mode("development")}
    assert tools["browser.open"]["required"] == ["url"]
    assert tools["browser.type"]["required"] == ["text"]
    assert tools["browser.click"]["required"] == []
    assert all(t["additionalProperties"] is False for name, t in tools.items() if name.startswith("browser."))
    assert "browser.snapshot" in {t["function"]["name"] for t in tool_schemas_for_mode("debug")}

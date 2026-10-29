from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from mcp.types import Tool

from koddi.integrations import mcp as module


@pytest.mark.asyncio
async def test_mcp_discovery_preserves_real_schemas_and_pagination(monkeypatch):
    class Session:
        async def list_tools(self, cursor=None):
            return SimpleNamespace(
                tools=[
                    Tool(
                        name="first" if cursor is None else "second",
                        description="Tool",
                        inputSchema={
                            "type": "object",
                            "properties": {"value": {"type": "integer"}},
                            "required": ["value"],
                        },
                    )
                ],
                nextCursor="next" if cursor is None else None,
            )

    @asynccontextmanager
    async def session(config):
        yield Session()

    monkeypatch.setattr(module, "_load_servers", lambda: {"test": {"url": "https://mcp.example"}})
    monkeypatch.setattr(module, "_session", session)
    result = await module.mcp_tools_list_async()
    assert result["ok"]
    assert {tool["name"] for tool in result["tools"]} == {"first", "second"}
    assert result["tools"][0]["inputSchema"]["required"] == ["value"]


@pytest.mark.asyncio
async def test_mcp_failure_never_returns_fake_empty_success(monkeypatch):
    @asynccontextmanager
    async def session(config):
        raise RuntimeError("server unavailable")
        yield

    monkeypatch.setattr(module, "_load_servers", lambda: {"test": {}})
    monkeypatch.setattr(module, "_session", session)
    result = await module.mcp_tools_list_async()
    assert result["ok"] is False
    assert result["server_statuses"][0]["status"] == "error"


@pytest.mark.asyncio
async def test_mcp_tools_list_without_config_is_empty_not_error(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "mcp_config_path", lambda: tmp_path / "missing.json")
    result = await module.mcp_tools_list_async()
    assert result["ok"] is True
    assert result["tools"] == [] and result["servers"] == []
    assert "error" not in result

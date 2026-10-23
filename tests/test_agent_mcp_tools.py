from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from mcp.types import Tool

from koddi.agents import mcp_tools
from koddi.integrations import mcp


@pytest.mark.asyncio
async def test_agent_receives_exact_schema_and_stable_unique_names(tmp_path, monkeypatch):
    config = tmp_path / "servers.json"
    config.touch()
    monkeypatch.setattr(mcp_tools, "mcp_config_path", lambda: config)
    schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}

    async def discover():
        return {
            "tools": [
                {"server": server, "name": "lookup", "inputSchema": schema}
                for server in ("a.b", "a_b")
            ],
            "errors": [],
        }

    monkeypatch.setattr(mcp_tools, "mcp_tools_list_async", discover)
    tools, bindings, errors = await mcp_tools.discover_agent_tools()
    assert not errors
    assert len(bindings) == 2
    assert all(t["function"]["parameters"] == schema for t in tools)
    assert set(bindings.values()) == {("a.b", "lookup"), ("a_b", "lookup")}
    assert (await mcp_tools.discover_agent_tools())[1] == bindings


@pytest.mark.asyncio
async def test_agent_skips_invalid_mcp_schema(tmp_path, monkeypatch):
    config = tmp_path / "servers.json"
    config.touch()
    monkeypatch.setattr(mcp_tools, "mcp_config_path", lambda: config)

    async def discover():
        return {
            "tools": [{"server": "broken", "name": "bad", "inputSchema": {"type": "not-a-type"}}]
        }

    monkeypatch.setattr(mcp_tools, "mcp_tools_list_async", discover)
    tools, bindings, errors = await mcp_tools.discover_agent_tools()
    assert not tools and not bindings and errors[0]["server"] == "broken"


@pytest.mark.asyncio
async def test_mcp_rejects_invalid_arguments_before_external_action(monkeypatch):
    called = []

    class Session:
        async def list_tools(self, cursor=None):
            return SimpleNamespace(
                tools=[
                    Tool(
                        name="lookup",
                        inputSchema={
                            "type": "object",
                            "properties": {"value": {"type": "integer"}},
                            "required": ["value"],
                        },
                    )
                ],
                nextCursor=None,
            )

        async def call_tool(self, name, args):
            called.append((name, args))

    @asynccontextmanager
    async def session(config):
        yield Session()

    monkeypatch.setattr(mcp, "_load_servers", lambda: {"test": {}})
    monkeypatch.setattr(mcp, "_session", session)
    result = await mcp.mcp_tool_call("test", "lookup", {"value": "not-an-integer"})
    assert result["ok"] is False
    assert not called

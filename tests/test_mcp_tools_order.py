import asyncio

from koddi.agents import mcp_tools


def test_mcp_schemas_are_sorted_whatever_order_servers_answer(monkeypatch):
    # от порядка схем зависит отпечаток, по которому сервер делит кэш между чатами
    tools = [{"server": s, "name": n, "inputSchema": {"type": "object"}} for s, n in (("b", "x"), ("a", "y"), ("c", "z"))]

    async def listing(order):
        return {"tools": order, "errors": []}

    monkeypatch.setattr(mcp_tools, "plugin_servers", lambda: [1])
    names = []
    for order in (tools, list(reversed(tools))):
        monkeypatch.setattr(mcp_tools, "mcp_tools_list_async", lambda order=order: listing(order))
        schemas, _, _ = asyncio.run(mcp_tools.discover_agent_tools())
        names.append([item["function"]["name"] for item in schemas])
    assert names[0] == names[1] == sorted(names[0])
